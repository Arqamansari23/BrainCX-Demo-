"""Live updates. Every change is pushed to the open browser tabs over WebSocket.

All writes go through this API, so routes call hub.broadcast() after each commit.
It doesn't matter whether the change came from the voice agent, a webhook, or
another tab: the board updates straight away.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._keepalive: asyncio.Task[None] | None = None

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def leave(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:  # the tab went away mid-send
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    def start(self) -> None:
        self._keepalive = asyncio.create_task(self._keep_alive())

    async def stop(self) -> None:
        if self._keepalive is not None:
            self._keepalive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive

    async def _keep_alive(self) -> None:
        """Ping idle sockets so proxies don't drop them, and prune dead ones."""
        while True:
            await asyncio.sleep(25)
            await self.broadcast({"type": "ping"})


hub = Hub()
