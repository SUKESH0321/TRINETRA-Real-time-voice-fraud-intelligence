import { useEffect, useRef } from "react";

/**
 * Spectrum read-out driven by a real AnalyserNode: mirrored bars with falling peak caps.
 * With no signal it "breathes" gently so the panel never looks dead.
 */
export function Visualizer({ analyser, active, caption }: { analyser: AnalyserNode | null; active: boolean; caption: string }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const g = canvas.getContext("2d");
    if (!g) return;
    let raf = 0, frame = 0, t = 0;
    let ink = "#f3f2ec", accent = "#c8ff3d", faint = "rgba(255,255,255,.15)";
    const BARS = 72;
    const peaks = new Float32Array(BARS);
    const smooth = new Float32Array(BARS);
    const data = new Uint8Array(analyser?.frequencyBinCount ?? 0);
    const draw = () => {
      if (frame++ % 60 === 0) {
        const cs = getComputedStyle(canvas);
        ink = cs.getPropertyValue("--ink").trim() || ink;
        accent = cs.getPropertyValue("--green").trim() || accent;
        faint = cs.getPropertyValue("--rule").trim() || faint;
      }
      t += 0.03;
      const { width: w, height: h } = canvas;
      g.clearRect(0, 0, w, h);
      const live = !!analyser && active;
      if (live) analyser!.getByteFrequencyData(data);
      const mid = h / 2;
      const step = w / BARS;
      for (let i = 0; i < BARS; i++) {
        let v: number;
        if (live) {
          const idx = Math.floor((i / BARS) ** 1.7 * data.length * 0.5);
          v = data[idx] / 255;
        } else {
          v = 0.035 + 0.03 * (Math.sin(t * 1.6 + i * 0.35) * 0.5 + 0.5) + 0.02 * Math.sin(t * 0.7 + i * 0.12);
        }
        smooth[i] += (v - smooth[i]) * 0.35;
        peaks[i] = Math.max(smooth[i], peaks[i] - 0.006);
        const bh = Math.max(2, smooth[i] * mid * 0.95);
        const x = Math.round(i * step);
        g.fillStyle = live && smooth[i] > 0.03 ? ink : faint;
        g.fillRect(x, mid - bh, 3, bh * 2);
        if (live) {
          g.fillStyle = accent;
          g.fillRect(x, mid - peaks[i] * mid * 0.95 - 4, 3, 2);
          g.fillRect(x, mid + peaks[i] * mid * 0.95 + 2, 3, 2);
        }
      }
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [analyser, active]);

  return (
    <figure className="m-0">
      <canvas ref={ref} width={960} height={96} role="img" aria-label="Live audio activity" className="block h-[96px] w-full" />
      <figcaption className="eyebrow mt-1.5 flex justify-between border-t border-rule pt-1.5">
        <span className="flex items-center gap-2">{active && <i className="live-dot !h-1.5 !w-1.5 text-green" />}Audio activity</span><span>{caption}</span>
      </figcaption>
    </figure>
  );
}
