/** Microphone capture (16 kHz mono PCM16 via AudioWorklet) and permission-error mapping. */

export type MediaProblem = "permission-denied" | "no-device" | "insecure-context" | "unsupported" | "in-use" | "unknown";

export interface MediaFailure {
  problem: MediaProblem;
  message: string;
}

const MESSAGES: Record<MediaProblem, string> = {
  "permission-denied": "Microphone permission was denied. Allow microphone access in the browser's site settings, or use a scripted demo scenario instead.",
  "no-device": "No microphone was found. Connect one, or use a scripted demo scenario instead.",
  "insecure-context": "Browsers only allow microphone access on HTTPS or localhost. Open the app via http://localhost:5173 or serve it over HTTPS.",
  unsupported: "This browser does not support microphone capture (getUserMedia).",
  "in-use": "The microphone is in use by another application or could not be started.",
  unknown: "Could not start the microphone.",
};

export function mapMediaError(err: unknown): MediaFailure {
  const name = (err as { name?: string } | null)?.name ?? "";
  let problem: MediaProblem = "unknown";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") problem = "permission-denied";
  else if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") problem = "no-device";
  else if (name === "NotReadableError" || name === "AbortError") problem = "in-use";
  else if (name === "UnsupportedError") problem = "unsupported";
  return { problem, message: MESSAGES[problem] };
}

export function checkMicSupport(): MediaFailure | null {
  if (typeof window !== "undefined" && window.isSecureContext === false) {
    return { problem: "insecure-context", message: MESSAGES["insecure-context"] };
  }
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    return { problem: "unsupported", message: MESSAGES.unsupported };
  }
  return null;
}

export class MediaError extends Error {
  failure: MediaFailure;
  constructor(failure: MediaFailure) {
    super(failure.message);
    this.failure = failure;
  }
}

export interface Capture {
  stream: MediaStream;
  ctx: AudioContext;
  analyser: AnalyserNode;
  stop: () => void;
}

/** Ask for the mic (explicit browser permission prompt) and stream PCM16 chunks to `onChunk`. */
export async function startMicCapture(onChunk: (buf: ArrayBuffer) => void): Promise<Capture> {
  const unsupported = checkMicSupport();
  if (unsupported) throw new MediaError(unsupported);
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
    });
  } catch (err) {
    throw new MediaError(mapMediaError(err));
  }
  return tapStream(stream, onChunk, true);
}

/** Tap any MediaStream (mic, or the remote end of a WebRTC call) into the PCM pipeline. */
export async function tapStream(stream: MediaStream, onChunk: (buf: ArrayBuffer) => void, ownsStream = false): Promise<Capture> {
  const ctx = new AudioContext();
  try {
    if (ctx.state === "suspended") await ctx.resume();
    await ctx.audioWorklet.addModule("/pcm-worklet.js");
  } catch (err) {
    if (ownsStream) stream.getTracks().forEach((t) => t.stop());
    void ctx.close();
    throw new MediaError(mapMediaError(err));
  }
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-capture", { numberOfInputs: 1, numberOfOutputs: 0 });
  node.port.onmessage = (e) => onChunk(e.data as ArrayBuffer);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  src.connect(node);
  src.connect(analyser);
  return {
    stream,
    ctx,
    analyser,
    stop: () => {
      try {
        src.disconnect();
        node.disconnect();
      } catch {
        /* already torn down */
      }
      if (ownsStream) stream.getTracks().forEach((t) => t.stop());
      void ctx.close();
    },
  };
}
