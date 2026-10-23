import { useEffect, useRef } from "react";
import { installPointer, pointer, prefersReducedMotion } from "../lib/fx";
import type { Level } from "../types";

/**
 * Ridgeline backdrop: a stack of waveform lines drawn back to front, each hiding the ones behind it.
 *
 * It bounces. The amplitude is an under-damped spring, so every kick overshoots and rings before it
 * settles, and each line reads the amplitude with a small delay, so a kick rolls toward you through the stack.
 * Kicks come from:
 *   - events         (`pulse` increments when a finding or alert arrives)
 *   - real audio     (the AnalyserNode of the clip, microphone or remote stream)
 *   - the cursor     (fast flicks and clicks)
 * The tint and the resting energy follow the risk level. Reduced motion draws one still frame.
 */
const TINT: Record<Level, [number, number, number]> = {
  low: [200, 255, 61],
  elevated: [255, 178, 36],
  high: [255, 83, 64],
  critical: [255, 83, 64],
};
const REST: Record<Level, number> = { low: 0.22, elevated: 0.4, high: 0.58, critical: 0.8 };
const SPEED: Record<Level, number> = { low: 0.6, elevated: 0.85, high: 1.2, critical: 1.8 };
const LINES = 34;
const STEP = 8;
const HIST = 96; // frames of amplitude history (about 3 s)

interface Props {
  level: Level;
  analyser: AnalyserNode | null;
  pulse: number;
}

export function Backdrop({ level, analyser, pulse }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef({ level, analyser, pulse });
  live.current = { level, analyser, pulse };

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const g = canvas.getContext("2d");
    if (!g) return;
    installPointer();
    const reduce = prefersReducedMotion();
    let w = 0, h = 0, raf = 0, last = 0, t = 0;
    let speed = SPEED[live.current.level];
    let lastPulse = live.current.pulse;
    const tint = [...TINT[live.current.level]];
    let bins: Uint8Array | null = null;

    // under-damped spring for the amplitude (px), and one for the horizontal peak position
    let amp = 20, ampV = 0;
    let peakX = window.innerWidth * 0.64, peakV = 0;
    const hist = new Float32Array(HIST).fill(amp);
    let head = 0;
    let px = pointer.px, py = pointer.py;
    let beat = 0;

    const kick = (v: number) => { ampV += v; };
    const onDown = () => kick(520);
    window.addEventListener("pointerdown", onDown, { passive: true });

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (reduce) frame(0.033);
    };

    const audioEnergy = (): number => {
      const an = live.current.analyser;
      if (!an) return 0;
      if (!bins || bins.length !== an.frequencyBinCount) bins = new Uint8Array(an.frequencyBinCount);
      an.getByteFrequencyData(bins as Uint8Array<ArrayBuffer>);
      const n = Math.max(1, Math.floor(bins.length * 0.35));
      let sum = 0;
      for (let i = 0; i < n; i++) sum += bins[i];
      return Math.min(1, (sum / n / 255) * 1.7);
    };

    const frame = (rawDt: number) => {
      const dt = Math.min(rawDt, 0.05);
      const { level: lv, pulse: pl } = live.current;
      const k = 1 - Math.exp(-Math.min(rawDt, 0.5) * 3.2);
      const target = TINT[lv];
      for (let i = 0; i < 3; i++) tint[i] += (target[i] - tint[i]) * k;
      speed += (SPEED[lv] - speed) * k;
      t += dt * speed;

      // ---- kicks ------------------------------------------------------------------------
      if (pl !== lastPulse) { kick(820); lastPulse = pl; }
      const dx = pointer.px - px, dy = pointer.py - py;
      px = pointer.px; py = pointer.py;
      const flick = Math.hypot(dx, dy) / Math.max(dt, 0.001); // px per second
      if (flick > 900) kick(Math.min(260, flick * 0.045));
      beat += dt * (1.1 + REST[lv] * 1.8);
      const heartbeat = Math.max(0, Math.sin(beat * Math.PI * 2)) ** 6; // sharp periodic thump
      const ae = audioEnergy();
      const restAmp = 14 + REST[lv] * 70 + ae * 120 + heartbeat * (10 + REST[lv] * 26);

      // ---- spring (semi-implicit Euler, substepped for stability) -------------------------
      const stiffness = 46, damping = 3.4; // damping ratio ~0.25: rings several times
      const sub = 3;
      for (let s = 0; s < sub; s++) {
        const h2 = dt / sub;
        ampV += (stiffness * (restAmp - amp) - damping * ampV) * h2;
        amp += ampV * h2;
      }
      amp = Math.max(3, Math.min(amp, 260));

      const wantPeak = w * (0.26 + pointer.x * 0.64);
      for (let s = 0; s < sub; s++) {
        const h2 = dt / sub;
        peakV += (18 * (wantPeak - peakX) - 3.2 * peakV) * h2;
        peakX += peakV * h2;
      }

      head = (head + 1) % HIST;
      hist[head] = amp;

      // ---- draw ---------------------------------------------------------------------------
      const top = h * 0.12, span = h * 0.82;
      const [r, gr, b] = tint.map(Math.round);
      g.clearRect(0, 0, w, h);
      for (let i = 0; i < LINES; i++) {
        const depth = i / (LINES - 1); // 0 = back, 1 = front
        const a = hist[(head - Math.round(i * 1.1) + HIST * 4) % HIST]; // front lines lag: the kick rolls toward you
        const baseY = top + depth * span;
        const gain = a * (0.5 + depth * 0.8);
        const line = new Path2D();
        for (let x = 0; x <= w + STEP; x += STEP) {
          const ex = (x - peakX) / (w * 0.27);
          const env = 0.1 + 0.9 * Math.exp(-ex * ex);
          const n =
            Math.sin(x * 0.011 + t * 0.9 + i * 0.55) * 0.55 +
            Math.sin(x * 0.027 - t * 1.35 + i * 0.9) * 0.3 +
            Math.sin(x * 0.063 + t * 2.2 + i * 1.7) * 0.15;
          const y = baseY - gain * env * (n * 0.5 + 0.5);
          if (x === 0) line.moveTo(x, y);
          else line.lineTo(x, y);
        }
        const fill = new Path2D(line);
        fill.lineTo(w + STEP, h);
        fill.lineTo(0, h);
        fill.closePath();
        g.fillStyle = "#000";
        g.fill(fill);
        g.lineWidth = 1 + depth * 0.8;
        g.strokeStyle = `rgba(${r},${gr},${b},${(0.08 + depth * 0.3).toFixed(3)})`;
        g.stroke(line);
      }
    };

    const loop = (ts: number) => {
      raf = requestAnimationFrame(loop);
      if (ts - last < 16) return; // up to ~60 fps so the spring reads as smooth
      const dt = (ts - last) / 1000;
      last = ts;
      frame(dt);
    };

    resize();
    window.addEventListener("resize", resize);
    if (!reduce) raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointerdown", onDown);
    };
  }, []);

  return (
    <>
      <canvas ref={ref} aria-hidden className="backdrop" />
      {/* keeps the text column calm: waves fade out toward the left edge and the top */}
      <div aria-hidden className="backdrop" style={{ background: "linear-gradient(90deg, rgb(0 0 0 / .8) 0%, rgb(0 0 0 / .5) 40%, rgb(0 0 0 / .22) 100%), linear-gradient(180deg, rgb(0 0 0 / .9) 0%, transparent 22%)" }} />
    </>
  );
}
