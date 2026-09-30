"""Settings, read from the project's .env.local (the same file the voice agent uses)."""

import os
from datetime import date, datetime
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env.local")

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://crm:crm_password@localhost:5433/crm"
)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5174").split(",")
    if origin.strip()
]

# Outbound events go to WEBHOOK_URL (optional); inbound leads must be signed.
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
INBOUND_WEBHOOK_SECRET = os.getenv("INBOUND_WEBHOOK_SECRET", "")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
# Must match @server.rtc_session(agent_name=...) in src/agent.py.
AGENT_NAME = "crm-agent"

REQUIRED = {
    "ADMIN_PASSWORD": ADMIN_PASSWORD,
    "SESSION_SECRET": SESSION_SECRET,
    "AGENT_API_KEY": AGENT_API_KEY,
}


@cache
def timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("TIMEZONE", "UTC"))


def today() -> date:
    return datetime.now(timezone()).date()


def nice_date(d: date) -> str:
    """'Thursday 2 October', for activity messages the agent may read aloud."""
    return f"{d:%A} {d.day} {d:%B}"
