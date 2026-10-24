import { useTween } from "../lib/fx";
import { fmtClock } from "../lib/store";
import type { Level, TimelineEntry } from "../types";
import type { Thresholds } from "./Panels";
import { cx } from "./ui";

const LEVEL_COLOR: Record<Level, string> = { low: "var(--green)", elevated: "var(--amber)", high: "var(--red)", critical: "var(--red)" };

/* ================================================================== risk dial */
const CX = 130, CY = 128, R = 104;
const pt = (s: number, r = R) => {
  const a = Math.PI * (1 - s);
  return [CX + r * Math.cos(a), CY - r * Math.sin(a)] as const;
};
function arc(a: number, b: number, r = R): string {
  if (b - a < 0.0005) return "";
  const [x1, y1] = pt(a, r);
  const [x2, y2] = pt(b, r);
  return `M${x1.toFixed(2)} ${y1.toFixed(2)}A${r} ${r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

/** Semicircular gauge. The needle and arc ease to the score; the coloured bands are the configured thresholds. */
export function RiskDial({ score, level, thresholds }: { score: number; level: Level; thresholds: Thresholds }) {
  const v = Math.max(0, Math.min(1, useTween(score, 900)));
  const bands: [number, number, string, number][] = [
    [0, thresholds.elevated, "var(--green)", 0.28],
    [thresholds.elevated, thresholds.high, "var(--amber)", 0.34],
    [thresholds.high, thresholds.critical, "var(--red)", 0.34],
    [thresholds.critical, 1, "var(--red)", 0.6],
  ];
  const color = LEVEL_COLOR[level];
  return (
    <svg viewBox="-10 -6 280 188" overflow="visible" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={score} aria-label="Conversation risk score" className="block w-full max-w-[340px]">
      {bands.map(([a, b, c, o], i) => <path key={i} d={arc(a + 0.004, b - 0.004)} fill="none" stroke={c} strokeOpacity={o} strokeWidth="12" />)}
      <path d={arc(0, v)} fill="none" stroke={color} strokeWidth="12" style={{ filter: `drop-shadow(0 0 7px ${color})`, transition: "stroke .5s" }} />
      {Array.from({ length: 21 }, (_, i) => {
        const s = i / 20;
        const [x1, y1] = pt(s, i % 2 === 0 ? 84 : 88);
        const [x2, y2] = pt(s, 94);
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--ink-2)" strokeOpacity={i % 2 === 0 ? 0.9 : 0.4} />;
      })}
      {(["elevated", "high", "critical"] as const).map((k) => {
        const [x, y] = pt(thresholds[k], 120);
        return <text key={k} x={x} y={y} fontSize="8.5" textAnchor="middle" dominantBaseline="middle" fill="var(--ink-2)" fontFamily="var(--font-mono)">{thresholds[k].toFixed(2)}</text>;
      })}
      <g transform={`rotate(${(v - 0.5) * 180} ${CX} ${CY})`}>
        <path d={`M${CX - 3} ${CY}L${CX} ${CY - 80}L${CX + 3} ${CY}Z`} fill="var(--ink)" />
      </g>
      <circle cx={CX} cy={CY} r="7" fill="var(--paper)" stroke="var(--ink)" strokeWidth="1.5" />
      <text x={CX} y={CY + 34} textAnchor="middle" fontSize="30" fontFamily="var(--font-mono)" fill="var(--ink)" fontWeight="300">{v.toFixed(2)}</text>
      <text x={CX} y={CY + 50} textAnchor="middle" fontSize="8.5" letterSpacing="1.2" fill="var(--ink-2)" fontFamily="var(--font-mono)">CONVERSATION SCORE</text>
    </svg>
  );
}

/* ================================================================== risk over time */
export function Sparkline({ history, thresholds, duration }: {
  history: { t: number; score: number; level: Level }[]; thresholds: Thresholds; duration: number | null;
}) {
  const W = 320, H = 96, P = 6;
  if (history.length < 2) return <p className="text-[13px] text-ink-2">The risk curve is drawn from real assessments as the call runs.</p>;
  const last = history[history.length - 1];
  const maxT = Math.max(duration ?? 0, last.t, 30);
  const x = (t: number) => P + (t / maxT) * (W - 2 * P);
  const y = (s: number) => H - P - s * (H - 2 * P);
  // step-after: risk only changes when evidence arrives
  let d = `M${x(history[0].t).toFixed(1)} ${y(history[0].score).toFixed(1)}`;
  for (let i = 1; i < history.length; i++) d += `H${x(history[i].t).toFixed(1)}V${y(history[i].score).toFixed(1)}`;
  d += `H${x(Math.max(last.t, last.t)).toFixed(1)}`;
  const area = `${d}V${H - P}H${x(history[0].t).toFixed(1)}Z`;
  const color = LEVEL_COLOR[last.level];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" role="img" aria-label="Risk over the call" data-testid="sparkline">
      <defs>
        <linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".35" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {(["elevated", "high", "critical"] as const).map((k) => (
        <g key={k}>
          <line x1={P} x2={W - P} y1={y(thresholds[k])} y2={y(thresholds[k])} stroke="var(--ink-2)" strokeOpacity=".4" strokeDasharray="2 4" />
          <text x={W - P} y={y(thresholds[k]) - 3} textAnchor="end" fontSize="7.5" fill="var(--ink-2)" fontFamily="var(--font-mono)" letterSpacing=".6">{k.toUpperCase()}</text>
        </g>
      ))}
      <path d={area} fill="url(#spark-fill)" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" style={{ transition: "stroke .5s" }} />
      <circle cx={x(last.t)} cy={y(last.score)} r="3.2" fill={color} />
      <circle cx={x(last.t)} cy={y(last.score)} r="3.2" fill="none" stroke={color} className="pulse-ring" />
    </svg>
  );
}

/* ================================================================== three-signal radar */
const AXES = [-90, 30, 150].map((deg) => (deg * Math.PI) / 180);
const RR = 78;
const at = (axis: number, v: number) => [Math.cos(AXES[axis]) * RR * v, Math.sin(AXES[axis]) * RR * v] as const;

export function SignalRadar({ values, level, naText = ["unavailable", "unavailable", "unavailable"] }: {
  /** 0..1, or null when that signal has no detector. The three are shown side by side, never combined. */
  values: [number | null, number | null, number | null]; level: Level; naText?: [string, string, string];
}) {
  const v0 = useTween(values[0] ?? 0, 800), v1 = useTween(values[1] ?? 0, 800), v2 = useTween(values[2] ?? 0, 800);
  const vs = [v0, v1, v2];
  const color = LEVEL_COLOR[level];
  const poly = vs.map((v, i) => at(i, Math.max(0.02, v)).map((n) => n.toFixed(1)).join(",")).join(" ");
  const ring = (k: number) => AXES.map((_, i) => at(i, k).map((n) => n.toFixed(1)).join(",")).join(" ");
  const labels: [string, string, "middle" | "start" | "end", number, number][] = [
    ["SYNTHETIC", "01", "middle", 0, -RR - 18],
    ["MANIPULATION", "02", "start", at(1, 1)[0] + 8, at(1, 1)[1] + 14],
    ["SCAM", "03", "end", at(2, 1)[0] - 8, at(2, 1)[1] + 14],
  ];
  return (
    <svg viewBox="-135 -112 270 215" className="mx-auto block w-full max-w-[300px]" role="img" aria-label="Three signals radar" data-testid="radar">
      {[0.33, 0.66, 1].map((k) => <polygon key={k} points={ring(k)} fill="none" stroke="var(--ink-2)" strokeOpacity={k === 1 ? 0.6 : 0.25} />)}
      {AXES.map((_, i) => <line key={i} x1="0" y1="0" x2={at(i, 1)[0]} y2={at(i, 1)[1]} stroke="var(--ink-2)" strokeOpacity=".35" />)}
      <polygon points={poly} fill={color} fillOpacity=".16" stroke={color} strokeWidth="1.6" strokeLinejoin="round" style={{ transition: "fill .5s, stroke .5s" }} />
      {vs.map((v, i) => {
        const [px, py] = at(i, Math.max(0.02, v));
        const na = values[i] === null;
        return na
          ? <circle key={i} cx={at(i, 1)[0]} cy={at(i, 1)[1]} r="4" fill="none" stroke="var(--ink-2)" strokeDasharray="2 2" />
          : <circle key={i} cx={px} cy={py} r="3.6" fill={color} style={{ filter: `drop-shadow(0 0 5px ${color})` }} />;
      })}
      {labels.map(([name, n, anchor, lx, ly], i) => (
        <text key={n} x={lx} y={ly} textAnchor={anchor} fontFamily="var(--font-mono)" fontSize="8.5" letterSpacing="1" fill="var(--ink)">
          <tspan fill="var(--ink-2)">{n} </tspan>{name}
          <tspan x={lx} dy="11" fill="var(--ink-2)" fontSize="8">{values[i] === null ? naText[i] : `${Math.round((values[i] as number) * 100)}%`}</tspan>
        </text>
      ))}
    </svg>
  );
}

/* ================================================================== call scrubber */
export function CallScrubber({ elapsed, duration, markers, running }: {
  elapsed: number; duration: number | null; markers: TimelineEntry[]; running: boolean;
}) {
  const total = duration ?? Math.max(60, Math.ceil((elapsed * 1.25) / 15) * 15);
  const pct = Math.min(100, (elapsed / total) * 100);
  return (
    <div className="select-none" role="progressbar" aria-valuemin={0} aria-valuemax={Math.round(total)} aria-valuenow={Math.round(elapsed)} aria-label="Call progress" data-testid="scrubber">
      <div className="relative h-9">
        {markers.filter((m) => m.kind !== "call").map((m) => (
          <span key={m.id} title={`${m.clock} ${m.title}`} data-kind={m.kind} data-level={m.level}
            className={cx("marker-in absolute bottom-3 w-[2px]", m.kind === "alert" ? "h-6" : m.kind === "voice" ? "h-3.5" : "h-4.5",
              m.level === "low" ? "bg-ink-2" : m.level === "elevated" ? "bg-amber" : "bg-red")}
            style={{ left: `${Math.min(100, (m.t / total) * 100)}%` }} />
        ))}
        <div className="absolute inset-x-0 bottom-2.5 h-px bg-rule" />
        <div className="absolute bottom-2.5 left-0 h-px bg-green transition-[width] duration-200 ease-linear" style={{ width: `${pct}%` }} />
        <span aria-hidden className={cx("absolute bottom-[7px] -ml-[5px] h-[9px] w-[9px] bg-green", running && "playhead")} style={{ left: `${pct}%`, boxShadow: "0 0 10px var(--green)" }} />
      </div>
      <div className="eyebrow flex justify-between"><span>{fmtClock(0)}</span><span>{fmtClock(total)}</span></div>
    </div>
  );
}
