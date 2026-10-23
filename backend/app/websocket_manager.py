"""Small helpers for WebSocket delivery: per-session sender and WebRTC signaling rooms."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class SocketSender:
    """Serialises JSON sends to one socket and swallows errors after the client has gone."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.open = True
        self._lock = asyncio.Lock()

    async def __call__(self, msg: dict[str, Any]) -> None:
        if not self.open:
            return
        async with self._lock:
            try:
                await self.ws.send_text(json.dumps(msg))
            except Exception:  # noqa: BLE001 - client disconnected mid-send
                self.open = False


class SignalingRooms:
    """Relays WebRTC offer/answer/ICE between the (at most two) peers in a room.
    Audio/video never touches the server; it flows peer to peer."""

    MAX_PEERS = 2

    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, WebSocket]] = {}

    def join(self, room: str, peer_id: str, ws: WebSocket) -> bool:
        peers = self.rooms.setdefault(room, {})
        if len(peers) >= self.MAX_PEERS and peer_id not in peers:
            return False
        peers[peer_id] = ws
        return True

    def leave(self, room: str, peer_id: str) -> None:
        peers = self.rooms.get(room, {})
        peers.pop(peer_id, None)
        if not peers:
            self.rooms.pop(room, None)

    def others(self, room: str, peer_id: str) -> list[WebSocket]:
        return [w for pid, w in self.rooms.get(room, {}).items() if pid != peer_id]

    def count(self, room: str) -> int:
        return len(self.rooms.get(room, {}))
