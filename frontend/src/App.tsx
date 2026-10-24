import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { CallScreen } from "./components/CallScreen";
import { Launcher } from "./components/Launcher";
import type { Thresholds } from "./components/Panels";
import { Backdrop } from "./components/Backdrop";
import { EdgeGlow, EyeMark, Spotlight } from "./components/fx";
import { AlertEngine } from "./lib/alerts";
import { useCall } from "./lib/useCall";
import { useHealth } from "./lib/useHealth";
import { useSession } from "./lib/useSession";
import type { ScenarioInfo } from "./types";

type Rules = { levels: Thresholds; alerts?: { cooldown_s?: number; global_min_gap_s?: number; escalation_delta?: number } };
const DEFAULT_THRESHOLDS: Thresholds = { elevated: 0.3, high: 0.55, critical: 0.8 };

export default function App() {
  const engine = useMemo(() => new AlertEngine(), []);
  const subscribe = useMemo(() => engine.subscribe.bind(engine), [engine]);
  const status = useSyncExternalStore(subscribe, () => engine.status);
  const { state, dispatch, send, sendAudio, setQuiet } = useSession(engine);
  const call = useCall({ send, sendAudio, dispatch });
  const { health, retryTranscription } = useHealth(state.conn === "open");
  const [quiet, setQuietState] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [visualOnly, setVisualOnly] = useState(false);
  const [rules, setRules] = useState<Rules | null>(null);
  const [lastScenario, setLastScenario] = useState<ScenarioInfo | null>(null);

  useEffect(() => {
    fetch("/api/rules").then((r) => r.json()).then(setRules).catch(() => undefined);
  }, []);

  // Release the demo clip / mic when the server says the call is over.
  const { release } = call;
  useEffect(() => { if (state.call.state === "ended") release(); }, [state.call.state, release]);

  const thresholds = rules?.levels ?? DEFAULT_THRESHOLDS;
  const active = state.call.state !== "idle";
  // While a call runs the session reports the live state; otherwise the health endpoint does.
  const asr = active ? state.transcription ?? health?.transcription : health?.transcription ?? state.caps?.transcription;
  const level = active ? state.assessment?.level ?? "low" : "low";

  const startScenario = (s: ScenarioInfo) => { setLastScenario(s); void call.startScenario(s, speed); };
  const onQuiet = (q: boolean) => { setQuietState(q); setQuiet(q); };
  const home = () => { call.stop(); dispatch({ type: "reset" }); };

  return (
    <>
    <Backdrop level={level} analyser={call.analyser} pulse={state.findings.length + state.alerts.length} />
    <Spotlight />
    <EdgeGlow level={active && state.activeAlert && (state.activeAlert.level === "high" || state.activeAlert.level === "critical") ? state.activeAlert.level : null} />
    <div className="page mx-auto min-h-full max-w-[1320px] px-5 pb-20 sm:px-8">
      <header className="flex items-center justify-between gap-4 border-b border-rule py-5">
        <button onClick={home} className="flex items-center gap-2.5" aria-label="TRINETRA home">
          <EyeMark size={34} level={level} />
          <span className="display text-[26px] leading-none tracking-[-0.01em]">Trinetra</span>
        </button>
        <div className="flex items-center gap-6">
          {state.conn === "closed" && (
            <p className="mono flex items-center gap-2 text-[12px] text-red" role="status">
              <i aria-hidden className="live-dot !h-1.5 !w-1.5" />Offline, retrying
            </p>
          )}
        </div>
      </header>

      <main className="pt-8">
        {active ? (
          <CallScreen
            state={state} engine={engine} status={status} analyser={call.analyser} thresholds={thresholds} rules={rules}
            quiet={quiet} onQuiet={onQuiet} notice={call.notice} onClearNotice={() => call.setNotice(null)}
            onEnd={call.stop}
            onReplay={state.call.mode === "scenario" && lastScenario ? () => startScenario(lastScenario) : null}
            onBack={home}
            onDismiss={() => dispatch({ type: "dismiss-alert" })}
            onManual={(speaker, text) => send({ type: "manual_transcript", speaker, text })}
            asr={asr} onRetryAsr={() => void retryTranscription()} conn={state.conn}
          />
        ) : (
          <Launcher
            engine={engine} status={status} visualOnly={visualOnly} onVisualOnly={() => setVisualOnly(true)}
            scenarios={state.scenarios} caps={state.caps} conn={state.conn} speed={speed} onSpeed={setSpeed}
            onStart={startScenario} onMic={() => void call.startMic()}
            onRtc={(role, room, source, clip) => void call.startRtcCall(role, room, source, clip)}
            rtcPhase={call.rtcPhase} notice={call.notice} asr={asr} onRetryAsr={() => void retryTranscription()}
          />
        )}
      </main>

      <footer className="mt-16 grid gap-3 border-t border-ink pt-4 text-[13px] text-ink-2 md:grid-cols-2">
        <p>TRINETRA indicates possible risk; it never proves fraud. A synthetic voice is not automatically a scammer, and a human voice can be used for fraud.</p>
        <p className="md:text-right">Scam detection is rule-based and English only. Nothing here is a trained accuracy claim.</p>
      </footer>
    </div>
    </>
  );
}
