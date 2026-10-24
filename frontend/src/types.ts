export type Level = "low" | "elevated" | "high" | "critical";
export type Source = "real" | "scripted" | "simulated" | "manual" | "unavailable";
export type CallMode = "idle" | "scenario" | "live-mic" | "webrtc";
export type CallStateName = "idle" | "running" | "ended";

export interface TranscriptLine {
  id: string;
  t: number;
  speaker: "caller" | "you";
  text: string;
  source: Source;
  method: string;
  session?: string;
}

export interface Finding {
  id: string;
  category: string;
  label: string;
  severity: Level;
  score: number;
  confidence: number;
  matched: string[];
  excerpt: string;
  spans: [number, number][];
  t: number;
  action: string;
  method: string;
  source: Source;
}

export interface VoiceResult {
  dimension: "synthetic" | "manipulation";
  status: "unavailable" | "inconclusive" | "clear" | "indicators";
  score: number | null;
  confidence: number;
  evidence: string[];
  method: string;
  limitations: string[];
  source: Source;
  t: number;
  experimental: boolean;
}

export interface DimensionState {
  value: number | null;
  status: string;
  source: Source;
  label: string;
  method: string;
}

export interface EvidenceItem {
  kind: "conversation" | "voice";
  label: string;
  t: number;
  score: number;
  source: Source;
  method: string;
  category?: string;
  excerpt?: string;
  matched?: string[];
  spans?: [number, number][];
  dimension?: string;
  evidence?: string[];
  limitations?: string[];
}

export interface Assessment {
  t: number;
  level: Level;
  score: number;
  confidence: number;
  reasons: string[];
  evidence: EvidenceItem[];
  uncertainty: string[];
  recommended_action: string;
  alert_eligible: boolean;
  primary_category: string | null;
  dimensions: { scam: DimensionState; synthetic: DimensionState; manipulation: DimensionState };
  voice_authenticity: string;
  bumped: boolean;
}

export interface Alert {
  id: string;
  t: number;
  level: Level;
  kind: "indicator" | "warning";
  headline: string;
  reason: string;
  reasons: string[];
  trigger: "new" | "escalation" | "update";
  evidence: {
    transcript: string | null;
    matched: string[];
    spans: [number, number][];
    detected_at: number;
    conversation: EvidenceItem[];
    voice: EvidenceItem[];
  };
  conversation_risk: Level;
  voice_authenticity: string;
  recommended_action: string;
  uncertainty: string[];
  sound: "high" | "critical" | null;
  vibrate: number[] | null;
  quiet_mode: boolean;
}

export interface TimelineEntry {
  id: string;
  t: number;
  clock: string;
  kind: "call" | "finding" | "voice" | "alert";
  level: Level;
  title: string;
  detail: string;
  source: Source;
  method: string;
}

export interface ScenarioInfo {
  id: string;
  title: string;
  summary: string;
  voice_kind: "unknown" | "synthetic-tts";
  voice_note: string;
  duration: number;
  has_audio: boolean;
  audio_url: string | null;
  expected: { max_level: Level; pings: number; note: string; alert_at?: number };
  injected: { t: number; dimension: string; status: string; score?: number; evidence?: string[] }[];
  bonus: boolean;
  order: number;
}

export type TranscriptionState = "idle" | "loading" | "ready" | "transcribing" | "unavailable" | "error";

export interface TranscriptionDiag {
  frames: number;
  audio_s: number;
  rms: number;
  quiet_s: number;
  decodes: number;
  decode_ms: number;
  buffer_s: number;
  finals: number;
  windows_skipped: number;
  invalid_chunks: number;
}

export interface TranscriptionStatus {
  state: TranscriptionState;
  name: string;
  code?: string;
  detail: string;
  action?: string;
  cause?: string;
  diag?: TranscriptionDiag;
}

export interface Capabilities {
  detectors: Record<"synthetic" | "manipulation", { available: boolean; name: string; experimental: boolean }>;
  transcription: TranscriptionStatus;
}

export type ServerMsg =
  | { type: "hello"; capabilities: Capabilities; scenarios: ScenarioInfo[]; seq?: number }
  | { type: "call_state"; state: CallStateName; mode: CallMode; t: number; info?: Record<string, unknown>; capabilities?: Capabilities; summary?: { level: Level; peak?: Level; alerts: number } }
  | { type: "transcript"; line: TranscriptLine }
  | { type: "transcript_partial"; text: string; t: number; session: string }
  | ({ type: "transcription_status" } & TranscriptionStatus)
  | { type: "finding"; finding: Finding }
  | { type: "voice"; result: VoiceResult }
  | { type: "risk"; assessment: Assessment }
  | { type: "alert"; alert: Alert }
  | { type: "timeline"; entry: TimelineEntry }
  | { type: "audio_metrics"; t: number; rms: number; peak: number; clipping: number }
  | { type: "settings"; quiet_mode: boolean }
  | { type: "error"; code: string; message: string }
  | { type: "pong" };
