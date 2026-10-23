import type { ReactNode } from "react";
import type { Level, Source } from "../types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export const LEVEL_LABEL: Record<Level, string> = { low: "Low", elevated: "Elevated", high: "High", critical: "Critical" };

/** Colour only ever encodes risk level. `fill` is a solid background for meters and markers. */
export const LEVEL_STYLE: Record<Level, { text: string; fill: string; border: string }> = {
  low: { text: "text-green", fill: "bg-green", border: "border-green" },
  elevated: { text: "text-amber", fill: "bg-amber-bg", border: "border-amber" },
  high: { text: "text-red", fill: "bg-red", border: "border-red" },
  critical: { text: "text-red", fill: "bg-red", border: "border-red" },
};

/** Small square-cornered level tag: LOW / ELEVATED / HIGH / CRITICAL. */
export function LevelBadge({ level, className }: { level: Level; className?: string }) {
  const s = LEVEL_STYLE[level];
  return (
    <span className={cx("mono inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.08em]", s.text, className)}>
      <span aria-hidden className={cx("h-2 w-2", s.fill)} />
      {LEVEL_LABEL[level]}
    </span>
  );
}

const SOURCE: Record<Source, { label: string; cls: string; title: string }> = {
  real: { label: "Real", cls: "border-ink text-ink", title: "Real analysis: computed by code running on actual audio or text in this session." },
  scripted: { label: "Scripted", cls: "border-rule text-ink-2", title: "Transcript line from the demo scenario script. The scam analysis of it is real." },
  simulated: { label: "Simulated", cls: "border-dashed border-amber text-amber", title: "Injected by the demo scenario. NOT the output of a detector." },
  manual: { label: "Typed", cls: "border-rule text-ink-2", title: "Typed by the user in the manual transcript box." },
  unavailable: { label: "Unavailable", cls: "border-rule text-ink-2 italic", title: "No detector is installed for this signal." },
};

/** Provenance tag. Dashed outline = not a real detection. */
export function SourceBadge({ source, className }: { source: Source; className?: string }) {
  const s = SOURCE[source] ?? SOURCE.unavailable;
  return (
    <span title={s.title} className={cx("mono inline-flex items-center border px-1.5 py-px text-[10px] uppercase leading-4 tracking-[0.08em]", s.cls, className)}>
      {s.label}
    </span>
  );
}

/** A section with a heavy top rule and a mono label, the page's basic unit instead of a boxed card. */
export function Card({ title, right, children, className, tone = "ink" }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; tone?: "ink" | "rule";
}) {
  return (
    <section className={cx("border-t pt-3", tone === "ink" ? "border-ink" : "border-rule", className)}>
      {(title || right) && (
        <header className="mb-4 flex items-baseline justify-between gap-4">
          <h2 className="eyebrow !text-ink">{title}</h2>
          {right && <div className="eyebrow flex flex-wrap items-center justify-end gap-x-3 gap-y-1">{right}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Btn({
  children, onClick, variant = "ghost", disabled, className, title, type = "button", arrow,
}: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "danger" | "inverse" | "ghost-inverse";
  disabled?: boolean; className?: string; title?: string; type?: "button" | "submit"; arrow?: boolean;
}) {
  const v = { primary: "btn-primary", ghost: "", danger: "btn-danger", inverse: "btn-inverse", "ghost-inverse": "btn-ghost-inverse" }[variant];
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick} className={cx("btn", v, arrow && "btn-arrow", className)}>
      {children}
    </button>
  );
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-6 border-t border-rule py-3 first:border-t-0">
      <span>
        <span className="block text-[14px] font-medium">{label}</span>
        {hint && <span className="block text-[13px] text-ink-2">{hint}</span>}
      </span>
      <input type="checkbox" role="switch" className="toggle" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

/** Evidence spans are underlined with a wavy line, like a proofreader's mark. */
export function Highlighted({ text, spans }: { text: string; spans: [number, number][] }) {
  const sorted = [...spans].filter(([a, b]) => b > a && a >= 0 && b <= text.length).sort((x, y) => x[0] - y[0]);
  const out: ReactNode[] = [];
  let pos = 0;
  sorted.forEach(([a, b], i) => {
    if (a < pos) return;
    if (a > pos) out.push(text.slice(pos, a));
    out.push(<mark key={i} className="hl">{text.slice(a, b)}</mark>);
    pos = b;
  });
  if (pos < text.length) out.push(text.slice(pos));
  return <>{out}</>;
}

/** Segmented meter (like a hardware level display). `value` is 0..1. */
export function Meter({ value, level, segments = 32, className }: { value: number; level: Level; segments?: number; className?: string }) {
  const on = Math.round(Math.max(0, Math.min(1, value)) * segments);
  return (
    <div aria-hidden className={cx("flex h-3 gap-[2px]", className)}>
      {Array.from({ length: segments }, (_, i) => (
        <span key={i} className={cx("h-full flex-1", i < on ? LEVEL_STYLE[level].fill : "bg-rule")} />
      ))}
    </div>
  );
}
