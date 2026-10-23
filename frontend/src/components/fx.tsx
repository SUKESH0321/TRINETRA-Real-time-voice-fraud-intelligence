import { useEffect, useRef, type ReactNode } from "react";
import { installPointer, pointer, prefersReducedMotion, useInView, useScramble, useTween } from "../lib/fx";
import type { Level } from "../types";
import { cx } from "./ui";

const LEVEL_VAR: Record<Level, string> = { low: "var(--green)", elevated: "var(--amber)", high: "var(--red)", critical: "var(--red)" };

/* ------------------------------------------------------------------ cursor spotlight */
export function Spotlight() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    installPointer();
    if (prefersReducedMotion()) return;
    let raf = 0, x = pointer.px, y = pointer.py;
    const loop = () => {
      x += (pointer.px - x) * 0.14;
      y += (pointer.py - y) * 0.14;
      ref.current?.style.setProperty("--x", `${x.toFixed(1)}px`);
      ref.current?.style.setProperty("--y", `${y.toFixed(1)}px`);
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => cancelAnimationFrame(raf);
  }, []);
  return <div ref={ref} aria-hidden className="spotlight" />;
}

/* ------------------------------------------------------------------ the third eye */
/** An iris scanner: rotating rings, a pupil that follows the cursor, a periodic blink. Pupil colour follows risk. */
export function EyeMark({ size = 220, level = "low", className }: { size?: number; level?: Level; className?: string }) {
  const pupil = useRef<SVGGElement>(null);
  useEffect(() => {
    installPointer();
    if (prefersReducedMotion()) return;
    let raf = 0, x = 0, y = 0;
    const loop = () => {
      const el = pupil.current;
      const svg = el?.ownerSVGElement;
      if (el && svg) {
        const r = svg.getBoundingClientRect();
        const dx = pointer.px - (r.left + r.width / 2);
        const dy = pointer.py - (r.top + r.height / 2);
        const dist = Math.hypot(dx, dy) || 1;
        const m = Math.min(1, dist / 280);
        x += ((dx / dist) * 17 * m - x) * 0.12;
        y += ((dy / dist) * 9 * m - y) * 0.12;
        el.setAttribute("transform", `translate(${x.toFixed(2)} ${y.toFixed(2)})`);
      }
      raf = requestAnimationFrame(loop);
    };
    loop();
    return () => cancelAnimationFrame(raf);
  }, []);

  const ticks = Array.from({ length: 72 }, (_, i) => i);
  const color = LEVEL_VAR[level];
  return (
    <svg viewBox="-100 -100 200 200" width={size} height={size} className={className} role="img" aria-label="Trinetra eye" style={{ overflow: "visible" }}>
      <g className="eye-spin-slow">
        <circle r="97" fill="none" stroke="var(--ink-2)" strokeOpacity=".5" strokeDasharray="1.5 7" />
        <circle cx="97" cy="0" r="3.2" fill={color} style={{ filter: `drop-shadow(0 0 6px ${color})`, transition: "fill .6s" }} />
      </g>
      <g className="eye-spin-rev">
        {ticks.map((i) => (
          <line key={i} x1="0" y1="-89" x2="0" y2={i % 6 === 0 ? -80 : -85} stroke="var(--ink-2)" strokeOpacity={i % 6 === 0 ? 0.9 : 0.4} transform={`rotate(${i * 5})`} />
        ))}
      </g>
      <g className="eye-blink">
        <path d="M-78 0C-48 -40 -22 -52 0 -52S48 -40 78 0C48 40 22 52 0 52S-48 40 -78 0Z" fill="none" stroke="var(--ink)" strokeWidth="1.4" />
        <g className="eye-spin-slow" style={{ opacity: 0.55 }}>
          {Array.from({ length: 36 }, (_, i) => (
            <line key={i} x1="0" y1="-26" x2="0" y2="-33" stroke="var(--ink-2)" transform={`rotate(${i * 10})`} />
          ))}
        </g>
        <circle r="33" fill="none" stroke="var(--ink-2)" strokeOpacity=".7" />
        <g ref={pupil}>
          <circle r="14" fill={color} style={{ filter: `drop-shadow(0 0 9px ${color})`, transition: "fill .6s" }} />
          <circle cx="-4.5" cy="-4.5" r="3" fill="#000" fillOpacity=".55" />
        </g>
      </g>
    </svg>
  );
}

/* ------------------------------------------------------------------ reveal on scroll */
export function Reveal({ children, delay = 0, className }: { children: ReactNode; delay?: number; className?: string }) {
  const [ref, seen] = useInView<HTMLDivElement>();
  return (
    <div ref={ref} className={cx("reveal", seen && "reveal-in", className)} style={{ transitionDelay: seen ? `${delay}ms` : "0ms" }}>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ text effects */
export function Scramble({ text, delay = 0, className }: { text: string; delay?: number; className?: string }) {
  const out = useScramble(text, delay);
  return <span className={className} aria-label={text}><span aria-hidden>{out}</span></span>;
}

export function CountUp({ to, suffix = "", className }: { to: number; suffix?: string; className?: string }) {
  const [ref, seen] = useInView<HTMLSpanElement>();
  const v = useTween(seen ? to : 0, 1500);
  return <span ref={ref} className={cx("mono tabular-nums", className)}>{Math.round(v)}{suffix}</span>;
}

/** Endless ticker. `tone="alarm"` is the black-on-red band used inside a Critical warning. */
export function Marquee({ items, seconds = 38, className }: { items: string[]; seconds?: number; className?: string }) {
  const row = items.map((t, i) => (
    <span key={i} className="flex shrink-0 items-center gap-6 pr-6">
      <span>{t}</span>
      <span aria-hidden className="text-green">/</span>
    </span>
  ));
  return (
    <div aria-hidden className={cx("marquee", className)}>
      <div className="marquee-track" style={{ animationDuration: `${seconds}s` }}>
        {row}
        {row}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ odometer timer */
function Digit({ d }: { d: number }) {
  return (
    <span className="odo">
      <span className="odo-col" style={{ transform: `translateY(${-d * 10}%)` }}>
        {Array.from({ length: 10 }, (_, i) => <span key={i}>{i}</span>)}
      </span>
    </span>
  );
}

/** mm:ss with rolling digits. The visually hidden copy carries the real text for screen readers and tests. */
export function Odometer({ seconds, testId }: { seconds: number; testId?: string }) {
  const s = Math.max(0, Math.round(seconds));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return (
    <p className="odometer mono" aria-label="Call duration">
      <span className="sr-only" data-testid={testId}>{`${mm}:${ss}`}</span>
      <span aria-hidden className="inline-flex items-baseline">
        <Digit d={Number(mm[0])} /><Digit d={Number(mm[1])} />
        <span className="odo-colon">:</span>
        <Digit d={Number(ss[0])} /><Digit d={Number(ss[1])} />
      </span>
    </p>
  );
}

/* ------------------------------------------------------------------ screen-edge alarm glow */
export function EdgeGlow({ level }: { level: Level | null }) {
  const cls = level === "critical" ? "edge-critical" : level === "high" ? "edge-high" : "";
  return <div aria-hidden className={cx("edge", cls)} />;
}
