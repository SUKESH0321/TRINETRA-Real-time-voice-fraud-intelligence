// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(resolve(__dirname, "../../public/pcm-worklet.js"), "utf8");

/** Runs the real worklet source with a stubbed AudioWorklet environment and returns the PCM16 it emits. */
function run(nativeRate: number, input: Float32Array): { pcm: Int16Array; chunks: number[] } {
  const chunks: ArrayBuffer[] = [];
  class AudioWorkletProcessor {
    port = { postMessage: (buf: ArrayBuffer) => chunks.push(buf) };
  }
  let Proc: new () => { process: (i: Float32Array[][]) => boolean };
  new Function("AudioWorkletProcessor", "registerProcessor", "sampleRate", SOURCE)(
    AudioWorkletProcessor, (_n: string, cls: typeof Proc) => { Proc = cls; }, nativeRate,
  );
  const p = new Proc!();
  for (let i = 0; i < input.length; i += 128) p.process([[input.subarray(i, i + 128)]]);
  const sizes = chunks.map((c) => c.byteLength);
  const total = chunks.reduce((n, c) => n + c.byteLength / 2, 0);
  const pcm = new Int16Array(total);
  let o = 0;
  for (const c of chunks) { const a = new Int16Array(c); pcm.set(a, o); o += a.length; }
  return { pcm, chunks: sizes };
}

const sine = (hz: number, rate: number, seconds: number, amp = 0.5) =>
  Float32Array.from({ length: Math.floor(rate * seconds) }, (_, i) => amp * Math.sin((2 * Math.PI * hz * i) / rate));

const peak = (a: Int16Array, from = 200) => Math.max(...Array.from(a.subarray(from), Math.abs));
const crossings = (a: Int16Array) => { let n = 0; for (let i = 1; i < a.length; i++) if (a[i - 1] < 0 && a[i] >= 0) n++; return n; };

describe("AudioWorklet resampler (the code that actually runs in the browser)", () => {
  it.each([48000, 44100, 32000])("emits 16 kHz audio from %i Hz input: right length and frequency", (rate) => {
    const secs = 3;
    const { pcm } = run(rate, sine(1000, rate, secs));
    expect(Math.abs(pcm.length - 16000 * secs)).toBeLessThan(1700); // within one chunk of 3 s at 16 kHz
    const hz = crossings(pcm) / (pcm.length / 16000);
    expect(hz).toBeGreaterThan(985);
    expect(hz).toBeLessThan(1015);
  });

  it("keeps the amplitude and encodes signed little-endian PCM16", () => {
    const { pcm } = run(48000, sine(500, 48000, 1, 0.5));
    expect(peak(pcm)).toBeGreaterThan(0.48 * 32767);
    expect(peak(pcm)).toBeLessThan(0.52 * 32767);
    expect(Math.min(...pcm)).toBeLessThan(0); // negative half-waves survive: it is signed
  });

  it("posts ~100 ms chunks (an even number of bytes, at least 1600 samples)", () => {
    const { chunks } = run(48000, sine(300, 48000, 1));
    expect(chunks.length).toBeGreaterThanOrEqual(8);
    for (const size of chunks) { expect(size % 2).toBe(0); expect(size).toBeGreaterThanOrEqual(3200); }
  });

  it("clamps out-of-range input instead of wrapping around", () => {
    const { pcm } = run(48000, sine(400, 48000, 0.5, 3.0));
    expect(Math.max(...pcm)).toBeLessThanOrEqual(32767);
    expect(Math.min(...pcm)).toBeGreaterThanOrEqual(-32768);
  });

  it("attenuates energy above 8 kHz instead of folding it into the speech band", () => {
    const inBand = peak(run(48000, sine(1000, 48000, 1)).pcm);
    const above = peak(run(48000, sine(11000, 48000, 1)).pcm);
    expect(above).toBeLessThan(inBand * 0.55);
  });

  it("is silent for silent input", () => {
    expect(peak(run(48000, new Float32Array(48000)).pcm)).toBe(0);
  });
});
