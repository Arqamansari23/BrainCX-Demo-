"""Automated workflows.

They run on every change, whoever made it (web app, voice agent, or inbound
webhook). They run inside the same transaction as the change, so a stage
change and the task it triggers are saved together or not at all. Each
function returns what it did, in plain words, and the API passes that back so
the voice agent can tell the user.

Outbound webhooks are the one exception. They go out after the response, as a
background task, so a slow receiver never delays the agent's reply.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
from fastapi import BackgroundTasks

from . import db
from .config import WEBHOOK_SECRET, WEBHOOK_URL, nice_date, today
from .realtime import hub
from .security import sign

logger = logging.getLogger("crm.automations")

# Stage playbook: entering a stage creates the next task for the salesperson.
PLAYBOOK: dict[str, tuple[str, int]] = {
    "qualified": ("Prepare proposal", 2),
    "proposal": ("Chase decision", 3),
    "won": ("Onboarding kickoff", 1),
}


async def _add_task(
    conn: asyncpg.Connection,
    *,
    contact_id: int,
    opportunity_id: int,
    title: str,
    days_from_now: int,
) -> str | None:
    """Create an automation task unless the same one is already open."""
    due = today() + timedelta(days=days_from_now)
    created = await conn.fetchval(
        """
        INSERT INTO tasks (contact_id, opportunity_id, title, due_date, source)
        SELECT $1, $2, $3, $4, 'automation'
        WHERE NOT EXISTS (
            SELECT 1 FROM tasks WHERE opportunity_id = $2 AND title = $3 AND NOT done
        )
        RETURNING id
        """,
        contact_id,
        opportunity_id,
        title,
        due,
    )
    if created is None:
        return None
    action = f"created task '{title}' due {nice_date(due)}"
    await db.log_activity(
        conn,
        contact_id=contact_id,
        opportunity_id=opportunity_id,
        kind="task_created",
        message=f"Automation {action}",
        source="automation",
    )
    return action


async def after_stage_change(
    conn: asyncpg.Connection, *, deal: asyncpg.Record, new_stage: str
) -> list[str]:
    actions: list[str] = []
    if rule := PLAYBOOK.get(new_stage):
        title, days = rule
        if action := await _add_task(
            conn,
            contact_id=deal["contact_id"],
            opportunity_id=deal["id"],
            title=title,
            days_from_now=days,
        ):
            actions.append(action)

    if new_stage == "won":
        became_customer = await conn.fetchval(
            """
            UPDATE contacts SET status = 'customer'
            WHERE id = $1 AND status <> 'customer' RETURNING id
            """,
            deal["contact_id"],
        )
        if became_customer:
            action = f"marked {deal['contact']} as a customer"
            await db.log_activity(
                conn,
                contact_id=deal["contact_id"],
                opportunity_id=deal["id"],
                kind="status_changed",
                message=f"Automation {action}",
                source="automation",
            )
            actions.append(action)
    return actions


async def after_lead_created(
    conn: asyncpg.Connection, *, contact_id: int, opportunity_id: int
) -> list[str]:
    action = await _add_task(
        conn,
        contact_id=contact_id,
        opportunity_id=opportunity_id,
        title="Intro call",
        days_from_now=1,
    )
    return [action] if action else []


# ------------------------------------------------------------ outbound webhooks


def queue_webhook(
    background: BackgroundTasks, event: str, data: dict[str, Any], actions: list[str]
) -> None:
    """Send a signed event after the response. Adds a note to `actions` when it will."""
    if not WEBHOOK_URL:
        return
    background.add_task(deliver_webhook, event, data)
    actions.append(f"sent the {event} webhook")


async def deliver_webhook(event: str, data: dict[str, Any]) -> None:
    """POST the event to WEBHOOK_URL (n8n, Zapier, webhook.site...) and log the outcome.

    One attempt with a short timeout. A production version would retry from an
    outbox table.
    """
    body = json.dumps(
        {"event": event, "occurred_at": datetime.now(UTC).isoformat(), "data": data},
        default=str,
    ).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Event": event,
        "X-Timestamp": timestamp,
        # Receivers recompute this with the shared secret to prove the event is ours.
        "X-Signature": sign(WEBHOOK_SECRET, timestamp, body),
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(WEBHOOK_URL, content=body, headers=headers)
        outcome = f"delivered (HTTP {response.status_code})"
        if not response.is_success:
            outcome = f"rejected (HTTP {response.status_code})"
    except httpx.HTTPError as e:
        logger.warning("webhook %s failed: %s", event, e)
        outcome = f"failed ({type(e).__name__})"

    message = f"Webhook {event} {outcome}"
    await db.log_activity(
        db.pool(),
        contact_id=data.get("contact_id"),
        opportunity_id=data.get("opportunity_id"),
        kind="webhook",
        message=message,
        source="automation",
    )
    await hub.broadcast({"type": "crm", "actor": "automation", "summary": message})
