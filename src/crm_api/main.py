"""FastAPI app: wires up the routes, the session cookie, CORS and the live-update socket."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import db
from .config import ALLOWED_ORIGINS, REQUIRED, SESSION_SECRET
from .realtime import hub
from .routes import router
from .security import is_logged_in

logging.basicConfig(level=logging.INFO)
# httpx logs every request URL at INFO, and WEBHOOK_URL can work like a secret.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start with missing secrets rather than run with weak defaults.
    missing = [name for name, value in REQUIRED.items() if not value]
    if missing:
        raise RuntimeError(
            f"Missing {', '.join(missing)}. Add them to .env.local (see .env.example)."
        )
    await db.connect()
    hub.start()
    try:
        yield
    finally:
        await hub.stop()
        await db.disconnect()


app = FastAPI(title="VoiceCRM API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="crm_session",
    same_site="lax",  # not sent on cross-site POSTs, which blocks CSRF
    https_only=False,  # set True behind HTTPS in production
    max_age=8 * 60 * 60,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-API-Key"],
)
app.include_router(router)


@app.websocket("/ws")
async def live_updates(ws: WebSocket) -> None:
    """Pushes a small {type, actor, summary} message whenever the CRM changes."""
    # Browsers always send Origin on WebSockets and attach cookies even across
    # sites, so a foreign origin is refused: another page can't ride the session
    # (cross-site WebSocket hijacking).
    origin = ws.headers.get("origin")
    if not is_logged_in(ws) or (origin is not None and origin not in ALLOWED_ORIGINS):
        await ws.close(code=1008)  # policy violation
        return
    await hub.join(ws)
    try:
        while True:
            # Keeps the socket open; clients don't send anything.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(ws)
