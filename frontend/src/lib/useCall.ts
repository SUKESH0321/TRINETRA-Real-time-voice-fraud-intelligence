import { useCallback, useEffect, useRef, useState } from "react";
import type { ScenarioInfo } from "../types";
import { MediaError, startMicCapture, tapStream, type Capture } from "./media";
import { startRtc, type Role, type RtcHandle, type RtcPhase } from "./webrtc";
import type { Action } from "./store";

interface Deps {
  send: (o: Record<string, unknown>) => boolean;
  sendAudio: (b: ArrayBuffer) => void;
  dispatch: (a: Action) => void;
}

/** Owns everything that touches real devices: scenario clip playback, the microphone, and WebRTC. */
export function useCall({ send, sendAudio, dispatch }: Deps) {
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rtcPhase, setRtcPhase] = useState<RtcPhase | null>(null);
  const [rtcRole, setRtcRole] = useState<Role | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const captureRef = useRef<Capture | null>(null);
  const rtcRef = useRef<RtcHandle | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);

  const teardown = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    captureRef.current?.stop();
    captureRef.current = null;
    rtcRef.current?.close();
    rtcRef.current = null;
    if (remoteAudioRef.current) remoteAudioRef.current.srcObject = null;
    void ctxRef.current?.close().catch(() => undefined);
    ctxRef.current = null;
    setAnalyser(null);
    setRtcPhase(null);
    setRtcRole(null);
  }, []);

  useEffect(() => teardown, [teardown]);

  const fail = useCallback((code: string, message: string) => {
    setNotice(message);
    dispatch({ type: "local-error", code, message, now: performance.now() });
  }, [dispatch]);

  const connectionDown = useCallback(() => fail("not_connected", "Not connected to the TRINETRA backend. Check that it is running."), [fail]);

  /** Scripted demo: server drives events; the browser plays the bundled clip (if any) in sync. */
  const startScenario = useCallback(async (s: ScenarioInfo, speed: number) => {
    teardown();
    setNotice(null);
    if (!send({ type: "start_scenario", scenario_id: s.id, speed })) return connectionDown();
    if (!s.audio_url) return;
    try {
      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const el = new Audio(s.audio_url);
      el.playbackRate = speed;
      audioRef.current = el;
      const src = ctx.createMediaElementSource(el);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      an.connect(ctx.destination);
      setAnalyser(an);
      if (ctx.state === "suspended") await ctx.resume();
      await el.play();
    } catch {
      setNotice("Your browser blocked audio playback for the demo clip. Click the page and replay the call, or continue with the transcript only.");
    }
  }, [send, teardown, connectionDown]);

  const startMic = useCallback(async () => {
    teardown();
    setNotice(null);
    try {
      const cap = await startMicCapture(sendAudio);
      captureRef.current = cap;
      setAnalyser(cap.analyser);
      if (!send({ type: "start_live", mode: "live-mic", title: "Microphone analysis" })) {
        cap.stop();
        captureRef.current = null;
        return connectionDown();
      }
    } catch (err) {
      const f = err instanceof MediaError ? err.failure : { problem: "unknown", message: "Could not start the microphone." };
      fail(`mic_${f.problem}`, f.message);
    }
  }, [send, sendAudio, teardown, fail, connectionDown]);

  /** In-app WebRTC call. Callee analyses the remote stream; caller sends mic or a demo clip. */
  const startRtcCall = useCallback(async (role: Role, room: string, source: "mic" | "clip", clip?: ScenarioInfo) => {
    teardown();
    setNotice(null);
    setRtcRole(role);
    let local: MediaStream | undefined;
    if (role === "caller") {
      try {
        if (source === "clip" && clip?.audio_url) {
          const ctx = new AudioContext();
          ctxRef.current = ctx;
          const el = new Audio(clip.audio_url);
          audioRef.current = el;
          const dest = ctx.createMediaStreamDestination();
          ctx.createMediaElementSource(el).connect(dest); // not connected to speakers: no echo on this tab
          local = dest.stream;
          el.addEventListener("ended", () => setNotice("Demo clip finished."));
          // start the clip only once the callee is connected
        } else {
          const cap = await startMicCapture(() => undefined);
          captureRef.current = cap;
          local = cap.stream;
        }
      } catch (err) {
        const f = err instanceof MediaError ? err.failure : { problem: "unknown", message: "Could not start the microphone." };
        setRtcRole(null);
        return fail(`mic_${f.problem}`, f.message);
      }
    }
    rtcRef.current = startRtc({
      role, room, localStream: local,
      onPhase: (p) => {
        setRtcPhase(p);
        if (p === "connected" && role === "caller" && audioRef.current) {
          void ctxRef.current?.resume();
          void audioRef.current.play().catch(() => setNotice("Audio playback was blocked by the browser. Click the page, then reconnect."));
        }
        if (p === "peer-left" && role === "callee") send({ type: "stop" });
      },
      onRemoteStream: async (stream) => {
        if (role !== "callee") return;
        if (!remoteAudioRef.current) remoteAudioRef.current = new Audio();
        remoteAudioRef.current.srcObject = stream;
        void remoteAudioRef.current.play().catch(() => setNotice("The browser blocked playback of the caller's audio. Analysis continues; click the page to hear it."));
        if (!send({ type: "start_live", mode: "webrtc", title: "In-app WebRTC call" })) return connectionDown();
        try {
          const cap = await tapStream(stream, sendAudio, false);
          captureRef.current = cap;
          setAnalyser(cap.analyser);
        } catch (err) {
          const f = err instanceof MediaError ? err.failure : { message: "Could not analyse the call audio." };
          fail("rtc_tap_failed", f.message);
        }
      },
    });
  }, [teardown, send, sendAudio, fail, connectionDown]);

  const stop = useCallback(() => {
    send({ type: "stop" });
    teardown();
  }, [send, teardown]);

  return { analyser, notice, setNotice, rtcPhase, rtcRole, startScenario, startMic, startRtcCall, stop, release: teardown };
}
