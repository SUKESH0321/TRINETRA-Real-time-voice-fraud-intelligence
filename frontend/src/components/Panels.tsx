import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Assessment, DimensionState, Finding, Level, TimelineEntry, TranscriptionStatus, TranscriptLine, VoiceResult } from "../types";
import { fmtClock } from "../lib/store";
import { RiskDial, SignalRadar, Sparkline } from "./viz";
import { Btn, Card, cx, Highlighted, LEVEL_LABEL, LEVEL_STYLE, Meter, SourceBadge } from "./ui";

export type Thresholds = { elevated: number; high: number; critical: number };

/* ---------------------------------------------------------------- Overall risk */
export function RiskOverview({ a, thresholds, running }: { a: Assessment | null; thresholds: Thresholds; running: boolean }) {
  const level: Level = a?.level ?? "low";
  const s = LEVEL_STYLE[level];
  const score = Math.max(0, Math.min(1, a?.score ?? 0));
  return (
    <Card title="Overall risk" right={<span>{a && a.confidence > 0 ? `Confidence ${(a.confidence * 100).toFixed(0)}%` : running ? "Listening" : "Idle"}</span>}>
      <p key={level} className={cx("display glint italic text-[clamp(72px,9.5vw,116px)]", s.text)} data-testid="overall-level">{LEVEL_LABEL[level]}</p>
      <div className="mt-2 flex justify-center"><RiskDial score={score} level={level} thresholds={thresholds} /></div>

      {a && (
        <div className="mt-4 space-y-2">
          <p className="text-[17px] leading-snug">{a.reasons[0]}</p>
          {a.reasons.slice(1).map((r, i) => <p key={i} className="text-[14px] text-ink-2">{r}</p>)}
          <div className="border-l-[3px] border-green pl-4 pt-1">
            <p className="eyebrow">Recommended action</p>
            <p className="mt-0.5 text-[15px] font-medium leading-snug">{a.recommended_action}</p>
          </div>
          {a.uncertainty.length > 0 && (
            <details className="pt-1 text-[13px] text-ink-2">
              <summary><span className="eyebrow"><span className="caret" />Uncertainty ({a.uncertainty.length})</span></summary>
              <ul className="mt-2 list-disc space-y-1 pl-5">{a.uncertainty.map((u, i) => <li key={i}>{u}</li>)}</ul>
            </details>
          )}
        </div>
      )}
    </Card>
  );
}

export function RiskHistory({ history, thresholds, duration }: {
  history: { t: number; score: number; level: Level }[]; thresholds: Thresholds; duration: number | null;
}) {
  return (
    <Card title="Risk over time" right={<span>from real assessments</span>}>
      <Sparkline history={history} thresholds={thresholds} duration={duration} />
    </Card>
  );
}

/* ---------------------------------------------------------------- Three independent signals */
function tone(status: string): string {
  return status === "indicators" ? "text-amber" : status === "clear" ? "text-green" : "text-ink-2";
}

