/** Small animation helpers. Everything here degrades to "no motion" when the user asks for reduced motion. */
import { useEffect, useRef, useState } from "react";

export function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** One shared pointer position so many components can follow the cursor without each adding a listener. */
export const pointer = {
  px: typeof window !== "undefined" ? window.innerWidth * 0.5 : 0,
  py: typeof window !== "undefined" ? window.innerHeight * 0.35 : 0,
  x: 0.5,
  y: 0.35,
  moved: false,
};
let pointerInstalled = false;
export function installPointer(): void {
  if (pointerInstalled || typeof window === "undefined") return;
  pointerInstalled = true;
  window.addEventListener(
    "pointermove",
    (e) => {
      pointer.px = e.clientX;
      pointer.py = e.clientY;
      pointer.x = e.clientX / Math.max(1, window.innerWidth);
      pointer.y = e.clientY / Math.max(1, window.innerHeight);
      pointer.moved = true;
    },
    { passive: true },
  );
}

/** True once the element has scrolled into view (and stays true). */
export function useInView<T extends Element>(rootMargin = "0px 0px -8% 0px"): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === "undefined" || prefersReducedMotion());
  useEffect(() => {
    const el = ref.current;
    if (seen || !el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      },
      { rootMargin },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [seen, rootMargin]);
  return [ref, seen];
}

/** Eases a number toward `target` (ease-out cubic). Returns the animated value. */
export function useTween(target: number, ms = 700): number {
  const [value, setValue] = useState(target);
  const current = useRef(target);
  useEffect(() => {
    if (prefersReducedMotion()) {
      current.current = target;
      setValue(target);
      return;
    }
    const from = current.current;
    const start = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const p = Math.min(1, (now - start) / ms);
      current.current = from + (target - from) * (1 - Math.pow(1 - p, 3));
      setValue(current.current);
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return value;
}

const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#/\\_<>";

/** Decode-in text effect: random glyphs that settle left to right into `text`. */
export function useScramble(text: string, delay = 0, ms = 1100): string {
  const mask = (n: number) => Array.from({ length: n }, () => GLYPHS[Math.floor(Math.random() * GLYPHS.length)]).join("");
  const [out, setOut] = useState(() => (prefersReducedMotion() ? text : mask(text.length)));
  useEffect(() => {
    if (prefersReducedMotion()) {
      setOut(text);
      return;
    }
    const start = performance.now() + delay;
    let raf = 0;
    const step = (now: number) => {
      const p = Math.max(0, Math.min(1, (now - start) / ms));
      const settled = Math.floor(p * text.length);
      let s = "";
      for (let i = 0; i < text.length; i++) s += i < settled || text[i] === " " ? text[i] : GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
      setOut(p >= 1 ? text : s);
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [text, delay, ms]);
  return out;
}
