import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TranscriptPanel } from "../components/Panels";
import { initialState, reducer } from "../lib/store";
import type { ServerMsg, TranscriptionDiag, TranscriptionStatus } from "../types";

const diag = (o: Partial<TranscriptionDiag> = {}): TranscriptionDiag => ({
  frames: 80, audio_s: 8, rms: 0.05, quiet_s: 0, decodes: 3, decode_ms: 640, buffer_s: 2.4, finals: 2, windows_skipped: 0, invalid_chunks: 0, ...o,
});
const ready: TranscriptionStatus = { state: "ready", name: "faster-whisper:base.en", detail: "Local model", diag: diag() };
const line = (id: string, text: string, source: "real" | "scripted" = "real") =>
  ({ id, t: 12, speaker: "caller" as const, text, source, method: "faster-whisper:base.en", session: "abc123" });

const panel = (over: Partial<Parameters<typeof TranscriptPanel>[0]> = {}) =>
  render(<TranscriptPanel lines={[]} findings={[]} asr={ready} partial="" live status="running" canSend onRetry={() => undefined} {...over} />);

describe("transcript panel: real recognition", () => {
  it("shows recognised lines labelled as real, with the model name", () => {
    panel({ lines: [line("l1", "Please tell me the OTP you received.")] });
    expect(screen.getByText("Please tell me the OTP you received.")).toBeInTheDocument();
    expect(screen.getByTestId("transcript-line")).toHaveAttribute("data-source", "real");
    expect(screen.getAllByText("faster-whisper:base.en").length).toBeGreaterThan(0);
    expect(screen.getByTestId("transcription-status")).toHaveAttribute("data-state", "ready");
  });

  it("shows interim text while speech is still being recognised", () => {
    panel({ partial: "please tell me your" });
    expect(screen.getByTestId("transcript-partial")).toHaveTextContent("please tell me your");
    expect(screen.getByText("Hearing")).toBeInTheDocument();
  });

  it("shows Transcribing… instead of Unavailable while a decode is running", () => {
    panel({ asr: { ...ready, state: "transcribing" } });
    expect(screen.getByTestId("transcription-status")).toHaveTextContent(/transcribing/i);
    expect(screen.getByTestId("transcription-status")).not.toHaveTextContent(/unavailable/i);
  });

  it("shows live diagnostics (audio received, level, decode time)", () => {
    panel();
    expect(screen.getByTestId("asr-diag")).toHaveTextContent(/audio 8\.0 s/);
    expect(screen.getByTestId("asr-diag")).toHaveTextContent(/last decode 640 ms/);
  });

  it("scripted lines keep their scripted label", () => {
    panel({ lines: [line("l1", "Hello there.", "scripted")], live: false });
    expect(screen.getByTestId("transcript-line")).toHaveAttribute("data-source", "scripted");
  });
});

describe("transcript panel: actionable failures", () => {
  it("not installed: states what to run and offers Retry", () => {
    const onRetry = vi.fn();
    panel({ asr: { state: "unavailable", name: "none", code: "not_installed", detail: "The local speech-recognition package is not installed.", action: "Run `npm run setup:asr`, then press Retry.", cause: "ModuleNotFoundError: faster_whisper" }, onRetry });
    const n = screen.getByTestId("asr-notice");
    expect(n).toHaveTextContent("not installed");
    expect(n).toHaveTextContent("npm run setup:asr");
    expect(n).toHaveTextContent("ModuleNotFoundError");
    fireEvent.click(screen.getByText("Retry"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("model error: shows the cause", () => {
    panel({ asr: { state: "error", name: "none", detail: "The speech model could not be downloaded: name resolution failed", action: "Check your internet connection and press Retry.", cause: "ConnectionError" } });
    expect(screen.getByTestId("asr-notice")).toHaveTextContent(/could not be downloaded/);
    expect(screen.getByTestId("transcription-status")).toHaveAttribute("data-state", "error");
  });

  it("loading: says so and does not offer Retry", () => {
    panel({ asr: { state: "loading", name: "none", detail: "Downloading the speech model (first run only)…" } });
    expect(screen.getByTestId("asr-notice")).toHaveTextContent(/Downloading the speech model/);
    expect(screen.queryByText("Retry")).toBeNull();
  });

  it("silence is reported as silence, not as a model failure", () => {
    panel({ asr: { ...ready, diag: diag({ quiet_s: 6, audio_s: 9, decodes: 0 }) } });
    expect(screen.getByTestId("asr-notice")).toHaveTextContent(/No speech detected/);
    expect(screen.getByTestId("asr-notice")).toHaveTextContent(/not a model failure/);
  });

  it("audio arriving but nothing recognised is surfaced as a diagnostic", () => {
    panel({ asr: { ...ready, diag: diag({ audio_s: 12, decodes: 0, quiet_s: 0 }) } });
    expect(screen.getByTestId("asr-notice")).toHaveTextContent(/arriving.*nothing has been recognised/i);
  });

  it("no notice when everything is healthy", () => {
    panel();
    expect(screen.queryByTestId("asr-notice")).toBeNull();
  });
});

describe("session store: transcription events", () => {
  const msg = (s: typeof initialState, m: ServerMsg) => reducer(s, { type: "msg", msg: m, now: 1 });

  it("applies transcription_status and interim text, and keeps final lines with their session id", () => {
    let s = msg(initialState, { type: "call_state", state: "running", mode: "live-mic", t: 0, info: {}, capabilities: { detectors: {} as never, transcription: { state: "loading", name: "none", detail: "Loading…" } } });
    expect(s.transcription?.state).toBe("loading");
    s = msg(s, { type: "transcription_status", state: "transcribing", name: "faster-whisper:base.en", detail: "Transcribing…", diag: diag() });
    expect(s.transcription?.state).toBe("transcribing");
    s = msg(s, { type: "transcript_partial", text: "please tell me", t: 3, session: "abc123" });
    expect(s.partial).toBe("please tell me");
    s = msg(s, { type: "transcript", line: line("l1", "Please tell me your OTP.") });
    expect(s.transcript[0].session).toBe("abc123");
    expect(s.transcript[0].source).toBe("real");
  });

  it("a new call clears the interim text and old transcription state", () => {
    let s = msg(initialState, { type: "transcript_partial", text: "stale", t: 1, session: "x" });
    s = msg(s, { type: "call_state", state: "running", mode: "live-mic", t: 0, info: {} });
    expect(s.partial).toBe("");
  });
});
