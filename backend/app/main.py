"""TRINETRA backend: FastAPI app + WebSockets.

REST
  GET  /api/health                    liveness + detector and transcription status (accurate, actionable)
  POST /api/transcription/retry       try loading the speech model again (e.g. after `npm run setup:asr`)
  GET  /api/scenarios                 scenario list (with expected outcomes and injected evidence)
  GET  /api/scenarios/{id}/audio      bundled demo clip (WAV), if any
  GET  /api/rules                     the active risk rules (thresholds are inspectable)
WebSocket
  /ws/session                         one analysis session (scripted scenario, live mic, or WebRTC callee)
  /ws/signal/{room}                   WebRTC signaling relay for the in-app two-party call
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
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
from .transcription import Transcriber, TranscriptionService
from .voice_analysis import DetectorRegistry, default_registry
from .websocket_manager import SignalingRooms, SocketSender

log = logging.getLogger("trinetra")
MAX_BACKLOG_S = 300  # live audio queued for processing is capped at five minutes of audio


def _as_service(transcriber: Transcriber | TranscriptionService | None) -> TranscriptionService:
    if transcriber is None:
        return TranscriptionService.from_env()
    if isinstance(transcriber, TranscriptionService):
        return transcriber
    return TranscriptionService.static(transcriber)


def create_app(
    rules: dict[str, Any] | None = None,
    registry: DetectorRegistry | None = None,
    transcriber: Transcriber | TranscriptionService | None = None,
    scenario_dir: Path | None = None,
) -> FastAPI:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s: %(message)s")
    state: dict[str, Any] = {"registry": registry}
    service = _as_service(transcriber)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Controlled startup: load the detectors once (off the event loop), and start the speech model in the
        # background so the server is reachable immediately and reports "loading" honestly until it is ready.
        if state["registry"] is None:
            state["registry"] = await asyncio.to_thread(default_registry)
        service.start()
        st = service.status()
        log.info("transcription: %s (%s)", st["state"], st["detail"])
        yield

    app = FastAPI(title="TRINETRA", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    rules = rules or load_rules()
    scenarios = sc.load_scenarios(scenario_dir)
    rooms = SignalingRooms()

    def get_registry() -> DetectorRegistry:
        if state["registry"] is None:  # only reachable when the app is used without lifespan (e.g. some tests)
            state["registry"] = default_registry()
        return state["registry"]

    # ---- REST ----------------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "detectors": get_registry().status(), "transcription": service.status()}

    @app.post("/api/transcription/retry")
    def retry_transcription() -> dict[str, Any]:
        return service.retry()

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
        session = CallSession(rules, get_registry(), service)
        play_task: asyncio.Task | None = None
        audio_task: asyncio.Task | None = None
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()  # unbounded by count; bounded by seconds below
        backlog = {"bytes": 0}

        async def emit(msgs: list[dict[str, Any]]) -> None:
            for m in msgs:
                await send(m)

        async def cancel(task: asyncio.Task | None) -> None:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

        async def audio_worker() -> None:
            """Single consumer: batches whatever has arrived, never drops audio, flushes on the stop sentinel."""
            finish = False
            while not finish:
                item = await audio_q.get()
                batch: list[bytes] = []
                if item is None:
                    finish = True
                else:
                    batch.append(item)
                while not finish and not audio_q.empty():  # coalesce when processing is behind
                    nxt = audio_q.get_nowait()
                    if nxt is None:
                        finish = True
                    else:
                        batch.append(nxt)
                if batch:
                    backlog["bytes"] -= sum(len(b) for b in batch)
                    await emit(session.ingest_audio_chunks(batch, process=False))  # fast: decode + buffer, never drops audio
                    await emit(session.begin_process())
                    # Speech recognition and the voice detectors are independent, so they run in parallel and
                    # a slow detector can no longer delay transcripts. Results are applied on this one task.
                    voice, asr = await asyncio.gather(asyncio.to_thread(session.compute_voice), asyncio.to_thread(session.compute_asr))
                    await emit(session.apply_results(voice, asr))
            await emit(await asyncio.to_thread(session.end))  # decodes the remaining buffer, then closes the call

        async def stop_live() -> None:
            nonlocal audio_task
            if audio_task and not audio_task.done():
                audio_q.put_nowait(None)  # sentinel: process everything queued, flush, end
                try:
                    await asyncio.wait_for(asyncio.shield(audio_task), timeout=60)
                except asyncio.TimeoutError:
                    log.warning("live session did not finish flushing within 60 s; cancelling")
                    await cancel(audio_task)
                    await emit(await asyncio.to_thread(session.end))
            audio_task = None

        async def stop_all() -> None:
            nonlocal play_task
            await cancel(play_task)
            play_task = None
            await stop_live()

        await send({"type": "hello", "capabilities": session.capabilities(),
                    "scenarios": [s.public() for s in scenarios.values()]})
        try:
            while True:
                packet = await ws.receive()
                if packet.get("type") == "websocket.disconnect":
                    break
                if packet.get("bytes") is not None:
                    if session.state == "running" and session.mode in ("live-mic", "webrtc") and audio_task is not None:
                        if backlog["bytes"] > MAX_BACKLOG_S * 32000:
                            await send({"type": "error", "code": "audio_backlog",
                                        "message": "Audio is arriving faster than it can be processed; the oldest backlog was discarded."})
                            while not audio_q.empty():
                                audio_q.get_nowait()
                            backlog["bytes"] = 0
                        backlog["bytes"] += len(packet["bytes"])
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
                    backlog["bytes"] = 0
                    await emit(session.start(mode, {"title": msg.get("title", "")}))
                    audio_task = asyncio.create_task(audio_worker())

                elif kind == "manual_transcript":
                    if session.state != "running":
                        await send({"type": "error", "code": "no_call", "message": "Start a call before adding transcript text."})
                        continue
                    t = session.audio_s if session.mode in ("live-mic", "webrtc") else session.t
                    await emit(await asyncio.to_thread(session.ingest_transcript, t, msg.get("speaker", "caller"),
                                                       str(msg.get("text", "")), SRC_MANUAL, "manual-input"))

                elif kind == "stop":
                    await stop_all()
                    if session.state == "running":  # a scenario that was stopped part-way
                        await emit(session.end())

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
            await cancel(play_task)
            await cancel(audio_task)  # the socket is gone: nobody to flush to

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
