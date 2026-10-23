import { describe, expect, it } from "vitest";
import { fmtClock, initialState, reducer } from "../lib/store";
import type { Alert, ServerMsg } from "../types";

const alert = (level: Alert["level"], kind: Alert["kind"]): Alert => ({
  id: "a1", t: 18, level, kind, headline: "H", reason: "R", reasons: ["R"], trigger: "new",
  evidence: { transcript: "x", matched: [], spans: [], detected_at: 18, conversation: [], voice: [] },
  conversation_risk: level, voice_authenticity: "Inconclusive", recommended_action: "A", uncertainty: [],
  sound: kind === "warning" ? "high" : null, vibrate: null, quiet_mode: false,
});
const msg = (s: typeof initialState, m: ServerMsg, now = 1000) => reducer(s, { type: "msg", msg: m, now });

describe("session reducer", () => {
  it("high/critical alerts open the warning card; elevated only sets the indicator", () => {
    let s = msg(initialState, { type: "alert", alert: alert("elevated", "indicator") });
    expect(s.activeAlert).toBeNull();
    expect(s.indicator?.level).toBe("elevated");
    s = msg(s, { type: "alert", alert: alert("high", "warning") });
    expect(s.activeAlert?.level).toBe("high");
    expect(s.indicator).toBeNull();
  });

  it("a later elevated alert does not replace a visible warning card", () => {
    let s = msg(initialState, { type: "alert", alert: alert("high", "warning") });
    s = msg(s, { type: "alert", alert: alert("elevated", "indicator") });
    expect(s.activeAlert?.level).toBe("high");
  });

  it("dismiss keeps the alert in history", () => {
    let s = msg(initialState, { type: "alert", alert: alert("high", "warning") });
    s = reducer(s, { type: "dismiss-alert" });
    expect(s.activeAlert).toBeNull();
    expect(s.alerts).toHaveLength(1);
  });

  it("starting a new call clears the previous call's state", () => {
    let s = msg(initialState, { type: "alert", alert: alert("high", "warning") });
    s = msg(s, { type: "timeline", entry: { id: "e1", t: 0, clock: "00:00", kind: "call", level: "low", title: "t", detail: "", source: "real", method: "" } });
    s = msg(s, { type: "call_state", state: "running", mode: "scenario", t: 0, info: { speed: 2 } }, 5000);
    expect(s.alerts).toHaveLength(0);
    expect(s.timeline).toHaveLength(0);
    expect(s.activeAlert).toBeNull();
    expect(s.call.speed).toBe(2);
    expect(s.call.startedAt).toBe(5000);
  });

  it("server errors are surfaced, capped to the last five", () => {
    let s = initialState;
    for (let i = 0; i < 8; i++) s = msg(s, { type: "error", code: "c" + i, message: "m" + i });
    expect(s.errors).toHaveLength(5);
    expect(s.errors[4].code).toBe("c7");
  });

  it("formats clocks", () => {
    expect(fmtClock(18)).toBe("00:18");
    expect(fmtClock(75.4)).toBe("01:15");
  });
});
