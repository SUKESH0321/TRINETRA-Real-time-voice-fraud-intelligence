import { useState } from "react";
import type { AlertEngine, AlertStatus } from "../lib/alerts";
import type { Thresholds } from "./Panels";
import { Btn, Card, Toggle } from "./ui";

interface Props {
  engine: AlertEngine;
  status: AlertStatus;
  quiet: boolean;
  onQuiet: (v: boolean) => void;
  thresholds: Thresholds;
  rules: { alerts?: { cooldown_s?: number; global_min_gap_s?: number; escalation_delta?: number } } | null;
}

export function AlertSettings({ engine, status, quiet, onQuiet, thresholds, rules }: Props) {
  const [testNote, setTestNote] = useState("");
  const audioText = { off: "Sound off", ready: "Sound ready", blocked: "Sound blocked by browser", unsupported: "Sound unsupported" }[status.audio];
  const vibText = status.vibration === "supported" ? "Vibration supported" : "Vibration unsupported";

  const test = async (kind: "high" | "critical") => {
    if (status.audio !== "ready") await engine.enable();
    const r = await engine.test(kind);
    setTestNote(
      [r.sound ? `Played the ${kind} sound.` : status.muted ? "Sound is muted." : "Could not play sound (browser blocked or unsupported).",
        r.vibration ? "Vibrated." : status.vibration === "unsupported" ? "Vibration is not supported on this device." : "Vibration is off."].join(" "),
    );
  };

  return (
    <Card title="Alert settings" right={<span>{audioText} · {vibText}</span>}>
      <Toggle checked={!status.muted} onChange={(on) => engine.setMuted(!on)} label="Alert sounds" hint="Mute to keep visual warnings only." />
      <Toggle checked={status.vibrationEnabled && status.vibration === "supported"} onChange={(on) => engine.setVibrationEnabled(on)}
        label="Vibration" hint={status.vibration === "supported" ? "Used for High and Critical alerts." : "Not supported by this browser or device."} />
      <Toggle checked={quiet} onChange={onQuiet} label="Quiet mode" hint="Only Critical alerts make sound or vibrate. Warnings stay visible." />
      <div className="mt-3 flex flex-wrap gap-2">
        {status.audio !== "ready" && <Btn variant="primary" onClick={() => void engine.enable()}>Enable alerts</Btn>}
        <Btn onClick={() => void test("high")}>Test High alert</Btn>
        <Btn onClick={() => void test("critical")}>Test Critical alert</Btn>
      </div>
      {testNote && <p className="mt-3 text-[13px] text-ink-2" role="status">{testNote}</p>}
      {status.audio === "blocked" && <p className="mt-3 text-[13px] text-amber">Your browser blocked audio. Click “Enable alerts” (a user action is required). Visual warnings still work.</p>}
      <details className="mt-4 text-[13px] text-ink-2">
        <summary><span className="eyebrow"><span className="caret" />Anti-spam rules &amp; thresholds</span></summary>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>Levels: elevated ≥ {thresholds.elevated}, high ≥ {thresholds.high}, critical ≥ {thresholds.critical}</li>
          <li>Per-incident cooldown {rules?.alerts?.cooldown_s ?? "…"} s · minimum gap between pings {rules?.alerts?.global_min_gap_s ?? "…"} s</li>
          <li>Escalation (bypasses cooldown): a higher level, or score up by {rules?.alerts?.escalation_delta ?? "…"}</li>
          <li>The same phrase repeated is a duplicate and raises no new alert.</li>
          <li>Source: <span className="mono">config/risk_rules.yaml</span></li>
        </ul>
      </details>
    </Card>
  );
}
