"""Who is calling: a logged-in browser, the voice agent's API key, or a signed webhook.

The caller's identity decides the "source" written to the activity log, so the
audit trail can't be spoofed by a request body.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Literal

from fastapi import Header, HTTPException
from starlette.requests import HTTPConnection

from .config import ADMIN_PASSWORD, AGENT_API_KEY

Actor = Literal["ui", "voice"]

SESSION_KEY = "user"
# Signed webhooks older (or newer) than this are rejected, which blocks replays.
MAX_CLOCK_SKEW_SECONDS = 300


def _same(a: str, b: str) -> bool:
    # Constant-time comparison, so secrets can't be guessed from response timing.
    return hmac.compare_digest(a.encode(), b.encode())


# ------------------------------------------------------------------ web session


def check_password(password: str) -> bool:
    return bool(ADMIN_PASSWORD) and _same(password, ADMIN_PASSWORD)


def is_logged_in(conn: HTTPConnection) -> bool:
    # Works for HTTP requests and WebSockets alike (SessionMiddleware covers both).
    return conn.session.get(SESSION_KEY) is not None


def require_session(conn: HTTPConnection) -> None:
    """Dependency for browser-only endpoints (board, LiveKit token)."""
    if not is_logged_in(conn):
        raise HTTPException(401, "Login required")


async def get_actor(
    conn: HTTPConnection, x_api_key: str | None = Header(default=None)
) -> Actor:
    """Dependency for endpoints both the web app and the voice agent use."""
    if x_api_key is not None:
        if AGENT_API_KEY and _same(x_api_key, AGENT_API_KEY):
            return "voice"
        raise HTTPException(401, "Invalid API key")
    if is_logged_in(conn):
        return "ui"
    raise HTTPException(401, "Login required")


# ------------------------------------------------------------------- webhooks


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 over "<timestamp>.<raw body>", sent as the X-Signature header."""
    digest = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def verify_signature(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> bool:
    if not (secret and timestamp and signature):
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if (
        abs((now if now is not None else time.time()) - sent_at)
        > MAX_CLOCK_SKEW_SECONDS
    ):
        return False
    return _same(sign(secret, timestamp, body), signature)