function VoiceRow({ n, title, dim, result }: { n: string; title: string; dim: DimensionState | undefined; result: VoiceResult | undefined }) {
  const status = dim?.status ?? "unavailable";
  const lvl: Level = status === "indicators" ? "elevated" : "low";
  return (
    <li className="grid grid-cols-[32px_1fr] gap-x-2 border-t border-rule py-4 first:border-t-0 first:pt-0" data-testid={`indicator-${result?.dimension ?? title}`} data-status={status}>
      <span className="eyebrow pt-1">{n}</span>
      <div className="min-w-0">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-[15px] font-medium">{title}</h3>
          <SourceBadge source={dim?.source ?? "unavailable"} />
        </div>
        <p key={status} className={cx("display anim-line mt-1 text-[30px] italic", tone(status))}>{dim?.label ?? "Not analysed"}</p>
        {dim?.value != null && status !== "unavailable" && <Meter value={dim.value} level={lvl} segments={24} className="mt-2" />}
        {result && (
          <details className="mt-2 text-[13px] text-ink-2">
            <summary><span className="eyebrow"><span className="caret" />Evidence &amp; limitations</span></summary>
            <p className="mono mt-2 text-[12px] text-ink">Method: {result.method}{result.experimental ? " (experimental)" : ""}</p>
            {result.evidence.map((e, i) => <p key={i} className="mt-1">{e}</p>)}
            <ul className="mt-2 list-disc space-y-1 pl-5">{result.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
          </details>
        )}
      </div>
    </li>
  );
}

export function Indicators({ a, voice, findings }: {
  a: Assessment | null; voice: Partial<Record<"synthetic" | "manipulation", VoiceResult>>; findings: Finding[];
}) {
  const scam = a?.dimensions.scam;
  const level = (scam?.status ?? "low") as Level;
  const last = findings.length ? findings[findings.length - 1] : null;
  const val = (d: DimensionState | undefined): number | null => (d && d.status !== "unavailable" && d.value != null ? d.value : null);
  return (
    <Card title="Three independent signals" right={<span>never summed</span>}>
      <SignalRadar values={[val(a?.dimensions.synthetic), val(a?.dimensions.manipulation), val(scam)]} level={a?.level ?? "low"} />
      <ol className="mt-6">
        <VoiceRow n="01" title="Synthetic speech indicators" dim={a?.dimensions.synthetic} result={voice.synthetic} />
        <VoiceRow n="02" title="Voice manipulation indicators" dim={a?.dimensions.manipulation} result={voice.manipulation} />
        <li className="grid grid-cols-[32px_1fr] gap-x-2 border-t border-rule py-4" data-testid="indicator-scam" data-status={scam?.status ?? "low"}>
          <span className="eyebrow pt-1">03</span>
          <div className="min-w-0">
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-[15px] font-medium">Scam conversation</h3>
              <SourceBadge source="real" />
            </div>
            <p key={scam?.label} className={cx("display anim-line mt-1 text-[30px] italic", LEVEL_STYLE[level].text)}>{scam?.label ?? "No suspicious requests"}</p>
            <Meter value={scam?.value ?? 0} level={level} segments={24} className="mt-2" />
            <p className="mt-2 text-[13px] text-ink-2">Rule-based phrase and context analysis, English only. Not a trained model.{last ? ` Latest: ${last.label.toLowerCase()}.` : ""}</p>
          </div>
        </li>
      </ol>
    </Card>
  );
}

/* ---------------------------------------------------------------- Transcript */
const ASR_LABEL: Record<string, string> = {
  idle: "not started", loading: "loading speech model…", ready: "ready", transcribing: "transcribing…", unavailable: "unavailable", error: "error",
};

/** What the speech recogniser is really doing, with the next step when it cannot work. */
export function AsrNotice({ asr, onRetry, live }: { asr: TranscriptionStatus | undefined; onRetry?: () => void; live: boolean }) {
  if (!asr) return null;
  const d = asr.diag;
  const bad = asr.state === "unavailable" || asr.state === "error";
  const silent = live && asr.state !== "unavailable" && !!d && d.audio_s >= 4 && d.quiet_s >= 4;
  const stuck = live && asr.state === "ready" && !!d && d.audio_s >= 8 && d.decodes === 0 && !silent;
  if (!bad && asr.state !== "loading" && !silent && !stuck) return null;
  return (
    <div role="status" data-testid="asr-notice" data-state={asr.state} className="mt-3 border-l-[3px] border-amber py-1 pl-4 text-[13px]">
      {asr.state === "loading" && <p>{asr.detail || "Loading the local speech model…"}</p>}
      {bad && (
        <>
          <p className="font-medium text-ink">{asr.detail}</p>
          {asr.action && <p className="mt-0.5 text-ink-2">{asr.action}</p>}
          {asr.cause && <p className="mono mt-1 break-all text-[11px] text-ink-2">{asr.cause}</p>}
          {onRetry && <Btn className="mt-2 !min-h-8 !px-3 !text-[13px]" onClick={onRetry}>Retry</Btn>}
        </>
      )}
      {silent && !bad && <p>No speech detected: the microphone signal has been silent for {Math.round(d!.quiet_s)} s. This is not a model failure. Check the input level and that the right microphone is selected.</p>}
      {stuck && <p>Audio is arriving ({d!.audio_s.toFixed(0)} s) but nothing has been recognised yet. Keep speaking, or check the backend log.</p>}
    </div>
  );
}

export function TranscriptPanel({ lines, findings, asr, partial, onRetry, onSend, canSend, status, live }: {
  lines: TranscriptLine[]; findings: Finding[];
  asr: TranscriptionStatus | undefined; partial: string; onRetry?: () => void; live: boolean;
  onSend?: (speaker: "caller" | "you", text: string) => void; canSend: boolean; status: "idle" | "running" | "ended";
}) {
  const box = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const [speaker, setSpeaker] = useState<"caller" | "you">("caller");
  useEffect(() => { box.current?.scrollTo?.({ top: box.current.scrollHeight, behavior: "smooth" }); }, [lines.length, partial]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim() || !onSend) return;
    onSend(speaker, text.trim());
    setText("");
  };
  const d = asr?.diag;
  const label = asr ? (asr.state === "ready" || asr.state === "transcribing" ? `${ASR_LABEL[asr.state]}${asr.name && asr.name !== "none" ? ` · ${asr.name}` : ""}` : ASR_LABEL[asr.state]) : "unknown";

  return (
    <Card title="Transcript" right={<span data-testid="transcription-status" data-state={asr?.state ?? "unknown"}>Live transcription · {label}</span>}>
      <div ref={box} className="max-h-80 min-h-28 overflow-y-auto pr-2" data-testid="transcript">
        {lines.length === 0 && !partial && (
          <p className="text-[14px] text-ink-2">
            {status === "running" ? <>Waiting for speech<span className="inline-flex w-6 gap-0.5 pl-1"><i className="live-dot !h-1 !w-1" /><i className="live-dot !h-1 !w-1" /><i className="live-dot !h-1 !w-1" /></span></> : "Transcript appears here during a call."}
          </p>
        )}
        {lines.map((l) => {
          const f = findings.filter((x) => x.t === l.t && l.text.includes(x.excerpt));
          const spans: [number, number][] = f.flatMap((x) => { const o = l.text.indexOf(x.excerpt); return x.spans.map(([a, b]) => [a + o, b + o] as [number, number]); });
          return (
            <div key={l.id} className="anim-line grid grid-cols-[64px_1fr] gap-x-4 border-t border-rule py-3 first:border-t-0 first:pt-0" data-testid="transcript-line" data-source={l.source}>
              <div>
                <p className="mono text-[12px]">{fmtClock(l.t)}</p>
                <p className={cx("eyebrow mt-0.5", l.speaker === "caller" ? "!text-ink" : "")}>{l.speaker === "caller" ? "Caller" : "You"}</p>
              </div>
              <div>
                <p className={cx("text-[16px] leading-snug", l.speaker === "you" && "text-ink-2")}><Highlighted text={l.text} spans={spans} /></p>
                <div className="mt-1.5 flex items-center gap-2"><SourceBadge source={l.source} />{l.method && l.source === "real" && <span className="mono text-[10px] text-ink-2">{l.method}</span>}</div>
              </div>
            </div>
          );
        })}
        {partial && (
          <div className="grid grid-cols-[64px_1fr] gap-x-4 border-t border-rule py-3" data-testid="transcript-partial">
            <div><p className="eyebrow">Hearing</p></div>
            <p className="text-[16px] italic leading-snug text-ink-2">{partial}<span className="inline-flex w-6 gap-0.5 pl-1 align-middle"><i className="live-dot !h-1 !w-1" /><i className="live-dot !h-1 !w-1" /></span></p>
          </div>
        )}
      </div>
      <AsrNotice asr={asr} onRetry={onRetry} live={live} />
      {live && d && (
        <p className="mono mt-3 text-[11px] text-ink-2" data-testid="asr-diag">
          audio {d.audio_s.toFixed(1)} s · level {d.rms.toFixed(3)} · recognised {d.finals} line{d.finals === 1 ? "" : "s"} · last decode {d.decode_ms} ms · buffer {d.buffer_s.toFixed(1)} s
          {d.windows_skipped > 0 ? ` · ${d.windows_skipped} voice windows skipped (busy)` : ""}
        </p>
      )}
      {canSend && (
        <form onSubmit={submit} className="mt-4 flex flex-wrap items-end gap-3">
          <label className="w-36 shrink-0">
            <span className="eyebrow">Speaker</span>
            <select aria-label="Speaker" value={speaker} onChange={(e) => setSpeaker(e.target.value as "caller" | "you")} className="field">
              <option value="caller">Caller says</option><option value="you">You say</option>
            </select>
          </label>
          <label className="min-w-0 flex-1">
            <span className="eyebrow">What was said (optional)</span>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="“Please tell me your OTP”" aria-label="Manual transcript text" className="field" />
          </label>
          <Btn type="submit" variant="primary" disabled={!text.trim()}>Analyse</Btn>
        </form>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------- Evidence timeline (ledger) */
const KIND_LABEL: Record<TimelineEntry["kind"], string> = { call: "Call", finding: "Speech", voice: "Voice", alert: "Alert" };

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  const box = useRef<HTMLOListElement>(null);
  useEffect(() => { box.current?.scrollTo?.({ top: box.current.scrollHeight, behavior: "smooth" }); }, [entries.length]);
  return (
    <Card title="Evidence timeline" right={<span>{entries.length} events</span>}>
      <ol ref={box} className="max-h-96 min-h-28 overflow-y-auto pr-2" data-testid="timeline">
        {entries.length === 0 && <li className="text-[14px] text-ink-2">Every event is recorded here with its time, reason and source.</li>}
        {entries.map((e) => (
          <li key={e.id} data-kind={e.kind} data-level={e.level} className="anim-flash grid grid-cols-[48px_74px_1fr] gap-x-3 border-t border-rule py-2.5 first:border-t-0 first:pt-0">
            <span className="mono pt-px text-[12px]">{e.clock}</span>
            <span className="eyebrow flex items-center gap-1.5 pt-px !text-ink">
              <span aria-hidden className={cx("h-2 w-2 shrink-0", e.level === "low" ? "bg-ink-2/50" : LEVEL_STYLE[e.level].fill)} />
              {KIND_LABEL[e.kind]}
            </span>
            <div className="min-w-0">
              <p className="text-[14px] leading-snug"><span className="sr-only">{KIND_LABEL[e.kind]}: </span>{e.title}</p>
              {e.detail && <p className="mt-0.5 text-[13px] leading-snug text-ink-2">{e.detail}</p>}
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <SourceBadge source={e.source} />
                {e.method && <span className="mono text-[10px] text-ink-2">{e.method}</span>}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
