"""REST endpoints.

The web app and the voice agent share these endpoints, so a change runs the same
validation and automations whoever makes it. Webhooks and the LiveKit token
endpoint have their own authentication.
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
)
from livekit import api as livekit_api
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import automations, db
from .config import (
    AGENT_NAME,
    INBOUND_WEBHOOK_SECRET,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    nice_date,
    today,
)
from .realtime import hub
from .security import (
    SESSION_KEY,
    Actor,
    check_password,
    get_actor,
    is_logged_in,
    require_session,
    verify_signature,
)

router = APIRouter(prefix="/api")

Stage = Literal["new", "contacted", "qualified", "proposal", "won", "lost"]


def _clean(value: Any) -> Any:
    """Trim strings and turn empty ones into None."""
    if isinstance(value, str):
        return value.strip() or None
    return value


# ------------------------------------------------------------------------ auth


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


@router.post("/auth/login")
async def login(body: LoginIn, request: Request) -> dict[str, bool]:
    if not check_password(body.password):
        raise HTTPException(401, "Wrong password")
    request.session[SESSION_KEY] = "admin"
    return {"ok": True}


@router.post("/auth/logout")
async def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request) -> dict[str, bool]:
    return {"logged_in": is_logged_in(request)}


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": await db.pool().fetchval("SELECT 1") == 1, "today": today()}


# ----------------------------------------------------------------------- reads


@router.get("/board", dependencies=[Depends(require_session)])
async def board() -> dict[str, Any]:
    return await db.board()


@router.get("/contacts")
async def search_contacts(
    q: str = Query(min_length=1, max_length=100),
    _actor: Actor = Depends(get_actor),
) -> list[dict[str, Any]]:
    return await db.search_contacts(q.strip())


# ----------------------------------------------------------------------- leads


class LeadIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    company: str | None = Field(default=None, max_length=120)
    email: str | None = Field(
        default=None, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    phone: str | None = Field(default=None, max_length=40)
    deal_title: str | None = Field(default=None, max_length=160)
    deal_value: float | None = Field(default=None, ge=0, le=100_000_000)

    trim_text = field_validator(
        "full_name", "company", "phone", "deal_title", mode="before"
    )(_clean)

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, value: Any) -> Any:
        value = _clean(value)
        return value.lower() if isinstance(value, str) else value


async def create_lead(
    lead: LeadIn, source: str, background: BackgroundTasks
) -> dict[str, Any]:
    title = lead.deal_title or f"{lead.company or lead.full_name} deal"
    value = lead.deal_value or 0
    async with db.pool().acquire() as conn, conn.transaction():
        # A form sent twice with the same email reuses the contact instead of
        # duplicating it; the new deal is added to that contact.
        contact = await conn.fetchrow(
            """
            INSERT INTO contacts (full_name, email, phone, company, source)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (email) DO UPDATE
                SET phone = COALESCE(contacts.phone, EXCLUDED.phone),
                    company = COALESCE(contacts.company, EXCLUDED.company)
            RETURNING id, full_name, company
            """,
            lead.full_name,
            lead.email,
            lead.phone,
            lead.company,
            source,
        )
        deal_id = await conn.fetchval(
            "INSERT INTO opportunities (contact_id, title, value) VALUES ($1, $2, $3) RETURNING id",
            contact["id"],
            title,
            value,
        )
        who = contact["full_name"] + (
            f" ({contact['company']})" if contact["company"] else ""
        )
        summary = f"New lead {who}: {title}"
        await db.log_activity(
            conn,
            contact_id=contact["id"],
            opportunity_id=deal_id,
            kind="lead_created",
            message=summary,
            source=source,
        )
        actions = await automations.after_lead_created(
            conn, contact_id=contact["id"], opportunity_id=deal_id
        )

    await hub.broadcast(
        {"type": "crm", "actor": source, "summary": summary, "opportunity_id": deal_id}
    )
    automations.queue_webhook(
        background,
        "lead.created",
        {
            "contact_id": contact["id"],
            "opportunity_id": deal_id,
            "contact": contact["full_name"],
            "company": contact["company"],
            "deal": title,
            "value": value,
            "source": source,
        },
        actions,
    )
    return {
        "contact_id": contact["id"],
        "opportunity_id": deal_id,
        "contact": contact["full_name"],
        "deal": title,
        "stage": "new",
        "automation": actions,
    }


@router.post("/contacts", status_code=201)
async def create_lead_endpoint(
    lead: LeadIn, background: BackgroundTasks, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    return await create_lead(lead, actor, background)


# ------------------------------------------------------------------- pipeline


class StageIn(BaseModel):
    stage: Stage

    @field_validator("stage", mode="before")
    @classmethod
    def lower_case(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


@router.patch("/opportunities/{deal_id}")
async def change_stage(
    deal_id: int,
    body: StageIn,
    background: BackgroundTasks,
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    async with db.pool().acquire() as conn, conn.transaction():
        deal = await conn.fetchrow(
            """
            SELECT o.id, o.title, o.stage, o.value::float8 AS value, o.contact_id,
                   c.full_name AS contact
            FROM opportunities o JOIN contacts c ON c.id = o.contact_id
            WHERE o.id = $1
            FOR UPDATE OF o
            """,
            deal_id,
        )
        if deal is None:
            raise HTTPException(404, "Deal not found")

        old, new = deal["stage"], body.stage
        result = {
            "contact": deal["contact"],
            "deal": deal["title"],
            "from_stage": old,
            "to_stage": new,
        }
        if old == new:
            return {
                **result,
                "changed": False,
                "message": f"{deal['title']} is already in {db.stage_label(new)}",
                "automation": [],
            }

        await conn.execute(
            "UPDATE opportunities SET stage = $2, updated_at = now() WHERE id = $1",
            deal_id,
            new,
        )
        summary = (
            f"{deal['contact']}: {deal['title']} moved from "
            f"{db.stage_label(old)} to {db.stage_label(new)}"
        )
        await db.log_activity(
            conn,
            contact_id=deal["contact_id"],
            opportunity_id=deal_id,
            kind="stage_changed",
            message=summary,
            source=actor,
        )
        actions = await automations.after_stage_change(conn, deal=deal, new_stage=new)

    await hub.broadcast(
        {"type": "crm", "actor": actor, "summary": summary, "opportunity_id": deal_id}
    )
    automations.queue_webhook(
        background,
        "opportunity.stage_changed",
        {
            "contact_id": deal["contact_id"],
            "opportunity_id": deal_id,
            "contact": deal["contact"],
            "deal": deal["title"],
            "value": deal["value"],
            "from_stage": old,
            "to_stage": new,
            "changed_by": actor,
        },
        actions,
    )
    return {**result, "changed": True, "message": summary, "automation": actions}


# ---------------------------------------------------------------------- tasks


class TaskIn(BaseModel):
    contact_id: int
    opportunity_id: int | None = None
    title: str = Field(min_length=2, max_length=160)
    due_date: date

    trim_text = field_validator("title", mode="before")(_clean)


class TaskUpdate(BaseModel):
    done: bool


@router.post("/tasks", status_code=201)
async def create_task(
    body: TaskIn, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    start = today()
    if not start <= body.due_date <= start + timedelta(days=365):
        raise HTTPException(
            422,
            f"The due date must be between today ({start.isoformat()}) and one year from now.",
        )
    async with db.pool().acquire() as conn, conn.transaction():
        contact = await conn.fetchrow(
            "SELECT id, full_name FROM contacts WHERE id = $1", body.contact_id
        )
        if contact is None:
            raise HTTPException(404, "Contact not found")
        if body.opportunity_id is not None:
            owner = await conn.fetchval(
                "SELECT contact_id FROM opportunities WHERE id = $1",
                body.opportunity_id,
            )
            if owner != body.contact_id:
                raise HTTPException(422, "That deal doesn't belong to this contact")
        task_id = await conn.fetchval(
            """
            INSERT INTO tasks (contact_id, opportunity_id, title, due_date, source)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
            """,
            body.contact_id,
            body.opportunity_id,
            body.title,
            body.due_date,
            actor,
        )
        summary = (
            f"Task '{body.title}' for {contact['full_name']} "
            f"due {nice_date(body.due_date)}"
        )
        await db.log_activity(
            conn,
            contact_id=body.contact_id,
            opportunity_id=body.opportunity_id,
            kind="task_created",
            message=summary,
            source=actor,
        )

    await hub.broadcast(
        {
            "type": "crm",
            "actor": actor,
            "summary": summary,
            "opportunity_id": body.opportunity_id,
        }
    )
    return {
        "id": task_id,
        "title": body.title,
        "contact": contact["full_name"],
        "due_date": body.due_date.isoformat(),
        "due": nice_date(body.due_date),
    }


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: int, body: TaskUpdate, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    async with db.pool().acquire() as conn, conn.transaction():
        task = await conn.fetchrow(
            """
            UPDATE tasks t SET done = $2 FROM contacts c
            WHERE t.id = $1 AND c.id = t.contact_id
            RETURNING t.id, t.title, t.contact_id, t.opportunity_id, c.full_name AS contact
            """,
            task_id,
            body.done,
        )
        if task is None:
            raise HTTPException(404, "Task not found")
        summary = (
            f"Task '{task['title']}' for {task['contact']} "
            f"{'completed' if body.done else 'reopened'}"
        )
        await db.log_activity(
            conn,
            contact_id=task["contact_id"],
            opportunity_id=task["opportunity_id"],
            kind="task_done",
            message=summary,
            source=actor,
        )
    await hub.broadcast({"type": "crm", "actor": actor, "summary": summary})
    return {"id": task_id, "done": body.done}


# -------------------------------------------------------------------- LiveKit


@router.post("/livekit/token", status_code=201, dependencies=[Depends(require_session)])
async def livekit_token() -> dict[str, str]:
    """Standard LiveKit token endpoint, consumed by TokenSource.endpoint in the web app.

    The server picks the room, the identity and the agent to dispatch, and ignores
    anything the browser sends, so a client can't join another room or summon a
    different agent. Each call gets a fresh room, and the token expires quickly.
    """
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise HTTPException(500, "LiveKit is not configured on the server")
    room = f"crm-{secrets.token_hex(4)}"
    token = (
        livekit_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(f"crm-user-{secrets.token_hex(3)}")
        .with_name("CRM user")
        .with_grants(
            livekit_api.VideoGrants(
                room_join=True, room=room, can_publish=True, can_subscribe=True
            )
        )
        .with_room_config(
            livekit_api.RoomConfiguration(
                agents=[livekit_api.RoomAgentDispatch(agent_name=AGENT_NAME)]
            )
        )
        .with_ttl(timedelta(minutes=10))
    )
    return {"server_url": LIVEKIT_URL, "participant_token": token.to_jwt()}


# ------------------------------------------------------------ inbound webhook


@router.post("/webhooks/leads", status_code=201)
async def inbound_lead(
    request: Request,
    background: BackgroundTasks,
    x_timestamp: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """New leads from outside: a website form, Zapier, Make or n8n.

    The sender signs the raw body with the shared INBOUND_WEBHOOK_SECRET (see
    scripts/send_lead.py). Unsigned, tampered or stale requests get a 401 before
    the body is even parsed.
    """
    raw = await request.body()
    if len(raw) > 10_000:
        raise HTTPException(413, "Payload too large")
    if not verify_signature(INBOUND_WEBHOOK_SECRET, x_timestamp, x_signature, raw):
        raise HTTPException(401, "Invalid or expired signature")
    try:
        lead = LeadIn.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(
            422, e.errors(include_url=False, include_context=False, include_input=False)
        ) from None
    return await create_lead(lead, "webhook", background)
