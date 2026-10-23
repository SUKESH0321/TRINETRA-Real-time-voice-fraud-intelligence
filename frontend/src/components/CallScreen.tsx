import { useEffect, useState } from "react";
import type { AlertEngine, AlertStatus } from "../lib/alerts";
import type { SessionState } from "../lib/store";
import { AlertSettings } from "./AlertSettings";
import { Odometer } from "./fx";
import { Indicators, RiskHistory, RiskOverview, Timeline, TranscriptPanel, type Thresholds } from "./Panels";
import { CallScrubber } from "./viz";
import { Visualizer } from "./Visualizer";
import { WarningCard } from "./WarningCard";
import { Btn, cx, LEVEL_LABEL, LEVEL_STYLE } from "./ui";
import type { Level } from "../types";

interface Props {
  state: SessionState;
  engine: AlertEngine;
  status: AlertStatus;
  analyser: AnalyserNode | null;
  thresholds: Thresholds;
  rules: AlertSettingsRules;
  quiet: boolean;
  onQuiet: (v: boolean) => void;
  onEnd: () => void;
  onReplay: (() => void) | null;
  onBack: () => void;
  onDismiss: () => void;
  onManual: (speaker: "caller" | "you", text: string) => void;
  notice: string | null;
  onClearNotice: () => void;
}
type AlertSettingsRules = Parameters<typeof AlertSettings>[0]["rules"];

function useElapsed(state: SessionState["call"]) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (state.state !== "running") return;
    const id = setInterval(() => setNow(performance.now()), 200);
    return () => clearInterval(id);
  }, [state.state]);
  if (state.state !== "running" || state.startedAt == null) return state.t;
  const dur = Number(state.info.duration ?? Infinity);
  return Math.min(dur, Math.max(0, ((now - state.startedAt) / 1000) * state.speed));
}

const MODE_LABEL: Record<string, { name: string; note: string }> = {
  scenario: { name: "Scripted demo call", note: "A simulated call. Transcript lines come from a scenario script; the analysis of them is real." },
  "live-mic": { name: "Live microphone analysis", note: "Analysing your microphone. This is not a phone call." },
  webrtc: { name: "In-app WebRTC call", note: "Analysing the remote audio stream of this in-app call only." },
};

export function CallScreen(p: Props) {
  const { state } = p;
  const call = state.call;
  const elapsed = useElapsed(call);
  const mode = MODE_LABEL[call.mode] ?? { name: "Call", note: "" };
  const running = call.state === "running";
  const title = String(call.info.title ?? mode.name);
  const live = call.mode === "live-mic" || call.mode === "webrtc";
  const final = call.summary?.level as Level | undefined;

  return (
    <div>
      {state.activeAlert && (
        <div className="sticky top-3 z-30 mb-8 max-h-[88vh] overflow-y-auto">
          <WarningCard alert={state.activeAlert} status={p.status} onDismiss={p.onDismiss} onMute={() => p.engine.setMuted(true)} />
        </div>
      )}
      {!state.activeAlert && state.indicator && (
        <div role="status" data-testid="elevated-indicator" className="anim-in mb-8 border-l-[3px] border-amber py-1 pl-4">
          <p className="eyebrow !text-amber">Elevated · no sound</p>
          <p className="mt-0.5 text-[15px]">{state.indicator.reason}</p>
        </div>
      )}
      {p.notice && (
        <div role="alert" className="mb-8 flex items-start justify-between gap-4 border-l-[3px] border-amber py-1 pl-4 text-[14px]">
          <span>{p.notice}</span>
          <button onClick={p.onClearNotice} aria-label="Dismiss notice" className="link eyebrow">Dismiss</button>
        </div>
      )}

      <header className="grid items-end gap-x-10 gap-y-4 border-b border-ink pb-6 md:grid-cols-[1fr_auto]">
        <div className="min-w-0">
          <p className="eyebrow">
            {mode.name} · <span data-testid="call-status" className={running ? "!text-green" : ""}>{running && <i className="live-dot mr-1.5 !h-1.5 !w-1.5 align-middle" />}{running ? "Connected" : call.state === "ended" ? "Ended" : "Idle"}</span>
          </p>
          <h1 className="display mt-2 text-[clamp(38px,5.4vw,68px)]" data-testid="call-title">{title}</h1>
        </div>
        <Odometer seconds={elapsed} testId="call-timer" />
      </header>
      <div className="mt-5">
        <CallScrubber elapsed={elapsed} duration={call.info.duration ? Number(call.info.duration) : null} markers={state.timeline} running={running} />
      </div>

      <div className="mt-8 grid gap-x-14 gap-y-10 lg:grid-cols-12">
        <div className="space-y-10 lg:col-span-7">
          <section>
            <Visualizer analyser={p.analyser} active={running}
              caption={!running ? "call ended" : p.analyser ? (live ? "live audio" : "demo clip") : call.mode === "scenario" ? "no audio in this scenario" : "no signal"} />
            {live && (
              <div className="mt-3 flex items-center gap-3" data-testid="input-level" data-rms={state.rms}>
                <span className="eyebrow">Input level</span>
                <div className="relative h-px flex-1 bg-rule">
                  <div className="absolute inset-y-[-1px] left-0 h-[3px] bg-ink transition-all" style={{ width: `${Math.min(100, Math.sqrt(state.rms) * 160)}%` }} />
                </div>
                <span className="mono w-12 text-right text-[12px]">{state.rms.toFixed(3)}</span>
              </div>
            )}
            <p className="mt-4 max-w-[62ch] text-[13px] text-ink-2">{mode.note}</p>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {running
                ? <Btn variant="danger" onClick={p.onEnd}>End call</Btn>
                : <>
                    {p.onReplay && <Btn variant="primary" onClick={p.onReplay}>Replay scenario</Btn>}
                    <Btn onClick={p.onBack}>Back to start</Btn>
                  </>}
              {call.state === "ended" && call.summary && final && (
                <span className="text-[14px] text-ink-2">
                  Final risk <b className={cx("font-semibold", LEVEL_STYLE[final].text)}>{LEVEL_LABEL[final]}</b> · {call.summary.alerts} alert{call.summary.alerts === 1 ? "" : "s"}
                </span>
              )}
            </div>
            {state.errors.length > 0 && (
              <ul className="mt-3 space-y-1 text-[13px] text-amber" role="status">{state.errors.map((e, i) => <li key={i}>{e.message}</li>)}</ul>
            )}
          </section>

          <TranscriptPanel lines={state.transcript} findings={state.findings} caps={state.caps?.transcription} status={call.state}
            canSend={running && live} onSend={p.onManual} />
          <Timeline entries={state.timeline} />
        </div>

        <aside className="space-y-10 lg:col-span-5">
          <RiskOverview a={state.assessment} thresholds={p.thresholds} running={running} />
          <RiskHistory history={state.history} thresholds={p.thresholds} duration={call.info.duration ? Number(call.info.duration) : null} />
          <Indicators a={state.assessment} voice={state.voice} findings={state.findings} />
          <AlertSettings engine={p.engine} status={p.status} quiet={p.quiet} onQuiet={p.onQuiet} thresholds={p.thresholds} rules={p.rules} />
        </aside>
      </div>
    </div>
  );
}
