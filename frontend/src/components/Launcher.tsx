import { useState, type ReactNode } from "react";
import type { AlertEngine, AlertStatus } from "../lib/alerts";
import { fmtClock } from "../lib/store";
import type { Role, RtcPhase } from "../lib/webrtc";
import type { Capabilities, Level, ScenarioInfo, TranscriptionStatus } from "../types";
import { CountUp, EyeMark, Marquee, Reveal, Scramble } from "./fx";
import { Btn, cx, LEVEL_LABEL, LEVEL_STYLE } from "./ui";

interface Props {
  engine: AlertEngine;
  status: AlertStatus;
  visualOnly: boolean;
  onVisualOnly: () => void;
  scenarios: ScenarioInfo[];
  caps: Capabilities | null;
  conn: "connecting" | "open" | "closed";
  speed: number;
  onSpeed: (s: number) => void;
  onStart: (s: ScenarioInfo) => void;
  onMic: () => void;
  onRtc: (role: Role, room: string, source: "mic" | "clip", clip?: ScenarioInfo) => void;
  rtcPhase: RtcPhase | null;
  notice: string | null;
  asr: TranscriptionStatus | undefined;
  onRetryAsr: () => void;
}

const PHASE_TEXT: Record<RtcPhase, string> = {
  signaling: "Connecting to the signaling server…",
  "waiting-for-peer": "Waiting for the other tab to join this room…",
  negotiating: "Negotiating the connection…",
  connected: "Connected. Audio is flowing peer to peer.",
  "peer-left": "The other side left the call.",
  failed: "Connection failed (this build has no STUN/TURN, so it works on localhost or the same network).",
  closed: "Disconnected.",
  "room-full": "This room already has two participants. Pick another room code.",
};

const SIGNALS: [string, string, string][] = [
  ["01", "Synthetic speech", "Does the audio look machine-made?"],
  ["02", "Voice manipulation", "Has it been spliced or edited?"],
  ["03", "Scam conversation", "OTPs, money, remote access, threats, secrecy."],
];

/** Left label column + right content, repeated down the page. Both halves reveal on scroll. */
function Section({ id, n, title, aside, children }: { id?: string; n: string; title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="grid scroll-mt-6 gap-x-14 gap-y-6 border-b border-rule py-10 lg:grid-cols-12">
      <header className="lg:col-span-3">
        <Reveal>
          <p className="eyebrow">{n}</p>
          <h2 className="display mt-2 text-[clamp(34px,4vw,48px)]">{title}</h2>
          {aside && <div className="mt-4">{aside}</div>}
        </Reveal>
      </header>
      <div className="min-w-0 lg:col-span-9"><Reveal delay={120}>{children}</Reveal></div>
    </section>
  );
}

