from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventHub:
    def __init__(self) -> None:
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.remove(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        data = json.dumps(event)
        dead = []
        for client in self._clients:
            try:
                await client.send_text(data)
            except Exception:
                dead.append(client)
        for client in dead:
            self._clients.remove(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)
