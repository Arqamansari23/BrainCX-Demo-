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
        # Routes await this before replying, so send to every tab at once and
        # give each a short timeout: a stalled tab must not slow the voice agent.
        clients = list(self._clients)
        delivered = await asyncio.gather(*(self._send(ws, message) for ws in clients))
        for ws, ok in zip(clients, delivered, strict=True):
            if not ok:
                self._clients.discard(ws)

    @staticmethod
    async def _send(ws: WebSocket, message: dict[str, Any]) -> bool:
        try:
            await asyncio.wait_for(ws.send_json(message), timeout=2)
            return True
        except Exception:  # the tab went away or stopped reading
            return False

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
