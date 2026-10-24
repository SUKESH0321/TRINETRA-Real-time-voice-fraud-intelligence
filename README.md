# TRINETRA — Real-time voice fraud intelligence

> Detect suspicious voice signals and scam tactics while the conversation is happening.

TRINETRA is a browser-based hackathon prototype. It watches a call as it unfolds and warns the user, with a sound, a vibration where supported, and a visible card, when the evidence suggests a possible scam. It reports **three independent signals** and never treats any one of them as proof:

| Signal | Question | How it is produced here |
|---|---|---|
| **Synthetic speech indicators** | Does the audio look like synthetic speech? | Optional pretrained AASIST model, otherwise *unavailable* (or *simulated* by a demo scenario, clearly labelled) |
| **Voice manipulation indicators** | Is there evidence of splicing or editing? | *Unavailable* by default; an opt-in **experimental** heuristic; or *simulated* by a scenario |
| **Scam conversation** | Does the caller ask for OTPs, money, remote access, use threats, impersonation, secrecy? | Rule-based phrase and context analysis (not a trained model), always on |

A synthetic voice is not automatically a scammer, and a human voice can be used for fraud. The risk engine keeps the three signals apart and a strong scam signal can raise a warning with both voice detectors unavailable.

**What this is not:** it does not intercept ordinary cellular or VoIP calls, it is not a native mobile app, and it cannot interrupt the phone's own call screen. It analyses audio that is *inside the browser*: a scripted demo call, your microphone, or the audio of an in-app WebRTC call between two tabs/devices.

---

## Quick start

