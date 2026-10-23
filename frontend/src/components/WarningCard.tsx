import { useState } from "react";
import type { Alert } from "../types";
import type { AlertStatus } from "../lib/alerts";
import { fmtClock } from "../lib/store";
import { Marquee } from "./fx";
import { Btn, cx, Highlighted, LEVEL_LABEL, SourceBadge } from "./ui";

interface Props {
  alert: Alert;
  status: AlertStatus;
  onDismiss: () => void;
  onMute: () => void;
}

/**
 * In-call warning. High = ruled paper with a red frame. Critical = solid red, white type.
 * It reports possible risk and the evidence behind it; it never says "fraud confirmed".
 */
export function WarningCard({ alert, status, onDismiss, onMute }: Props) {
  const [showEvidence, setShowEvidence] = useState(false);
  const critical = alert.level === "critical";
  const audible = !!alert.sound && !status.muted;
  const dim = critical ? "text-white/75" : "text-ink-2";
  const ruleCls = critical ? "border-white/30" : "border-rule";

  return (
    <div role="alertdialog" aria-live="assertive" aria-label={alert.headline} data-testid="warning-card" data-level={alert.level}
      className={cx("overflow-hidden border-2", critical ? "on-red anim-shake anim-critical border-[var(--alarm)] bg-[var(--alarm)] text-white" : "anim-drop border-red bg-surface text-ink")}>
      {critical && <Marquee seconds={22} className="bg-black py-2 text-white" items={[alert.recommended_action, "Do not share codes", "Hang up", alert.recommended_action]} />}
      <div className="p-5 sm:p-7">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <p className={cx("eyebrow", critical ? "!text-white" : "!text-red")}>
            Alert · {LEVEL_LABEL[alert.level]}{alert.trigger === "escalation" ? " · risk increased" : alert.trigger === "update" ? " · new evidence" : ""}
          </p>
          <p className={cx("eyebrow", critical && "!text-white/75")}>
            {alert.sound ? (audible ? "Sound alert" : "Sound muted") : "Visual warning"}
            {alert.vibrate && status.vibration === "supported" && status.vibrationEnabled ? " · vibration sent" : ""}
          </p>
        </div>

        <h3 data-testid="warning-headline" className="mt-3 text-[clamp(26px,4.2vw,44px)] font-semibold uppercase leading-[1.02] tracking-[-0.015em]">
          {alert.headline}
        </h3>

        <p className="mt-4 max-w-[62ch] text-[17px] leading-snug"><span className="font-semibold">Reason:</span> {alert.reason}</p>

        <dl className={cx("mt-5 border-t text-[14px]", ruleCls)}>
          <div className={cx("grid grid-cols-[132px_1fr] gap-3 border-b py-2.5", ruleCls)}>
            <dt className={cx("eyebrow !normal-case !tracking-normal", critical && "!text-white/75")}>Transcript</dt>
            <dd className="min-w-0 text-[15px]">
              {alert.evidence.transcript
                ? <>&ldquo;<Highlighted text={alert.evidence.transcript} spans={alert.evidence.spans} />&rdquo;</>
                : <span className={dim}>No transcript evidence. Voice signal only.</span>}
            </dd>
          </div>
          <div className={cx("grid grid-cols-[132px_1fr] gap-3 border-b py-2.5", ruleCls)}>
            <dt className={cx("eyebrow !normal-case !tracking-normal", critical && "!text-white/75")}>Detection time</dt>
            <dd className="mono">{fmtClock(alert.evidence.detected_at)}</dd>
          </div>
          <div className={cx("grid grid-cols-[132px_1fr] gap-3 border-b py-2.5", ruleCls)}>
            <dt className={cx("eyebrow !normal-case !tracking-normal", critical && "!text-white/75")}>Conversation risk</dt>
            <dd className="font-semibold">{LEVEL_LABEL[alert.conversation_risk]}</dd>
          </div>
          <div className={cx("grid grid-cols-[132px_1fr] gap-3 border-b py-2.5", ruleCls)}>
            <dt className={cx("eyebrow !normal-case !tracking-normal", critical && "!text-white/75")}>Voice authenticity</dt>
            <dd>{alert.voice_authenticity}</dd>
          </div>
        </dl>

        <div className="mt-5 border-l-[3px] border-current pl-4">
          <p className={cx("eyebrow", critical && "!text-white/75")}>Recommended action</p>
          <p className="mt-1 max-w-[60ch] text-[17px] font-medium leading-snug">{alert.recommended_action}</p>
        </div>

        <div className="mt-6 flex flex-wrap gap-2">
          <Btn variant={critical ? "inverse" : "primary"} onClick={onDismiss}>I understand</Btn>
          <Btn variant={critical ? "ghost-inverse" : "ghost"} onClick={() => setShowEvidence((v) => !v)}>
            {showEvidence ? "Hide evidence" : "View evidence"}
          </Btn>
          {!status.muted && <Btn variant={critical ? "ghost-inverse" : "ghost"} onClick={onMute}>Mute alert sound</Btn>}
        </div>

        {showEvidence && (
          <div className={cx("mt-6 border-t pt-4 text-[14px]", ruleCls)} data-testid="evidence-detail">
            <ul className="space-y-4">
              {alert.evidence.conversation.map((e, i) => (
                <li key={`c${i}`}>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-semibold">{e.label}</span>
                    <span className="mono text-[12px] opacity-75">{fmtClock(e.t)}</span>
                    <SourceBadge source={e.source} className={critical ? "!border-white/60 !text-white" : ""} />
                    <span className="mono text-[11px] opacity-70">{e.method}</span>
                  </div>
                  {e.excerpt && <p className={cx("mt-1", dim)}>&ldquo;<Highlighted text={e.excerpt} spans={e.spans ?? []} />&rdquo;</p>}
                </li>
              ))}
              {alert.evidence.voice.map((e, i) => (
                <li key={`v${i}`}>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-semibold">{e.label}</span>
                    <SourceBadge source={e.source} className={critical ? "!border-white/60 !text-white" : ""} />
                    <span className="mono text-[11px] opacity-70">{e.method}</span>
                  </div>
                  {e.evidence?.map((x, j) => <p key={j} className={cx("mt-1", dim)}>{x}</p>)}
                  {e.limitations?.map((x, j) => <p key={j} className={cx("mt-1 text-[13px]", dim)}>Limitation: {x}</p>)}
                </li>
              ))}
            </ul>
            {alert.uncertainty.length > 0 && (
              <ul className={cx("mt-4 list-disc space-y-0.5 pl-5 text-[13px]", dim)}>
                {alert.uncertainty.map((u, i) => <li key={i}>{u}</li>)}
              </ul>
            )}
          </div>
        )}

        <p className={cx("mt-5 text-[12px]", dim)}>This is a risk indication from rule-based analysis, not proof of fraud.</p>
      </div>
    </div>
  );
}
