import type {
  Alert, Assessment, CallMode, Level, CallStateName, Capabilities, Finding, ScenarioInfo, ServerMsg, TimelineEntry,
  TranscriptLine, TranscriptionStatus, VoiceResult,
} from "../types";

export interface CallInfo {
  state: CallStateName;
  mode: CallMode;
  t: number;
  info: Record<string, unknown>;
  startedAt: number | null; // performance.now() when "running" arrived
  speed: number;
  summary: { level: string; peak?: string; alerts: number } | null;
}

export interface UiError {
  code: string;
  message: string;
  at: number;
}

export interface SessionState {
  conn: "connecting" | "open" | "closed";
  caps: Capabilities | null;
  scenarios: ScenarioInfo[];
  call: CallInfo;
  transcript: TranscriptLine[];
  findings: Finding[];
  timeline: TimelineEntry[];
  assessment: Assessment | null;
  history: { t: number; score: number; level: Level }[]; // one point per risk assessment, for the risk-over-time chart
  voice: Partial<Record<"synthetic" | "manipulation", VoiceResult>>;
  alerts: Alert[];
  activeAlert: Alert | null; // warning card (high/critical)
  indicator: Alert | null; // subtle elevated indicator
  rms: number;
  transcription: TranscriptionStatus | null; // live status of speech recognition during a call
  partial: string; // interim text for speech that is still being recognised
  errors: UiError[];
}

export const initialCall: CallInfo = { state: "idle", mode: "idle", t: 0, info: {}, startedAt: null, speed: 1, summary: null };

export const initialState: SessionState = {
  conn: "connecting",
  caps: null,
  scenarios: [],
  call: initialCall,
  transcript: [],
  findings: [],
  timeline: [],
  assessment: null,
  history: [],
  voice: {},
  alerts: [],
  activeAlert: null,
  indicator: null,
  rms: 0,
  transcription: null,
  partial: "",
  errors: [],
};

export type Action =
  | { type: "conn"; conn: SessionState["conn"] }
  | { type: "msg"; msg: ServerMsg; now: number }
  | { type: "dismiss-alert" }
  | { type: "local-error"; code: string; message: string; now: number }
  | { type: "clear-errors" }
  | { type: "reset" };

export function reducer(s: SessionState, a: Action): SessionState {
  switch (a.type) {
    case "conn":
      return { ...s, conn: a.conn };
    case "dismiss-alert":
      return { ...s, activeAlert: null };
    case "clear-errors":
      return { ...s, errors: [] };
    case "reset":
      return { ...initialState, conn: s.conn, caps: s.caps, scenarios: s.scenarios };
    case "local-error":
      return { ...s, errors: [...s.errors.slice(-4), { code: a.code, message: a.message, at: a.now }] };
    case "msg":
      return applyMessage(s, a.msg, a.now);
  }
}

function applyMessage(s: SessionState, m: ServerMsg, now: number): SessionState {
  switch (m.type) {
    case "hello":
      return { ...s, caps: m.capabilities, scenarios: m.scenarios };
    case "call_state": {
      if (m.state === "running") {
        // a new call resets everything that belongs to a call
        return {
          ...s,
          caps: m.capabilities ?? s.caps,
          call: {
            state: "running", mode: m.mode, t: 0, info: m.info ?? {}, startedAt: now,
            speed: Number(m.info?.speed ?? 1) || 1, summary: null,
          },
          transcript: [], findings: [], timeline: [], assessment: null, history: [], voice: {}, alerts: [],
          activeAlert: null, indicator: null, rms: 0, partial: "", transcription: m.capabilities?.transcription ?? s.caps?.transcription ?? null, errors: [],
        };
      }
      return { ...s, call: { ...s.call, state: m.state, t: m.t, summary: m.summary ?? null } };
    }
    case "transcription_status": {
      const { type: _t, ...status } = m;
      void _t;
      return { ...s, transcription: status };
    }
    case "transcript_partial":
      return { ...s, partial: m.text };
    case "transcript":
      return { ...s, transcript: [...s.transcript, m.line], call: { ...s.call, t: Math.max(s.call.t, m.line.t) } };
    case "finding":
      return { ...s, findings: [...s.findings, m.finding] };
    case "voice":
      return { ...s, voice: { ...s.voice, [m.result.dimension]: m.result } };
    case "risk": {
      const a = m.assessment;
      const prev = s.history[s.history.length - 1];
      const same = prev && prev.score === a.score && prev.level === a.level;
      return { ...s, assessment: a, history: same ? s.history : [...s.history.slice(-399), { t: a.t, score: a.score, level: a.level }] };
    }
    case "timeline":
      return { ...s, timeline: [...s.timeline, m.entry] };
    case "alert": {
      const alerts = [...s.alerts, m.alert];
      if (m.alert.kind === "warning") return { ...s, alerts, activeAlert: m.alert, indicator: null };
      return { ...s, alerts, indicator: s.activeAlert ? s.indicator : m.alert };
    }
    case "audio_metrics":
      return { ...s, rms: m.rms };
    case "error":
      return { ...s, errors: [...s.errors.slice(-4), { code: m.code, message: m.message, at: now }] };
    default:
      return s;
  }
}

export function fmtClock(t: number): string {
  const s = Math.max(0, Math.round(t));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