Requirements: **Node.js 18+** (tested with 24) and either **[uv](https://docs.astral.sh/uv/)** or **Python 3.11–3.12**. No API keys, no accounts, no paid services.

```bash
npm run setup      # one time: Python venv + backend deps + frontend deps
npm run dev        # starts backend (:8000) and frontend (:5173)
```

Open **http://localhost:5173**, click **Enable alerts**, then **Start demo call** on any scenario.

Other commands:

| Command | What it does |
|---|---|
| `npm run setup:asr` | **Live speech recognition**: installs faster-whisper, downloads the `base.en` model (~150 MB, once) and runs a real transcription self-test. CPU only |
| `npm run setup:aasist` | Optional synthetic-voice detector: CPU PyTorch + the AASIST reference model (~250 MB). Independent of `setup:asr` |
| `npm run setup:ml` | Both of the above |
| `npm run check:asr` | Loads the speech model and transcribes a bundled spoken clip; prints exactly what is wrong if it cannot |
| `npm start` | Builds the frontend and serves everything from one process on http://localhost:8000 |
| `npm run dev:https` | Dev server with a self-signed certificate, so a phone on your Wi-Fi may use its microphone |
| `npm test` | Backend (pytest) + frontend (vitest) tests |
| `npm run check:scenarios` | Runs all demo scenarios instantly and compares them with their expected outcomes |

No `npm`? The backend alone is `backend/.venv/Scripts/python -m uvicorn app.main:app --app-dir backend` (macOS/Linux: `.venv/bin/python`).

### Live transcription (`npm run setup:asr`)

Microphone and in-app-call transcripts come from **faster-whisper** running locally on the CPU (no GPU, no cloud, audio never leaves the machine).

```bash
npm run setup:asr     # once, needs internet for the first model download
npm run dev           # the backend loads the model in the background; the UI follows its state
```

* The default model is `base.en` (set `TRINETRA_WHISPER_MODEL` for another size). After the first download it is loaded from the local cache, so restarts never need the network. `TRINETRA_MODEL_DIR` chooses the cache folder; `TRINETRA_OFFLINE=1` forbids downloads.
* The UI always shows the model's real state: **loading** (also while it downloads), **ready**, **transcribing**, **unavailable** or **error**, with the cause and the exact next step. If you install the model while the backend is already running, press **Retry** in the app (no restart needed).
* Speech is transcribed as you talk: interim text appears after about 1 to 2 s ("Hearing…"), and a line becomes final (and is passed to the scam analysis) once two consecutive decodes agree, roughly 3 to 5 s after the sentence ends on a typical laptop CPU. Fragments are never analysed, so a half-heard sentence cannot raise a false alarm.

### Synthetic-voice detector (`npm run setup:aasist`, optional)

**AASIST** (NAVER Corp., MIT licence) is cloned to `backend/models/aasist` (the weights are in that repository) and runs on CPU PyTorch. It is a research model trained on ASVspoof 2019. **In this repo's own tests it is noisy**: it flags untouched TTS clips in some windows and not others, so TRINETRA requires agreement across several windows and labels its limits. Treat it as a demonstration of the plumbing, not as a dependable detector.

Without these the app still works fully; live transcription and the synthetic detector simply show **Unavailable** (with the command to fix it).

---

## Demo guide (5 minutes)

1. Open the app, click **Enable alerts** (browsers need a click before they will play sound). Use **Test High alert** / **Test Critical alert** in *Alert settings* to check volume. Optionally set **Playback speed** to 2× or 4×.
2. Run the scenarios below with **Start demo call**. Each one is deterministic: the same timeline plays every time, and **Replay scenario** repeats it exactly.
3. While a call runs, point out: the **three independent signals**, the **source badges** (Real analysis / Scripted / Simulated / Unavailable), the **evidence timeline**, and the **warning card**.

| # | Scenario | What to look for | Expected |
|---|---|---|---|
| 1 | **Legitimate conversation** | Friends chatting, a normal request to move a meeting | Low. No alert at all |
| 2 | **Human caller, OTP request** | "This is Rahul from the bank's fraud prevention department" … at **00:18** "Please read the code you just received so I can secure your account." | **High** warning + one ping + vibration at 00:18. Voice authenticity: *Inconclusive*. The repeated request at 00:28 does **not** ping again (anti-spam) |
| 3 | **Synthetic voice, harmless content** | Appointment reminder read by a TTS voice (real audio clip, plays in the page) | Synthetic indicators appear (**Simulated** badge, or **Real analysis** if AASIST is installed) → **Elevated**, silent. No scam finding, no ping, no fraud verdict |
| 4 | **Synthetic voice, family-emergency scam** | "Hi grandma, it's me…" TTS audio, urgent money, secrecy, "don't call my old number" | Voice and conversation reported separately. Elevated (voice only) → **High** ping at the money request → **Critical** ping at 00:24 |
| 5 | **Suspicious phrase, safe context** | "Never share your OTP with anyone." A bank awareness message | Low. The phrase is advice, not a request, so no warning |
| 6 | **Manipulated / edited audio** | Delivery message whose audio is two recordings joined at ~00:11 (constructed for the demo) | Manipulation indicator shown on its own (**Simulated**) → **Elevated**, silent. Nothing in the content is suspicious, so no fraud verdict |
| Bonus | **Escalating bank scam** | OTP → remote access (AnyDesk) → "move your savings to the safe account" | High ping → Critical ping on escalation → Critical ping for the transfer instruction; repeats in between stay silent |

**Smart ping alerts**

* **Elevated:** subtle banner only. No sound.
* **High:** one two-note ping, one vibration (if supported), warning card with the reason, the transcript evidence (highlighted), detection time, conversation risk, voice authenticity and the recommended action.
* **Critical:** distinct urgent siren, vibration pattern, strong warning card.
* **Anti-spam:** repeated evidence is deduplicated; same-level evidence on an existing incident is rate-limited (45 s cooldown); only a genuinely worse situation (higher level, or score up by 0.15) escalates and pings. **Quiet mode** (only Critical makes sound), **Mute** (visual warnings stay), **Test alert**, and status chips for *Sound blocked* / *Vibration unsupported*.
* The card offers **I understand**, **View evidence** and **Mute alert sound**, and never says "fraud confirmed".

### Live modes (experimental)

* **Microphone analysis**: browser permission prompt, then your mic is analysed live. The page shows the measured input level, interim and final transcript text (labelled *Real*, with the model name), and a diagnostics line (audio received, level, lines recognised, last decode time). A silent microphone is reported as silence, not as a model failure. Without the speech model you can still type what the caller says into the transcript box (labelled *Typed in*); the scam analysis is then real.
* **In-app call (two tabs or devices)**: open the app twice, same room code. One tab joins as **Callee** (it analyses what it hears), the other is the **Caller** and sends a bundled TTS clip or its microphone. Media flows peer to peer; the backend only relays signaling. Use headphones if both tabs use microphones. There is no STUN/TURN server, so it works on localhost and the same network.

Phones need HTTPS to use the microphone: run `npm run dev:https` and open `https://<your-computer-ip>:5173` (accept the self-signed certificate warning).

---

## Real, scripted, simulated, unavailable

Every transcript line, timeline entry and indicator carries a source badge.

| Badge | Meaning |
|---|---|
| **Real analysis** | Computed in this session from actual audio or text by the code (scam rules, risk engine, alert manager, audio metrics, AASIST/Whisper if installed, the experimental splice check if enabled) |
| **Scripted** | A transcript line from a demo scenario script. The scam analysis *of* that line is real |
| **Simulated** | Voice evidence **injected by a scenario**. It is not a detector output and is labelled "simulated by scenario" in the timeline |
| **Typed in** | Text you entered in the manual transcript box |
| **Unavailable** | No detector installed for that signal. No number is shown |

The audio clips are **genuinely synthetic**: generated locally with the Windows built-in voices (SAPI). No third-party recordings are bundled, so the human-voice scenarios (1, 2, 5, bonus) are transcript-only. The "manipulated" clip is a constructed demo sample (two TTS segments with different background noise, joined). You can regenerate the clips on Windows with `backend/.venv/Scripts/python backend/tools/make_demo_audio.py`.

---

## Architecture

```
frontend/  React + TypeScript + Vite + Tailwind
  src/lib/alerts.ts      sound (Web Audio), vibration, honest status (blocked / unsupported)
  src/lib/media.ts       getUserMedia, AudioWorklet -> 16 kHz PCM16, permission-error mapping
  src/lib/webrtc.ts      two-party call, signaling over /ws/signal/{room}
  src/lib/useSession.ts  WebSocket session, auto-reconnect      src/lib/store.ts  reducer
  src/components/        Launcher, CallScreen, WarningCard, Panels (risk, indicators, transcript, timeline), AlertSettings
backend/   Python + FastAPI + WebSockets
  app/audio_ingestion.py  validate/buffer audio (invalid, empty, wrong rate, silent, too short)
  app/voice_analysis.py   detect_synthetic_speech(), detect_voice_manipulation(), detector registry
  app/aasist_detector.py  optional pretrained synthetic-speech model
  app/transcription.py    ASR: model lifecycle (loading/ready/unavailable/error), word-level decoding, streaming commit logic
  app/fraud_analysis.py   rule-based scam analysis (negation, safe context, evidence spans)
  app/risk_engine.py      separate scores -> overall level, reasons, uncertainty, action
  app/alert_manager.py    cooldown, dedupe, escalation, quiet mode
  app/session.py          one call: every input takes the same path (scenario or live)
  app/scenarios.py        deterministic replay of app/scenario_data/*.json
  app/websocket_manager.py  socket sender + WebRTC signaling rooms
  config/risk_rules.yaml  ALL thresholds, weights, cooldowns, recommended actions
```

**Message flow.** Browser → `/ws/session`: JSON controls (`start_scenario`, `start_live`, `manual_transcript`, `settings`, `stop`) and binary PCM16 audio frames. Server → browser: `call_state`, `transcript`, `finding`, `voice`, `risk`, `alert`, `timeline`, `audio_metrics`, `error`, each with a sequence number. REST: `/api/health`, `/api/scenarios`, `/api/scenarios/{id}/audio`, `/api/rules`.

**Risk engine** (`config/risk_rules.yaml`). Scam evidence is kept per category and decays with call time (half-life 120 s). The score is the strongest category plus 0.35 × the strength of each *other* distinct category: repeats do not stack. Levels: Elevated ≥ 0.30, High ≥ 0.55, Critical ≥ 0.80. Voice signals alone are capped at Elevated (`voice_only_max_level`); they can optionally raise a scam warning one level (`corroboration_bumps_level`, off by default). Reasons, uncertainty notes and a recommended action are returned with every assessment. Edit the YAML and restart to tune.

**Scam analysis.** English phrase patterns for: OTP/PIN/password/CVV requests, remote-access installs, "safe account" transfers, urgent money requests, gift-card/crypto payments, impersonation, threats and pressure, avoiding official verification, secrecy. Only the *caller's* speech is judged. "Never share your OTP", "scammers may ask for your PIN" and "your OTP is 482913" are recognised as safe context.

---

## Tests

```bash
npm test
```

* **Backend (pytest, 160 tests):** scam phrases and negation, risk thresholds, voice/scam separation, alert cooldown/escalation/dedupe/quiet mode, scenario expectations and replay determinism, WebSocket delivery, WebRTC signaling rooms, PCM16 decoding and silence handling, detector smoothing, the bundled audio clips, and the transcription stack: model loading (cached, download, offline, missing package, failure and retry), streaming commits with overlapping decodes (no repeated text, interim text, pause and end-of-call flushing, bounded buffers), decoder failures, session isolation, batching, transcript-to-scam-analysis-to-alert integration, correct `real`/`scripted`/`manual` labels, the health/retry endpoints and a live WebSocket stream that flushes on stop. The speech model is mocked there; one extra test runs the **real** model on a bundled spoken clip when it is installed (it prints `[REAL MODEL ...]`) and is skipped otherwise.
* **Frontend (vitest, 49 tests):** audio blocked by browser policy, unsupported vibration, mute/quiet behaviour, microphone permission denial, insecure context, reducer behaviour, warning-card content, transcription states (ready, transcribing, loading, unavailable, error, silence, stalled), interim and final transcript rendering, and the real AudioWorklet source run against 48, 44.1 and 32 kHz input (output length, frequency, amplitude, signed little-endian PCM16, clamping, attenuation above 8 kHz).
* **Browser end-to-end** (manual, or scripted through the in-app browser during development): start each scenario at 4× speed and compare the timeline with the table above.

---

## Limitations

* **Not a phone interceptor.** Only audio inside the browser is analysed.
* **Scam detection is rules, not machine learning.** It is English only, will miss paraphrases, and can false-alarm on unusual wording. Indian-language and code-switched speech are *not* supported yet (the analyzer and transcription layers are pluggable so they can be added and tested later).
* **Synthetic-speech detection** is only as good as the optional AASIST model (trained on 2019 data, uncalibrated, weak on modern TTS and phone audio) and is *unavailable* otherwise. In the scenarios it is **simulated** unless you install it.
* **Voice-manipulation detection is not a real model.** The only built-in check (enable with `TRINETRA_EXPERIMENTAL_SPLICE=1`) compares the noise level of different pauses. It was only verified on this repo's demo clips, fires on noise gates/AGC/fans, and says nothing without two steady pauses. It cannot detect voice conversion or identify a speaker.
* **Live transcription** needs `npm run setup:asr`. Without it nothing is transcribed (the UI says why); use the typed transcript box. It is English only (`base.en`), a small CPU model that can mishear words, and finals lag the speech by a few seconds. Recognition and the AASIST detector compete for CPU on slow machines, so the voice detector skips windows when busy (the diagnostics line says so).
* **WebRTC** has no STUN/TURN: same machine or same network only.
* The warning card, sound and vibration are browser features. Vibration requires a supporting device (most desktops silently ignore it, shown as "Vibration unsupported" or "vibration sent" without effect).
* Tuning values (weights, thresholds) were set by hand against the demo scenarios, not validated on real call data. No accuracy figures are claimed.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot reach the TRINETRA backend" | Run `npm run dev` (needs `npm run setup` first). Port 8000 busy? Set `TRINETRA_PORT` (and `TRINETRA_BACKEND` for Vite) |
| `Backend virtualenv not found` | Run `npm run setup` |
| No sound | Click **Enable alerts**, then **Test High alert**. A *Sound blocked* chip means the browser refused: click the page and retry. Check mute, quiet mode and system volume |
| Vibration does nothing | Most desktop browsers do not vibrate. The chip says *unsupported* when the API is missing; Android Chrome supports it |
| Microphone: "permission denied" | Allow the mic in the browser's site settings, or use a scripted scenario |
| Microphone: "HTTPS or localhost" | Open http://localhost:5173, or use `npm run dev:https` for other devices |
| In-app call stuck on "Waiting for the other tab" | Both tabs need the same room code, one Callee and one Caller, and the same network. A third tab gets "room full" |
| Demo clip is silent | The browser blocked autoplay: click the page, then **Replay scenario** |
| `Library cublas64_12.dll is not found` | Whisper runs on CPU by default (`TRINETRA_WHISPER_DEVICE=cpu`); only set `cuda` if CUDA 12 libraries are installed |
| "Local transcription model is not installed" / Transcription unavailable | Run `npm run setup:asr`, then press **Retry** in the app (or restart the backend). The message in the app shows the exact cause |
| Transcription says **error** (download failed) | The first run needs internet to fetch the model. Connect, press **Retry** or re-run `npm run setup:asr`. Set `TRINETRA_MODEL_DIR` if the default cache folder is not writable |
| `setup:asr` fails installing faster-whisper | Usually an unsupported Python (3.14 may lack wheels). Delete `backend/.venv`, re-run `npm run setup` (uv installs Python 3.12), then `npm run setup:asr` |
| "No speech detected" | The microphone signal is silent: check the OS input device and level. This is not a model failure |
| "Audio is arriving but nothing has been recognised" | The model is slow or stuck: check the backend log and the diagnostics line (last decode time) |
| `setup:aasist` fails | AASIST is optional; the app keeps working with the synthetic detector shown as Unavailable |
| Changed scenarios or rules not applied | Restart the backend |

---

## Extending

* **Real detector:** implement `detect(window) -> VoiceResult` and pass it to `DetectorRegistry(synthetic=..., manipulation=...)`. Set `needs_smoothing = True` for noisy per-window models.
* **Another language:** add a `ScamAnalyzer` variant and an ASR backend that implements `Transcriber`; add its own tests before claiming support.
* **Other transport:** `CallSession` has no socket or clock dependencies; feed it transcript lines, voice results or audio chunks from any source.

Environment variables are listed in [`.env.example`](.env.example).