export function Launcher(p: Props) {
  const [step1Open, setStep1Open] = useState(true);
  const ready = p.status.audio === "ready" || p.visualOnly;
  const connOk = p.conn === "open";
  const enable = async () => { await p.engine.enable(); setStep1Open(false); };

  return (
    <div>
      <section className="grid items-center gap-x-14 gap-y-12 pb-12 pt-8 md:pt-14 lg:grid-cols-12">
        <div className="lg:col-span-7">
          <h1 className="display text-[clamp(60px,10.5vw,150px)] tracking-[-0.03em]"><Scramble text="TRINETRA" /></h1>
          <p className="mt-7 text-[clamp(20px,2.4vw,30px)] font-medium leading-tight tracking-[-0.015em]">Real-time voice fraud intelligence</p>
          <p className="mt-2 max-w-[46ch] text-[17px] text-ink-2">Detect suspicious voice signals and scam tactics while the conversation is happening.</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a href="#demo" className="btn btn-primary btn-arrow">Run a demo call</a>
            <a href="#live" className="btn">Live analysis</a>
          </div>
        </div>
        <div className="flex flex-col items-center gap-6 lg:col-span-5">
          <EyeMark size={290} />
          <ol className="w-full max-w-[380px] border-t border-ink bg-black/60 px-3 backdrop-blur-sm">
            {SIGNALS.map(([n, name, note]) => (
              <li key={n} className="grid grid-cols-[32px_1fr] border-b border-rule py-2.5">
                <span className="eyebrow pt-0.5">{n}</span>
                <span><b className="font-medium">{name}</b><br /><span className="text-[13px] text-ink-2">{note}</span></span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <div className="border-y border-ink py-3 text-ink-2">
        <Marquee seconds={46} items={["Synthetic speech indicators", "Voice manipulation indicators", "Scam conversation", "OTP requests", "Safe-account transfers", "Remote-access installs", "Impersonation", "Pressure and secrecy", "Nothing leaves this machine"]} />
      </div>
      <dl className="grid grid-cols-2 border-b border-ink md:grid-cols-4">
        {([[p.scenarios.length || 7, "", "Scripted scenarios"], [3, "", "Independent signals"], [0, "", "External API calls"], [100, "%", "Local analysis"]] as [number, string, string][]).map(([n, suffix, label], i) => (
          <div key={label} className={cx("px-1 py-6 md:px-6", i > 0 && "md:border-l md:border-rule", i % 2 === 1 && "border-l border-rule md:border-l")}>
            <dd className="display text-[clamp(48px,5.5vw,84px)]"><CountUp to={n} suffix={suffix} /></dd>
            <dt className="eyebrow mt-2">{label}</dt>
          </div>
        ))}
      </dl>
      <p className="mt-4 max-w-[70ch] text-[13px] text-ink-2">A browser demo, not a phone interceptor. Analysis stays on this machine. Simulated and real results are labelled.</p>

      {p.conn !== "open" && (
        <div role="status" className="mt-6 border-l-[3px] border-amber py-1 pl-4 text-[14px]">
          {p.conn === "connecting" ? "Connecting to the TRINETRA backend…" : "Cannot reach the TRINETRA backend. Start it with `npm run dev` and reload. Retrying…"}
        </div>
      )}
      {p.notice && <div role="alert" className="mt-6 border-l-[3px] border-amber py-1 pl-4 text-[14px]">{p.notice}</div>}

      <Section n="01 — Alerts" title="Enable alerts">
        {step1Open || !ready ? (
          <div className="max-w-[62ch]">
            <p className="text-[16px] text-ink-2">Browsers only play sound after a click. Enabling alerts lets TRINETRA ping you, and vibrate where supported, when a call looks risky, so you do not have to watch the screen.</p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Btn variant="primary" onClick={() => void enable()}>Enable alerts</Btn>
              <Btn onClick={() => { p.onVisualOnly(); setStep1Open(false); }}>Continue with visual alerts only</Btn>
            </div>
            {p.status.audio === "blocked" && <p className="mt-3 text-[14px] text-amber">The browser blocked audio. Click “Enable alerts” again, or continue with visual alerts only.</p>}
            {p.status.audio === "unsupported" && <p className="mt-3 text-[14px] text-amber">This browser has no Web Audio support. Visual warnings will still appear.</p>}
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
            <p className="mono text-[13px]"><span className={p.status.audio === "ready" ? "text-green" : "text-ink-2"}>●</span> {p.status.audio === "ready" ? "Sound ready" : "Visual alerts only"}</p>
            <p className="mono text-[13px]"><span className={p.status.vibration === "supported" ? "text-green" : "text-ink-2"}>●</span> {p.status.vibration === "supported" ? "Vibration supported" : "Vibration unsupported on this device"}</p>
            <div className="ml-auto flex gap-2">
              <Btn onClick={() => void p.engine.test("high")}>Test sound</Btn>
              <Btn onClick={() => setStep1Open(true)}>Change</Btn>
            </div>
          </div>
        )}
      </Section>

      <Section id="demo" n="02 — Demo calls" title="Start a demo call"
        aside={
          <label className="block max-w-40">
            <span className="eyebrow">Playback speed</span>
            <select aria-label="Playback speed" value={p.speed} onChange={(e) => p.onSpeed(Number(e.target.value))} className="field">
              <option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option>
            </select>
          </label>
        }>
        <p className="mb-8 max-w-[64ch] text-[16px] text-ink-2">
          Scripted scenarios replay the same timeline every time. Transcript lines are scripted; the scam analysis, risk engine and alerts are the real code paths.
          Voice evidence tagged <span className="mono border border-dashed border-amber px-1 text-[11px] uppercase text-amber">Simulated</span> is injected by the scenario, not detected.
        </p>
        <div className="border-t border-ink">
          {p.scenarios.map((s, idx) => {
            const lvl = s.expected.max_level as Level;
            return (
              <Reveal key={s.id} delay={(idx % 3) * 70}>
              <article data-testid={`scenario-${s.id}`} className="row grid gap-x-8 gap-y-4 border-b border-rule px-2 py-7 md:grid-cols-[48px_1fr_auto]">
                <span className="row-n mono pt-2 text-[13px] text-ink-2">{s.bonus ? "+" : String(s.order).padStart(2, "0")}</span>
                <div className="min-w-0">
                  <h3 className="row-title display text-[clamp(30px,3.4vw,44px)]">{s.title.replace(/^Bonus:\s*/, "")}</h3>
                  <p className="mt-2 max-w-[58ch] text-[15px] text-ink-2">{s.summary}</p>
                  <dl className="mono mt-4 flex flex-wrap gap-x-7 gap-y-1.5 text-[12px]">
                    <div><dt className="inline text-ink-2">Audio </dt><dd className="inline">{s.audio_kind === "user" ? "your recording" : s.has_audio ? "bundled TTS clip" : "transcript only"}</dd></div>
                    <div><dt className="inline text-ink-2">Expect </dt><dd className={cx("inline font-medium", LEVEL_STYLE[lvl].text)}>{LEVEL_LABEL[lvl]}</dd>
                      <dd className="inline"> · {s.expected.pings} ping{s.expected.pings === 1 ? "" : "s"}{s.expected.alert_at ? ` · warning at ${fmtClock(s.expected.alert_at)}` : ""}</dd></div>
                    {s.injected.length > 0 && <div><dt className="inline text-amber">Simulated </dt><dd className="inline">{s.injected.map((e) => `${e.dimension} ${fmtClock(e.t)}`).join(", ")}</dd></div>}
                  </dl>
                </div>
                <div className="flex items-start md:justify-end">
                  <Btn variant="primary" arrow disabled={!ready || !connOk} onClick={() => p.onStart(s)}
                    title={!ready ? "Enable alerts first" : !connOk ? "Backend not connected" : undefined}>
                    Start demo call
                  </Btn>
                </div>
              </article>
              </Reveal>
            );
          })}
          {p.scenarios.length === 0 && <p className="py-6 text-[14px] text-ink-2">Scenarios load once the backend connects.</p>}
        </div>
      </Section>

      <LiveSection {...p} ready={ready} connOk={connOk} />
    </div>
  );
}

function LiveSection(p: Props & { ready: boolean; connOk: boolean }) {
  const [room, setRoom] = useState(() => Math.random().toString(36).slice(2, 6));
  const [role, setRole] = useState<Role>("callee");
  const [source, setSource] = useState<"clip" | "mic">("clip");
  const clips = p.scenarios.filter((s) => s.has_audio);
  const [clipId, setClipId] = useState("synthetic-relative-scam");
  const clip = clips.find((c) => c.id === clipId) ?? clips[0];
  const tx = p.asr ?? p.caps?.transcription;
  const det = p.caps?.detectors;

  return (
    <Section id="live" n="03 — Live" title="Live analysis"
      aside={<span className="eyebrow">Experimental</span>}>
      <dl className="mono grid gap-x-8 gap-y-2 border-b border-ink pb-5 text-[12px] sm:grid-cols-3">
        <div data-testid="launcher-asr" data-state={tx?.state ?? "unknown"}><dt className="text-ink-2">Transcription</dt><dd>{tx?.state === "ready" ? `ready · ${tx.name}` : tx?.state === "loading" ? "loading speech model…" : tx?.state === "error" ? "error" : "unavailable"}</dd></div>
        <div><dt className="text-ink-2">Synthetic detector</dt><dd>{det?.synthetic.available ? det.synthetic.name : "unavailable"}</dd></div>
        <div><dt className="text-ink-2">Manipulation detector</dt><dd>{det?.manipulation.available ? `${det.manipulation.name}${det.manipulation.experimental ? " (experimental)" : ""}` : "unavailable"}</dd></div>
      </dl>
      {tx && (tx.state === "unavailable" || tx.state === "error" || tx.state === "loading") && (
        <div role="status" className="mt-5 border-l-[3px] border-amber py-1 pl-4 text-[13px]" data-testid="launcher-asr-notice">
          <p className="font-medium">{tx.state === "loading" ? tx.detail || "Loading the local speech model…" : tx.detail}</p>
          {tx.action && <p className="mt-0.5 text-ink-2">{tx.action}</p>}
          {tx.cause && <p className="mono mt-1 break-all text-[11px] text-ink-2">{tx.cause}</p>}
          {tx.state !== "loading" && <Btn className="mt-2 !min-h-8 !px-3 !text-[13px]" onClick={p.onRetryAsr}>Retry</Btn>}
        </div>
      )}
      <div className="grid gap-x-14 gap-y-10 pt-8 lg:grid-cols-2">
        <div>
          <h3 className="display text-[30px]">Microphone analysis</h3>
          <p className="mt-2 max-w-[48ch] text-[15px] text-ink-2">Analyses your microphone only, after the browser asks for permission. It does not capture phone calls or system audio. Audio stays on this machine. Without a local speech model, type what the caller says.</p>
          <Btn className="mt-5" arrow disabled={!p.ready || !p.connOk} onClick={p.onMic}>Start microphone analysis</Btn>
        </div>
        <div>
          <h3 className="display text-[30px]">In-app call, two tabs</h3>
          <p className="mt-2 max-w-[48ch] text-[15px] text-ink-2">Open this page twice with the same room code. One tab is the <b className="font-medium text-ink">callee</b> and analyses what it hears; the other is the <b className="font-medium text-ink">caller</b>. Use headphones if both use microphones.</p>
          <div className="mt-5 grid gap-x-6 gap-y-4 sm:grid-cols-2">
            <label><span className="eyebrow">Room code</span>
              <input value={room} onChange={(e) => setRoom(e.target.value.replace(/[^a-z0-9]/gi, "").toLowerCase())} className="field mono" />
            </label>
            <label><span className="eyebrow">This tab is the</span>
              <select value={role} onChange={(e) => setRole(e.target.value as Role)} className="field">
                <option value="callee">Callee (analysed)</option><option value="caller">Caller (sends audio)</option>
              </select>
            </label>
            {role === "caller" && (
              <>
                <label><span className="eyebrow">Caller audio</span>
                  <select value={source} onChange={(e) => setSource(e.target.value as "clip" | "mic")} className="field">
                    <option value="clip">Demo clip (synthetic TTS)</option><option value="mic">Microphone</option>
                  </select>
                </label>
                {source === "clip" && (
                  <label><span className="eyebrow">Clip</span>
                    <select value={clip?.id} onChange={(e) => setClipId(e.target.value)} className="field">
                      {clips.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
                    </select>
                  </label>
                )}
              </>
            )}
          </div>
          <Btn className="mt-5" arrow disabled={!p.ready || !p.connOk || !room} onClick={() => p.onRtc(role, room, source, clip)}>
            {role === "callee" ? "Join as callee" : "Call"}
          </Btn>
          {p.rtcPhase && <p className={cx("mt-3 text-[14px]", p.rtcPhase === "failed" || p.rtcPhase === "room-full" ? "text-amber" : "text-ink-2")} role="status">{PHASE_TEXT[p.rtcPhase]}</p>}
        </div>
      </div>
    </Section>
  );
}
