"""Connection pool, the activity log helper, and the read queries."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from .config import DATABASE_URL, today

STAGES = ["new", "contacted", "qualified", "proposal", "won", "lost"]

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Return json columns (used for nested deals and tasks) as Python objects.
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def connect() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        # Queries here take milliseconds; give up well before the agent's 8 s HTTP timeout
        # so it never reports "not saved" for a write that still commits later.
        DATABASE_URL,
        min_size=1,
        max_size=10,
        command_timeout=5,
        init=_init_connection,
    )


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not initialised")
    return _pool


def stage_label(stage: str) -> str:
    return stage.capitalize()


async def log_activity(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    contact_id: int | None,
    opportunity_id: int | None,
    kind: str,
    message: str,
    source: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO activities (contact_id, opportunity_id, kind, message, source)
        VALUES ($1, $2, $3, $4, $5)
        """,
        contact_id,
        opportunity_id,
        kind,
        message,
        source,
    )


async def board() -> dict[str, Any]:
    """Everything the web app shows, in one round trip."""
    p = pool()
    deals = await p.fetch(
        """
        SELECT o.id, o.title, o.value::float8 AS value, o.stage, o.updated_at,
               c.id AS contact_id, c.full_name AS contact, c.company,
               nt.title AS next_task, nt.due_date AS next_task_due
        FROM opportunities o
        JOIN contacts c ON c.id = o.contact_id
        LEFT JOIN LATERAL (
            SELECT t.title, t.due_date FROM tasks t
            WHERE t.opportunity_id = o.id AND NOT t.done
            ORDER BY t.due_date, t.id LIMIT 1
        ) nt ON true
        ORDER BY o.updated_at DESC
        """
    )
    tasks = await p.fetch(
        """
        SELECT t.id, t.title, t.due_date, t.source, t.contact_id,
               c.full_name AS contact, o.title AS deal
        FROM tasks t
        JOIN contacts c ON c.id = t.contact_id
        LEFT JOIN opportunities o ON o.id = t.opportunity_id
        WHERE NOT t.done
        ORDER BY t.due_date, t.id
        """
    )
    activities = await p.fetch(
        """
        SELECT id, kind, message, source, created_at FROM activities
        ORDER BY created_at DESC, id DESC LIMIT 30
        """
    )
    contacts = await p.fetch(
        """
        SELECT c.id, c.full_name, c.email, c.phone, c.company, c.status, c.source,
               count(o.id)::int AS deals,
               COALESCE(sum(o.value) FILTER (WHERE o.stage NOT IN ('won', 'lost')), 0)::float8
                   AS open_value
        FROM contacts c
        LEFT JOIN opportunities o ON o.contact_id = c.id
        GROUP BY c.id
        ORDER BY c.full_name
        """
    )
    return {
        "today": today().isoformat(),
        "stages": STAGES,
        "deals": [dict(r) for r in deals],
        "tasks": [dict(r) for r in tasks],
        "activities": [dict(r) for r in activities],
        "contacts": [dict(r) for r in contacts],
    }


async def search_contacts(query: str) -> list[dict[str, Any]]:
    """Fuzzy search by name or company, best match first.

    Trigram similarity copes with names heard slightly wrong ("Jon Smith"), and
    word_similarity lets a first name or a company ("John", "Acme") match.
    """
    rows = await pool().fetch(
        """
        SELECT * FROM (
            SELECT c.id, c.full_name, c.company, c.email, c.status,
                   GREATEST(similarity(c.full_name, $1),
                            word_similarity($1, c.full_name),
                            word_similarity($1, COALESCE(c.company, '')))::float8 AS score,
                   -- Whole-name similarity: a full name must match as a whole, so
                   -- "James Wu" doesn't become James Lee just because the first names match.
                   similarity(c.full_name, $1)::float8 AS name_similarity,
                   COALESCE((SELECT json_agg(json_build_object(
                                 'id', o.id, 'title', o.title, 'stage', o.stage,
                                 'value', o.value::float8) ORDER BY o.id)
                             FROM opportunities o WHERE o.contact_id = c.id), '[]') AS deals,
                   COALESCE((SELECT json_agg(json_build_object(
                                 'title', t.title, 'due_date', t.due_date) ORDER BY t.due_date)
                             FROM tasks t WHERE t.contact_id = c.id AND NOT t.done), '[]')
                       AS open_tasks
            FROM contacts c
        ) matches
        WHERE score > 0.3
        ORDER BY score DESC, full_name
        LIMIT 5
        """,
        query,
    )
    return [dict(r) for r in rows]
