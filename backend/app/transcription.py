"""Local speech recognition: model lifecycle, word-level decoding and streaming commit logic.

Three layers, each testable on its own:

* ``Transcriber``           decodes one audio window into timed words (faster-whisper, or a test fake).
* ``TranscriptionService``  owns the model lifecycle (loading -> ready | unavailable | error) with an
                            accurate, actionable status. It loads once, in the background, and can be retried.
* ``StreamingTranscriber``  turns a growing audio buffer into interim text and final sentences using
                            LocalAgreement-style commits, so text appears while the person is still speaking
                            and overlapping windows never repeat a line.

Nothing here invents text: if the model is missing or failing, the status says exactly why and what to run.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from .audio_ingestion import SAMPLE_RATE, SILENCE_RMS, AudioWindow, assess_quality, metrics

log = logging.getLogger("trinetra.asr")

SETUP_CMD = "npm run setup:asr"


@dataclass
class Word:
    text: str
    start: float  # seconds
    end: float
    prob: float = 1.0


class Transcriber(Protocol):
    name: str
    available: bool

    def status(self) -> dict: ...
    def transcribe_words(self, window: AudioWindow) -> list[Word]: ...


class TranscriptionUnavailable(RuntimeError):
    """Raised when audio is sent to a model that is not loaded."""


class TranscriptionLoadError(RuntimeError):
    """Model could not be made ready. `code` is machine-readable; `action` tells the user what to do."""

    def __init__(self, code: str, detail: str, action: str = "", cause: str = ""):
        super().__init__(detail)
        self.code, self.detail, self.action, self.cause = code, detail, action, cause


# ------------------------------------------------------------------------------------------------
# Backends
# ------------------------------------------------------------------------------------------------
class UnavailableTranscriber:
    """Placeholder used when transcription is switched off. Never produces text."""

    name = "none"
    available = False

    def __init__(self, reason: str = "No local speech-recognition model installed.", code: str = "unavailable"):
        self.reason, self.code = reason, code

    def status(self) -> dict:
        return {"state": "unavailable", "name": self.name, "code": self.code, "detail": self.reason, "cause": "",
                "action": f"Use a scripted scenario or the manual transcript box, or run `{SETUP_CMD}`."}

    def transcribe_words(self, window: AudioWindow) -> list[Word]:
        raise TranscriptionUnavailable(self.reason)


class FasterWhisperTranscriber:
    available = True

    def __init__(self, model, model_size: str):
        self.model = model
        self.name = f"faster-whisper:{model_size}"

    def status(self) -> dict:
        return {"state": "ready", "name": self.name, "code": "ready", "action": "", "cause": "",
                "detail": "Local model, English only. Audio stays on this machine."}

    def transcribe_words(self, window: AudioWindow) -> list[Word]:
        segments, _info = self.model.transcribe(
            window.samples.astype(np.float32), language="en", beam_size=1, word_timestamps=True,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 350}, condition_on_previous_text=False,
        )
        words: list[Word] = []
        for seg in segments:  # generator: decoding happens here
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append(Word(text, float(w.start), float(w.end), float(getattr(w, "probability", 1.0))))
        return words


def model_cache_dir() -> str | None:
    return os.environ.get("TRINETRA_MODEL_DIR") or None


def load_faster_whisper(report: Callable[[str], None] | None = None) -> FasterWhisperTranscriber:
    """Load faster-whisper on CPU. Uses the local cache first (so restarts never re-download), and only
    then downloads. Every failure becomes a TranscriptionLoadError with a precise code and next step."""
    size = os.environ.get("TRINETRA_WHISPER_MODEL", "base.en")
    device = os.environ.get("TRINETRA_WHISPER_DEVICE", "cpu")
    offline = os.environ.get("TRINETRA_OFFLINE") == "1"
    root = model_cache_dir()
    try:
        from faster_whisper import WhisperModel  # optional dependency
    except ImportError as exc:
        raise TranscriptionLoadError(
            "not_installed", "The local speech-recognition package (faster-whisper) is not installed in the backend environment.",
            f"Run `{SETUP_CMD}`, then press Retry (or restart the backend).", f"{type(exc).__name__}: {exc}") from exc

    threads = int(os.environ.get("TRINETRA_WHISPER_THREADS", max(2, (os.cpu_count() or 4) // 2)))

    def build(local_only: bool):
        return WhisperModel(size, device=device, compute_type="int8", download_root=root, local_files_only=local_only, cpu_threads=threads)

    try:
        model = build(True)
        log.info("speech model %s loaded from local cache (device=%s)", size, device)
        return FasterWhisperTranscriber(model, size)
    except Exception as local_exc:  # noqa: BLE001 - not cached yet (or unreadable)
        log.info("speech model %s not in local cache (%s: %s)", size, type(local_exc).__name__, str(local_exc)[:120])
        if offline:
            raise TranscriptionLoadError(
                "model_missing_offline", f"The speech model '{size}' is not downloaded and TRINETRA_OFFLINE=1 forbids downloading it.",
                f"Run `{SETUP_CMD}` once while online.", f"{type(local_exc).__name__}: {local_exc}") from local_exc
    if report:
        report(f"Downloading the speech model '{size}' (first run only, about 150 MB)…")
    log.info("downloading speech model %s", size)
    try:
        model = build(False)
    except Exception as exc:  # noqa: BLE001
        log.error("speech model download/load failed: %s", exc, exc_info=True)
        raise TranscriptionLoadError(
            "download_failed", f"The speech model '{size}' could not be downloaded or loaded: {str(exc)[:200]}",
            f"Check your internet connection and press Retry, or run `{SETUP_CMD}`.", f"{type(exc).__name__}: {exc}") from exc
    log.info("speech model %s downloaded and loaded", size)
    return FasterWhisperTranscriber(model, size)


# ------------------------------------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------------------------------------
class TranscriptionService:
    """Owns the model. State is always accurate: idle -> loading -> ready | unavailable | error.

    It satisfies the Transcriber protocol (``available`` is live), so a call that starts while the model is
    still loading begins transcribing as soon as it is ready."""

    def __init__(self, loader: Callable[[Callable[[str], None]], Transcriber] | None = None):
        self._loader = loader or load_faster_whisper
        self._lock = threading.Lock()
        self._impl: Transcriber | None = None
        self._thread: threading.Thread | None = None
        self.state = "idle"
        self.code = "idle"
        self.detail = "Speech model not loaded yet."
        self.action = ""
        self.cause = ""
        self.name = "none"
        self.loaded_in = 0.0

    # ---- construction helpers --------------------------------------------------------------
    @classmethod
    def static(cls, transcriber: Transcriber) -> "TranscriptionService":
        """Wrap an already-built transcriber (tests, or transcription disabled)."""
        svc = cls(loader=lambda report: transcriber)
        if transcriber.available:
            svc._impl, svc.state, svc.code, svc.name = transcriber, "ready", "ready", transcriber.name
            svc.detail = transcriber.status().get("detail", "")
        else:
            st = transcriber.status()
            svc.state, svc.code, svc.detail, svc.action = "unavailable", st.get("code", "unavailable"), st.get("detail", ""), st.get("action", "")
        return svc

    @classmethod
    def from_env(cls) -> "TranscriptionService":
        if os.environ.get("TRINETRA_TRANSCRIBER", "auto") == "none":
            return cls.static(UnavailableTranscriber("Transcription is disabled (TRINETRA_TRANSCRIBER=none).", "disabled"))
        return cls()

    # ---- state -----------------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.state == "ready" and self._impl is not None

    def _set(self, **kw: Any) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "name": self.name, "code": self.code, "detail": self.detail,
                "action": self.action, "cause": self.cause}

    # ---- loading ---------------------------------------------------------------------------
    def start(self, blocking: bool = False) -> None:
        """Begin loading in the background (no-op if already loading or ready)."""
        with self._lock:
            if self.state in ("loading", "ready"):
                return
            self.state, self.code, self.detail, self.action, self.cause = "loading", "loading", "Loading the local speech model…", "", ""
            self._thread = threading.Thread(target=self._load, name="asr-loader", daemon=True)
            self._thread.start()
        if blocking:
            self._thread.join()

    def retry(self) -> dict[str, Any]:
        with self._lock:
            if self.state in ("error", "unavailable", "idle"):
                self.state = "idle"
        self.start()
        return self.status()

    def _load(self) -> None:
        t0 = time.time()
        try:
            impl = self._loader(lambda msg: self._set(detail=msg))
        except TranscriptionLoadError as exc:
            log.warning("transcription unavailable (%s): %s | cause: %s", exc.code, exc.detail, exc.cause)
            state = "unavailable" if exc.code in ("not_installed", "disabled") else "error"
            self._set(state=state, code=exc.code, detail=exc.detail, action=exc.action, cause=exc.cause[:300])
            return
        except Exception as exc:  # noqa: BLE001 - never let a loader crash the thread silently
            log.error("speech model failed to load", exc_info=True)
            self._set(state="error", code="load_failed", detail=f"The speech model failed to load: {str(exc)[:200]}",
                      action="Press Retry. If it keeps failing, check the backend log.", cause=f"{type(exc).__name__}: {exc}")
            return
        if not getattr(impl, "available", False):
            st = impl.status()
            self._set(state="unavailable", code=st.get("code", "unavailable"), detail=st.get("detail", ""), action=st.get("action", ""))
            return
        self._impl = impl
        self._set(state="ready", code="ready", detail=impl.status().get("detail", ""), action="", cause="", name=impl.name,
                  loaded_in=round(time.time() - t0, 1))
        log.info("transcription ready: %s (%.1fs)", impl.name, time.time() - t0)

    # ---- decoding --------------------------------------------------------------------------
    def transcribe_words(self, window: AudioWindow) -> list[Word]:
        impl = self._impl
        if self.state != "ready" or impl is None:
            raise TranscriptionUnavailable(self.detail)
        return impl.transcribe_words(window)


# ------------------------------------------------------------------------------------------------
# Streaming
# ------------------------------------------------------------------------------------------------
@dataclass
class Final:
    text: str
    t: float  # audio time (s) of the last word


@dataclass
class StepResult:
    partial: str = ""
    finals: list[Final] = field(default_factory=list)
    decoded: bool = False
    silent: bool = False
    decode_ms: int = 0
    buffer_s: float = 0.0


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", w.lower())


class StreamingTranscriber:
    """Streaming speech-to-text on top of a word-level decoder.

    Every ``hop_s`` of new audio the whole *uncommitted* buffer is decoded. Words are committed once two
    consecutive decodes agree on them (LocalAgreement-2), then that audio is trimmed from the buffer so
    work stays bounded and nothing is ever emitted twice. Words not yet agreed are exposed as an interim
    ``partial``. Committed words become a *final* line at a sentence end, after a pause, or at the end of
    the call. This keeps latency to a couple of seconds without repeating text from overlapping windows.
    """

    def __init__(self, asr: Transcriber, hop_s: float = 1.0, min_s: float = 1.0, max_buffer_s: float = 10.0,
                 keep_s: float = 0.25, pause_s: float = 1.6, max_pending_words: int = 40, sample_rate: int = SAMPLE_RATE):
        self.asr = asr
        self.hop_s, self.min_s, self.max_buffer_s, self.keep_s = hop_s, min_s, max_buffer_s, keep_s
        self.pause_s, self.max_pending_words, self.sr = pause_s, max_pending_words, sample_rate
        self.reset()

    # ---- state -----------------------------------------------------------------------------
    def reset(self) -> None:
        self.audio = np.zeros(0, dtype=np.float32)
        self.buf_start = 0.0  # audio time of audio[0]
        self.total = 0.0  # audio time at the end of the buffer
        self._since = 0.0
        self.committed_end = 0.0
        self.prev_tail: list[Word] = []
        self.pending: list[Word] = []
        self.decodes = 0

    @property
    def buffer_s(self) -> float:
        return len(self.audio) / self.sr

    def append(self, samples: np.ndarray) -> None:
        self.audio = np.concatenate([self.audio, samples.astype(np.float32, copy=False)])
        sec = len(samples) / self.sr
        self.total += sec
        self._since += sec

    def due(self) -> bool:
        return self._since >= self.hop_s and self.buffer_s >= self.min_s

    # ---- helpers ---------------------------------------------------------------------------
    def _trim_to(self, t: float) -> None:
        cut = int(max(0.0, t - self.buf_start) * self.sr)
        if cut > 0:
            self.audio = self.audio[cut:]
            self.buf_start += cut / self.sr

    def _decode(self) -> list[Word]:
        win = AudioWindow(self.audio.copy(), self.sr, self.buf_start)
        words = [Word(w.text, w.start + self.buf_start, w.end + self.buf_start, w.prob) for w in self.asr.transcribe_words(win)]
        # drop anything that belongs to audio we already committed (the small kept margin can re-decode it)
        return [w for w in words if w.start >= self.committed_end - 0.1 and w.end > self.committed_end + 0.02]

    def _emit_pending(self, *, force: bool) -> list[Final]:
        """Turn committed words into sentences. Incomplete sentences wait unless `force`."""
        finals: list[Final] = []
        sentence: list[Word] = []
        rest: list[Word] = []
        for i, w in enumerate(self.pending):
            sentence.append(w)
            if re.search(r"[.!?][\"')\]]*$", w.text):
                finals.append(Final(" ".join(x.text for x in sentence), round(sentence[-1].end, 2)))
                sentence = []
        rest = sentence
        if rest and (force or len(rest) >= self.max_pending_words):
            finals.append(Final(" ".join(x.text for x in rest), round(rest[-1].end, 2)))
            rest = []
        self.pending = rest
        return finals

    # ---- main step -------------------------------------------------------------------------
    def step(self) -> StepResult:
        """Decode the buffer. Raises whatever the decoder raises (the caller reports it)."""
        self._since = 0.0
        res = StepResult(buffer_s=self.buffer_s)
        win = AudioWindow(self.audio, self.sr, self.buf_start)
        if assess_quality(win) is not None:  # silent or too short: nothing audible to decode
            res.silent = True
            res.finals = self._emit_pending(force=True) if self.total - self.committed_end >= self.pause_s else []
            if res.finals or not self.pending:
                self._trim_to(self.total)
                self.committed_end = max(self.committed_end, self.total)
                self.prev_tail = []
            return res
        t0 = time.perf_counter()
        words = self._decode()
        res.decoded = True
        res.decode_ms = int((time.perf_counter() - t0) * 1000)
        self.decodes += 1

        # LocalAgreement-2: the longest common prefix of the previous and the current decode is stable.
        n = 0
        while n < min(len(self.prev_tail), len(words)) and _norm(self.prev_tail[n].text) == _norm(words[n].text):
            n += 1
        # Safety valve: a very long uncommitted stretch is committed, keeping the last ~1.5 s for context.
        if self.buffer_s > self.max_buffer_s and n < len(words):
            limit = self.total - 1.5
            n = max(n, sum(1 for w in words if w.end <= limit))
        if n:
            committed = words[:n]
            self.pending.extend(committed)
            self.committed_end = committed[-1].end
            self._trim_to(self.committed_end - self.keep_s)
            words = words[n:]
        self.prev_tail = words
        res.finals = self._emit_pending(force=False)
        # A pause after the last committed word ends the utterance even without punctuation.
        if self.pending and not words and self.total - self.committed_end >= self.pause_s:
            res.finals += self._emit_pending(force=True)
        res.partial = " ".join(w.text for w in (self.pending + words)).strip() if (self.pending or words) else ""
        return res

    def flush(self) -> StepResult:
        """End of call: decode what is left and finalise everything. Raises if the decoder raises."""
        res = StepResult(buffer_s=self.buffer_s)
        audible = self.buffer_s >= 0.4 and metrics(self.audio)["rms"] >= SILENCE_RMS
        if audible:
            t0 = time.perf_counter()
            words = self._decode()
            res.decoded, res.decode_ms = True, int((time.perf_counter() - t0) * 1000)
            self.pending.extend(words)
            if words:
                self.committed_end = words[-1].end
        res.finals = self._emit_pending(force=True)
        self.audio = np.zeros(0, dtype=np.float32)
        self.buf_start = self.total
        self.prev_tail = []
        return res


# ------------------------------------------------------------------------------------------------
# Compatibility + CLI
# ------------------------------------------------------------------------------------------------
def make_transcriber() -> TranscriptionService:
    """Kept for callers that want a ready-to-use service: built from the environment, loading in the background."""
    svc = TranscriptionService.from_env()
    svc.start()
    return svc


def _selftest() -> int:
    """`python -m app.transcription` : load the model and transcribe a bundled spoken clip. Used by setup."""
    from .scenarios import AUDIO_DIR, read_wav

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    svc = TranscriptionService.from_env()
    svc.start(blocking=True)
    st = svc.status()
    print(f"state={st['state']} code={st['code']}")
    if st["state"] != "ready":
        print(st["detail"])
        if st.get("action"):
            print("Next step:", st["action"])
        if st.get("cause"):
            print("Cause:", st["cause"])
        return 1
    clip = Path(AUDIO_DIR) / "synthetic_relative_scam.wav"
    samples = read_wav(clip)[: 5 * SAMPLE_RATE]
    t0 = time.time()
    words = svc.transcribe_words(AudioWindow(samples, SAMPLE_RATE, 0.0))
    text = " ".join(w.text for w in words)
    print(f"model={st['name']} loaded_in={svc.loaded_in}s decoded 5s of audio in {time.time() - t0:.1f}s")
    print("transcript:", text)
    ok = "grandma" in text.lower()
    print("SELFTEST", "PASSED" if ok else "FAILED (unexpected transcript)")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(_selftest())
