import { useCallback, useEffect, useRef, useState } from "react";
import type { Capabilities } from "../types";

export interface Health {
  ok: boolean;
  detectors: Capabilities["detectors"];
  transcription: Capabilities["transcription"];
}

/**
 * Polls /api/health so the UI always shows the model's real state: it follows "loading" until the speech
 * model is ready, notices when it becomes ready after `npm run setup:asr`, and exposes Retry.
 */
export function useHealth(connected: boolean) {
  const [health, setHealth] = useState<Health | null>(null);
  const [failed, setFailed] = useState(false);
  const latest = useRef<Health | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/health");
      if (!r.ok) throw new Error(String(r.status));
      const h: Health = await r.json();
      latest.current = h;
      setHealth(h);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      await refresh();
      if (!alive) return;
      const st = latest.current?.transcription.state;
      // fast while loading or failing; slow once settled (it can still change if the model is installed later)
      timer.current = setTimeout(tick, st === "loading" || st === "idle" || !latest.current ? 1200 : 6000);
    };
    void tick();
    return () => { alive = false; clearTimeout(timer.current); };
  }, [refresh, connected]);

  const retryTranscription = useCallback(async () => {
    try {
      await fetch("/api/transcription/retry", { method: "POST" });
    } catch {
      /* the next poll reports the failure */
    }
    await refresh();
  }, [refresh]);

  return { health, failed, retryTranscription };
}
