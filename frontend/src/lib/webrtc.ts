/**
 * Minimal two-party WebRTC call. Media flows peer to peer; the backend only relays signaling
 * (/ws/signal/{room}). The CALLEE tap the remote stream and sends it to the analysis session, so
 * what is analysed is exactly the audio belonging to this call, never system audio.
 * No STUN/TURN is configured: it works on localhost and the same LAN. Across networks you would add servers.
 */
export type Role = "caller" | "callee";
export type RtcPhase = "signaling" | "waiting-for-peer" | "negotiating" | "connected" | "peer-left" | "failed" | "closed" | "room-full";

export interface RtcHandle {
  close: () => void;
}

export interface RtcOptions {
  role: Role;
  room: string;
  localStream?: MediaStream; // caller only
  onRemoteStream?: (s: MediaStream) => void; // callee
  onPhase: (p: RtcPhase) => void;
}

export function startRtc(opts: RtcOptions): RtcHandle {
  const { role, room, localStream, onRemoteStream, onPhase } = opts;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/signal/${encodeURIComponent(room)}`);
  let pc: RTCPeerConnection | null = null;
  let closed = false;
  let peers = 1;
  const pendingIce: RTCIceCandidateInit[] = [];

  const send = (o: unknown) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(o));

  const makePc = () => {
    const p = new RTCPeerConnection({ iceServers: [] });
    p.onicecandidate = (e) => e.candidate && send({ type: "ice", candidate: e.candidate });
    p.onconnectionstatechange = () => {
      if (closed) return;
      if (p.connectionState === "connected") onPhase("connected");
      else if (p.connectionState === "failed") onPhase("failed");
    };
    p.ontrack = (e) => onRemoteStream?.(e.streams[0] ?? new MediaStream([e.track]));
    localStream?.getTracks().forEach((t) => p.addTrack(t, localStream));
    pc = p;
    return p;
  };

  const callerOffer = async () => {
    if (role !== "caller" || pc) return;
    onPhase("negotiating");
    const p = makePc();
    await p.setLocalDescription(await p.createOffer());
    send({ type: "offer", sdp: p.localDescription });
  };

  const flushIce = async () => {
    while (pendingIce.length && pc?.remoteDescription) await pc.addIceCandidate(pendingIce.shift()!);
  };

  ws.onmessage = async (ev) => {
    const m = JSON.parse(ev.data as string);
    try {
      if (m.type === "room_full") return onPhase("room-full");
      if (m.type === "joined") {
        peers = m.peers;
        onPhase(peers >= 2 ? "negotiating" : "waiting-for-peer");
        if (peers >= 2) await callerOffer();
      } else if (m.type === "peer_joined") {
        peers = m.peers;
        await callerOffer();
      } else if (m.type === "peer_left") {
        onPhase("peer-left");
      } else if (m.type === "offer" && role === "callee") {
        onPhase("negotiating");
        const p = pc ?? makePc();
        await p.setRemoteDescription(m.sdp);
        await flushIce();
        await p.setLocalDescription(await p.createAnswer());
        send({ type: "answer", sdp: p.localDescription });
      } else if (m.type === "answer" && role === "caller" && pc) {
        await pc.setRemoteDescription(m.sdp);
        await flushIce();
      } else if (m.type === "ice") {
        if (pc?.remoteDescription) await pc.addIceCandidate(m.candidate);
        else pendingIce.push(m.candidate);
      }
    } catch {
      if (!closed) onPhase("failed");
    }
  };
  ws.onerror = () => !closed && onPhase("failed");
  ws.onclose = () => !closed && onPhase("closed");
  onPhase("signaling");

  return {
    close: () => {
      closed = true;
      pc?.close();
      ws.close();
      onPhase("closed");
    },
  };
}
