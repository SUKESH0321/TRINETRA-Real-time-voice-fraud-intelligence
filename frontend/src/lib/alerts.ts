/**
 * Alert output: synthesized sound (Web Audio), vibration, and honest status reporting.
 *
 * Browsers only allow audio after a user gesture, so `enable()` must be called from a click.
 * Every failure mode (audio blocked/unsupported, vibration unsupported) is reported as a status
 * instead of throwing, and visual warnings always continue to work.
 */
import type { Alert } from "../types";

export type AudioStatus = "off" | "ready" | "blocked" | "unsupported";
export type VibrationStatus = "supported" | "unsupported";

export interface AlertStatus {
  audio: AudioStatus;
  vibration: VibrationStatus;
  muted: boolean;
  vibrationEnabled: boolean;
}

type Ctx = AudioContext;
type CtxFactory = () => Ctx | null;

const defaultFactory: CtxFactory = () => {
  const AC: typeof AudioContext | undefined =
    typeof window !== "undefined"
      ? window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      : undefined;
  return AC ? new AC() : null;
};

export class AlertEngine {
  private ctx: Ctx | null = null;
  private listeners = new Set<() => void>();
  status: AlertStatus;
  private factory: CtxFactory;
  private nav: { vibrate?: (p: number | number[]) => boolean };

  constructor(factory: CtxFactory = defaultFactory, nav?: { vibrate?: (p: number | number[]) => boolean }) {
    this.factory = factory;
    this.nav = nav ?? (typeof navigator !== "undefined" ? (navigator as never) : {});
    this.status = {
      audio: "off",
      vibration: typeof this.nav.vibrate === "function" ? "supported" : "unsupported",
      muted: false,
      vibrationEnabled: true,
    };
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private set(patch: Partial<AlertStatus>) {
    this.status = { ...this.status, ...patch };
    this.listeners.forEach((l) => l());
  }

  /** Call from a click handler. Resolves with the resulting audio status. */
  async enable(): Promise<AudioStatus> {
    try {
      if (!this.ctx) this.ctx = this.factory();
      if (!this.ctx) {
        this.set({ audio: "unsupported" });
        return "unsupported";
      }
      if (this.ctx.state === "suspended") await this.ctx.resume();
      const ok = this.ctx.state === "running";
      this.set({ audio: ok ? "ready" : "blocked" });
      return ok ? "ready" : "blocked";
    } catch {
      this.set({ audio: "blocked" });
      return "blocked";
    }
  }

  setMuted(muted: boolean) {
    this.set({ muted });
  }

  setVibrationEnabled(on: boolean) {
    this.set({ vibrationEnabled: on });
  }

  /** Sound + vibration for an alert, honouring mute/quiet-mode flags that the server already applied. */
  async notify(alert: Pick<Alert, "sound" | "vibrate">): Promise<void> {
    if (alert.sound) await this.playSound(alert.sound);
    if (alert.vibrate) this.vibrate(alert.vibrate);
  }

  async playSound(kind: "high" | "critical"): Promise<boolean> {
    if (this.status.muted) return false;
    if (!this.ctx || this.status.audio === "unsupported") return false;
    try {
      if (this.ctx.state === "suspended") await this.ctx.resume();
      if (this.ctx.state !== "running") {
        this.set({ audio: "blocked" });
        return false;
      }
      if (this.status.audio !== "ready") this.set({ audio: "ready" });
      kind === "critical" ? this.criticalTone(this.ctx) : this.highTone(this.ctx);
      return true;
    } catch {
      this.set({ audio: "blocked" });
      return false;
    }
  }

  vibrate(pattern: number[]): boolean {
    if (this.status.vibration !== "supported" || !this.status.vibrationEnabled) return false;
    try {
      return !!this.nav.vibrate?.(pattern);
    } catch {
      return false;
    }
  }

  /** "Test alert" button: plays the sound and a short vibration regardless of an active call. */
  async test(kind: "high" | "critical"): Promise<{ sound: boolean; vibration: boolean }> {
    const sound = await this.playSound(kind);
    const vibration = this.vibrate(kind === "critical" ? [300, 120, 300, 120, 500] : [200]);
    return { sound, vibration };
  }

  private tone(ctx: Ctx, freq: number, start: number, dur: number, type: OscillatorType, gain: number) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, start);
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(gain, start + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
    osc.connect(g).connect(ctx.destination);
    osc.start(start);
    osc.stop(start + dur + 0.02);
  }

  /** High: a short two-note ping. */
  private highTone(ctx: Ctx) {
    const t = ctx.currentTime + 0.02;
    this.tone(ctx, 880, t, 0.16, "sine", 0.28);
    this.tone(ctx, 1318, t + 0.17, 0.24, "sine", 0.28);
  }

  /** Critical: an urgent alternating siren, clearly different from the high ping. */
  private criticalTone(ctx: Ctx) {
    const t = ctx.currentTime + 0.02;
    for (let i = 0; i < 6; i++) this.tone(ctx, i % 2 ? 740 : 1046, t + i * 0.2, 0.18, "square", 0.16);
  }
}
