import { describe, expect, it, vi } from "vitest";
import { AlertEngine } from "../lib/alerts";

function fakeCtx(state: "running" | "suspended", resumeTo: "running" | "suspended" | "throw" = "running") {
  const osc = () => ({ type: "", frequency: { setValueAtTime: vi.fn() }, connect: (n: unknown) => n, start: vi.fn(), stop: vi.fn() });
  const gain = () => ({ gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() }, connect: (n: unknown) => n });
  const ctx = {
    state,
    currentTime: 0,
    destination: {},
    resume: vi.fn(async () => {
      if (resumeTo === "throw") throw new Error("NotAllowedError");
      ctx.state = resumeTo;
    }),
    createOscillator: vi.fn(osc),
    createGain: vi.fn(gain),
  };
  return ctx as unknown as AudioContext & { createOscillator: ReturnType<typeof vi.fn> };
}

describe("AlertEngine", () => {
  it("reports audio ready after a successful enable (user gesture)", async () => {
    const e = new AlertEngine(() => fakeCtx("suspended", "running"), { vibrate: () => true });
    expect(e.status.audio).toBe("off");
    expect(await e.enable()).toBe("ready");
    expect(e.status.audio).toBe("ready");
  });

  it("reports blocked (not an exception) when the browser refuses to start audio", async () => {
    const e = new AlertEngine(() => fakeCtx("suspended", "suspended"), {});
    expect(await e.enable()).toBe("blocked");
    expect(await e.playSound("high")).toBe(false);
    expect(e.status.audio).toBe("blocked");
  });

  it("reports blocked when resume() rejects", async () => {
    const e = new AlertEngine(() => fakeCtx("suspended", "throw"), {});
    expect(await e.enable()).toBe("blocked");
  });

  it("reports unsupported when Web Audio is missing", async () => {
    const e = new AlertEngine(() => null, {});
    expect(await e.enable()).toBe("unsupported");
    expect(await e.playSound("critical")).toBe(false);
  });

  it("plays a different tone for critical than for high", async () => {
    const ctx = fakeCtx("running");
    const e = new AlertEngine(() => ctx, {});
    await e.enable();
    await e.playSound("high");
    const high = ctx.createOscillator.mock.calls.length;
    await e.playSound("critical");
    expect(ctx.createOscillator.mock.calls.length - high).toBeGreaterThan(high);
  });

  it("does not play when muted but still vibrates", async () => {
    const vibrate = vi.fn(() => true);
    const ctx = fakeCtx("running");
    const e = new AlertEngine(() => ctx, { vibrate });
    await e.enable();
    e.setMuted(true);
    await e.notify({ sound: "high", vibrate: [200] });
    expect(ctx.createOscillator).not.toHaveBeenCalled();
    expect(vibrate).toHaveBeenCalledWith([200]);
  });

  it("handles unsupported vibration gracefully and says so", async () => {
    const ctx = fakeCtx("running");
    const e = new AlertEngine(() => ctx, {}); // no navigator.vibrate
    expect(e.status.vibration).toBe("unsupported");
    await e.enable();
    await expect(e.notify({ sound: "critical", vibrate: [300, 100, 300] })).resolves.toBeUndefined();
    expect(ctx.createOscillator).toHaveBeenCalled(); // sound still plays
    expect(await e.test("high")).toEqual({ sound: true, vibration: false });
  });

  it("respects the vibration toggle", () => {
    const vibrate = vi.fn(() => true);
    const e = new AlertEngine(() => fakeCtx("running"), { vibrate });
    e.setVibrationEnabled(false);
    expect(e.vibrate([200])).toBe(false);
    expect(vibrate).not.toHaveBeenCalled();
  });

  it("an alert without sound or vibration (elevated / quiet mode) produces no output", async () => {
    const vibrate = vi.fn(() => true);
    const ctx = fakeCtx("running");
    const e = new AlertEngine(() => ctx, { vibrate });
    await e.enable();
    await e.notify({ sound: null, vibrate: null });
    expect(ctx.createOscillator).not.toHaveBeenCalled();
    expect(vibrate).not.toHaveBeenCalled();
  });

  it("notifies subscribers when status changes", async () => {
    const e = new AlertEngine(() => fakeCtx("running"), {});
    const fn = vi.fn();
    e.subscribe(fn);
    await e.enable();
    expect(fn).toHaveBeenCalled();
  });
});
