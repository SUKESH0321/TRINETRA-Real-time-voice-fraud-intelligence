import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WarningCard } from "../components/WarningCard";
import type { Alert } from "../types";

const base: Alert = {
  id: "a1", t: 18, level: "high", kind: "warning", headline: "POSSIBLE PHONE SCAM",
  reason: "The caller used a request for a one-time code, PIN or password (at 00:18).", reasons: [], trigger: "new",
  evidence: {
    transcript: "Please read the code you just received so I can secure your account.",
    matched: ["read", "code you just received"], spans: [[7, 11], [16, 39]], detected_at: 18,
    conversation: [{ kind: "conversation", label: "Request for a one-time code", t: 18, score: 0.67, source: "scripted",
      method: "rule-based:credential_request", excerpt: "Please read the code you just received so I can secure your account.", spans: [[7, 11]] }],
    voice: [],
  },
  conversation_risk: "high", voice_authenticity: "Inconclusive (no detector running)",
  recommended_action: "Do not share the code. Contact your bank using its official app or published number.",
  uncertainty: ["Voice detectors are unavailable."], sound: "high", vibrate: [200], quiet_mode: false,
};
const status = { audio: "ready", vibration: "supported", muted: false, vibrationEnabled: true } as const;
const noop = () => undefined;

describe("WarningCard", () => {
  it("shows reason, transcript evidence, time, conversation risk, voice authenticity and the action", () => {
    render(<WarningCard alert={base} status={status} onDismiss={noop} onMute={noop} />);
    expect(screen.getByText("POSSIBLE PHONE SCAM")).toBeInTheDocument();
    expect(screen.getByText(/one-time code, PIN or password/)).toBeInTheDocument();
    expect(screen.getByText("00:18")).toBeInTheDocument();
    expect(screen.getByText("Inconclusive (no detector running)")).toBeInTheDocument();
    expect(screen.getByText(/Do not share the code/)).toBeInTheDocument();
  });

  it("never claims fraud is confirmed", () => {
    const { container } = render(<WarningCard alert={base} status={status} onDismiss={noop} onMute={noop} />);
    expect(container.textContent?.toLowerCase()).not.toContain("fraud confirmed");
    expect(container.textContent).toMatch(/not proof of fraud/i);
  });

  it("buttons: I understand dismisses, View evidence toggles, Mute mutes", () => {
    const onDismiss = vi.fn();
    const onMute = vi.fn();
    render(<WarningCard alert={base} status={status} onDismiss={onDismiss} onMute={onMute} />);
    fireEvent.click(screen.getByText("I understand"));
    expect(onDismiss).toHaveBeenCalled();
    fireEvent.click(screen.getByText("Mute alert sound"));
    expect(onMute).toHaveBeenCalled();
    expect(screen.queryByTestId("evidence-detail")).toBeNull();
    fireEvent.click(screen.getByText("View evidence"));
    expect(screen.getByTestId("evidence-detail")).toBeInTheDocument();
    expect(screen.getByText(/Voice detectors are unavailable/)).toBeInTheDocument();
  });

  it("states honestly when sound is muted or vibration is unsupported", () => {
    render(<WarningCard alert={base} status={{ ...status, muted: true, vibration: "unsupported" }} onDismiss={noop} onMute={noop} />);
    expect(screen.getByText(/Sound muted/)).toBeInTheDocument();
    expect(screen.queryByText(/vibration sent/)).toBeNull();
    expect(screen.queryByText("Mute alert sound")).toBeNull(); // already muted
  });

  it("critical alert carries the critical level marker", () => {
    render(<WarningCard alert={{ ...base, level: "critical", headline: "LIKELY SCAM - ACT NOW", sound: "critical" }} status={status} onDismiss={noop} onMute={noop} />);
    expect(screen.getByTestId("warning-card")).toHaveAttribute("data-level", "critical");
  });
});
