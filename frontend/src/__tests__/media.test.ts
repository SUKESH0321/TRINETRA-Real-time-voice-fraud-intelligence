import { afterEach, describe, expect, it, vi } from "vitest";
import { checkMicSupport, mapMediaError, MediaError, startMicCapture } from "../lib/media";

afterEach(() => vi.unstubAllGlobals());

describe("microphone permission handling", () => {
  it("maps a denied permission to a clear message", () => {
    const f = mapMediaError(Object.assign(new Error("x"), { name: "NotAllowedError" }));
    expect(f.problem).toBe("permission-denied");
    expect(f.message).toMatch(/denied/i);
    expect(f.message).toMatch(/scripted demo/i); // points the user to the fallback
  });

  it.each([
    ["NotFoundError", "no-device"],
    ["NotReadableError", "in-use"],
    ["SomethingElse", "unknown"],
  ])("maps %s to %s", (name, problem) => {
    expect(mapMediaError({ name }).problem).toBe(problem);
  });

  it("startMicCapture rejects with a MediaError when the user denies permission", async () => {
    const denied = Object.assign(new Error("denied"), { name: "NotAllowedError" });
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(denied) } });
    await expect(startMicCapture(() => undefined)).rejects.toBeInstanceOf(MediaError);
    await expect(startMicCapture(() => undefined)).rejects.toMatchObject({ failure: { problem: "permission-denied" } });
  });

  it("detects a missing getUserMedia", () => {
    vi.stubGlobal("navigator", {});
    expect(checkMicSupport()?.problem).toBe("unsupported");
  });

  it("detects an insecure context", () => {
    vi.stubGlobal("isSecureContext", false);
    expect(checkMicSupport()?.problem).toBe("insecure-context");
  });
});
