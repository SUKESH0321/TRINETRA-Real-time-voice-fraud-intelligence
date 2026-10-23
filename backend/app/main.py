"""TRINETRA backend: FastAPI app + WebSockets.

REST
  GET  /api/health                 liveness + capability summary
  GET  /api/scenarios              scenario list (with expected outcomes and injected evidence)
  GET  /api/scenarios/{id}/audio   bundled demo clip (WAV), if any
  GET  /api/rules                  the active risk rules (thresholds are inspectable)
WebSocket
  /ws/session                      one analysis session (scripted scenario, live mic, or WebRTC callee)
  /ws/signal/{room}                WebRTC signaling relay for the in-app two-party call
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scenarios as sc
from .config import load_rules
from .models import SRC_MANUAL
from .session import CallSession
from .transcription import Transcriber, make_transcriber
from .voice_analysis import DetectorRegistry, default_registry
from .websocket_manager import SignalingRooms, SocketSender

log = logging.getLogger("trinetra")


def create_app(
    rules: dict[str, Any] | None = None,
    registry: DetectorRegistry | None = None,
    transcriber: Transcriber | None = None,
    scenario_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="TRINETRA", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    rules = rules or load_rules()
    scenarios = sc.load_scenarios(scenario_dir)
    rooms = SignalingRooms()
    state: dict[str, Any] = {"registry": registry, "transcriber": transcriber}

    def get_registry() -> DetectorRegistry:
        if state["registry"] is None:
            state["registry"] = default_registry()
        return state["registry"]

    def get_transcriber() -> Transcriber:
        if state["transcriber"] is None:
            state["transcriber"] = make_transcriber()
        return state["transcriber"]

    # ---- REST ----------------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "detectors": get_registry().status(), "transcription": get_transcriber().status()}

    @app.get("/api/scenarios")
    def list_scenarios() -> list[dict[str, Any]]:
        return [s.public() for s in scenarios.values()]

    @app.get("/api/scenarios/{sid}/audio")
    def scenario_audio(sid: str):
        s = scenarios.get(sid)
        if not s or not s.audio_path:
            raise HTTPException(404, "No audio for this scenario")
        return FileResponse(s.audio_path, media_type="audio/wav")

    @app.get("/api/rules")
    def get_rules() -> dict[str, Any]:
        return rules

    # ---- analysis session ----------------------------------------------------------------
    @app.websocket("/ws/session")
    async def ws_session(ws: WebSocket) -> None:
        await ws.accept()
        send = SocketSender(ws)
        session = CallSession(rules, get_registry(), get_transcriber())
        play_task: asyncio.Task | None = None
        audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        audio_task: asyncio.Task | None = None
        clock = {"t0": 0.0}

        def now() -> float:
            return round(time.monotonic() - clock["t0"], 2) if session.mode in ("live-mic", "webrtc") else session.t

        async def emit(msgs: list[dict[str, Any]]) -> None:
            for m in msgs:
                await send(m)

        async def stop_all() -> None:
            nonlocal play_task, audio_task
            for task in (play_task, audio_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
            play_task = audio_task = None
            while not audio_q.empty():
                audio_q.get_nowait()

        async def audio_worker() -> None:
            while True:
                data = await audio_q.get()
                msgs = await asyncio.to_thread(session.ingest_audio_chunk, data)
                await emit(msgs)

        await send({"type": "hello", "capabilities": session.capabilities(),
                    "scenarios": [s.public() for s in scenarios.values()]})
        try:
            while True:
                packet = await ws.receive()
                if packet.get("type") == "websocket.disconnect":
                    break
                if packet.get("bytes") is not None:
                    if session.state == "running" and session.mode in ("live-mic", "webrtc"):
                        if audio_q.full():
                            audio_q.get_nowait()  # keep it real-time: drop the oldest chunk
                        audio_q.put_nowait(packet["bytes"])
                    continue
                try:
                    msg = json.loads(packet.get("text") or "{}")
                except json.JSONDecodeError:
                    await send({"type": "error", "code": "bad_json", "message": "Message was not valid JSON."})
                    continue
                kind = msg.get("type")

                if kind == "settings":
                    session.set_quiet(bool(msg.get("quiet_mode")))
                    await send({"type": "settings", "quiet_mode": session.alerts.quiet_mode})

                elif kind == "start_scenario":
                    s = scenarios.get(msg.get("scenario_id", ""))
                    if not s:
                        await send({"type": "error", "code": "unknown_scenario", "message": "Unknown scenario."})
                        continue
                    await stop_all()
                    play_task = asyncio.create_task(sc.play(session, s, send, float(msg.get("speed", 1.0))))

                elif kind == "start_live":
                    mode = msg.get("mode", "live-mic")
                    if mode not in ("live-mic", "webrtc"):
                        await send({"type": "error", "code": "bad_mode", "message": "Unknown live mode."})
                        continue
                    await stop_all()
                    clock["t0"] = time.monotonic()
                    await emit(session.start(mode, {"title": msg.get("title", "")}))
                    audio_task = asyncio.create_task(audio_worker())

                elif kind == "manual_transcript":
                    if session.state != "running":
                        await send({"type": "error", "code": "no_call", "message": "Start a call before adding transcript text."})
                        continue
                    await emit(session.ingest_transcript(now(), msg.get("speaker", "caller"), str(msg.get("text", "")),
                                                         SRC_MANUAL, "manual-input"))

                elif kind == "stop":
                    await stop_all()
                    await emit(session.end(now()))

                elif kind == "ping":
                    await send({"type": "pong"})

                else:
                    await send({"type": "error", "code": "unknown_message", "message": f"Unknown message type: {kind!r}"})
        except WebSocketDisconnect:
            pass
        except RuntimeError:
            pass  # receive() after disconnect
        finally:
            send.open = False
            await stop_all()

    # ---- WebRTC signaling ----------------------------------------------------------------
    @app.websocket("/ws/signal/{room}")
    async def ws_signal(ws: WebSocket, room: str) -> None:
        await ws.accept()
        peer_id = f"p{id(ws)}"
        if not rooms.join(room, peer_id, ws):
            await ws.send_text(json.dumps({"type": "room_full"}))
            await ws.close()
            return
        await ws.send_text(json.dumps({"type": "joined", "peer": peer_id, "peers": rooms.count(room)}))
        for other in rooms.others(room, peer_id):
            await other.send_text(json.dumps({"type": "peer_joined", "peers": rooms.count(room)}))
        try:
            while True:
                text = await ws.receive_text()
                for other in rooms.others(room, peer_id):
                    await other.send_text(text)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rooms.leave(room, peer_id)
            for other in rooms.others(room, peer_id):
                try:
                    await other.send_text(json.dumps({"type": "peer_left", "peers": rooms.count(room)}))
                except Exception:  # noqa: BLE001
                    pass

    # ---- built frontend (production-style single command) --------------------------------
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app


app = create_app()
