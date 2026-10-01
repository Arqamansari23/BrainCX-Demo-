"""The voice agent's side of the CRM: calls to the CRM API, name matching, and dates.

The agent has no database access. It uses the same REST API as the web app,
authenticated with its own AGENT_API_KEY, so every change goes through the
API's validation and automations.
"""

from __future__ import annotations

import functools
import os
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from livekit.agents import utils
from livekit.agents.llm import ToolError

OPEN_STAGES = ("new", "contacted", "qualified", "proposal")


# ---------------------------------------------------------------------- dates


@functools.cache
def timezone() -> ZoneInfo:
    # Read lazily so .env.local (loaded by agent.py) is in place first.
    return ZoneInfo(os.getenv("TIMEZONE", "UTC"))


def today() -> date:
    return datetime.now(timezone()).date()


def spoken_date(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B %Y}"


def parse_due_date(value: str) -> date:
    try:
        due = date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(
            f"'{value}' is not a date. Use YYYY-MM-DD. Today is "
            f"{today().isoformat()} ({spoken_date(today())})."
        ) from None
    if due < today():
        raise ToolError(
            f"{spoken_date(due)} is in the past. Today is {spoken_date(today())}. "
            "Ask the user for a future date."
        )
    return due


# ------------------------------------------------------------------- API calls


def _error_detail(data: Any) -> str:
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(item.get("msg", item)) for item in detail)
    return "The CRM rejected the request."


async def api(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    """Call the CRM API and turn failures into messages the model can relay."""
    url = os.getenv("CRM_API_URL", "http://localhost:8001") + path
    headers = {"X-API-Key": os.getenv("AGENT_API_KEY", "")}
    try:
        # The job's shared aiohttp session; closed for us when the call ends.
        session = utils.http_context.http_session()
        async with session.request(
            method,
            url,
            json=json,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as response:
            status = response.status
            data = await response.json(content_type=None)
    except aiohttp.ClientConnectorError as e:
        # The connection never opened, so the CRM can't have processed anything.
        raise ToolError(
            "The CRM can't be reached right now. Tell the user nothing was saved "
            "and suggest trying again in a moment."
        ) from e
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        if method == "GET":
            raise ToolError(
                "The CRM didn't answer in time. Ask the user to try again in a moment."
            ) from e
        # The request may have reached the CRM, so don't claim it failed: a retry
        # could create a duplicate.
        raise ToolError(
            "The CRM didn't confirm the change in time, so it may or may not have "
            "been saved. Tell the user to check the board before trying again."
        ) from e

    if status == 401:
        raise ToolError(
            "The CRM refused the assistant's credentials. Nothing was saved."
        )
    if status >= 500:
        raise ToolError(
            "The CRM had an internal error. Tell the user nothing was saved."
        )
    if status >= 400:
        raise ToolError(_error_detail(data))
    return data


# --------------------------------------------------------- matching what was said


def _describe(contact: dict[str, Any]) -> str:
    company = contact.get("company")
    return f"{contact['full_name']} at {company}" if company else contact["full_name"]


STRONG_MATCH = 0.6
CLEAR_LEAD = 0.25


def pick_contact(
    query: str, hits: list[dict[str, Any]], company: str | None = None
) -> dict[str, Any]:
    """Choose the contact the user meant from scored search results, best first.

    An exact name match wins. Otherwise the top result must be a strong match and
    clearly ahead of the next one. For a full name, "strong" means the whole name
    is similar ("Jon Smith" is John Smith), so a shared first name alone ("James Wu"
    vs James Lee) never counts. A single word ("Priya", "Smith", "Acme") may match
    part of a name or the company. Anything less raises a ToolError that tells the
    model what to ask the user, and nothing is changed.
    """
    if company:
        wanted_company = company.strip().lower()
        hits = [h for h in hits if wanted_company in (h.get("company") or "").lower()]
    if not hits:
        where = f" at {company}" if company else ""
        raise ToolError(
            f"No contact in the CRM matches '{query}'{where}. Say so, ask the user to "
            "repeat the name, and offer to add them as a new lead if they are new."
        )
    wanted = query.strip().lower()
    exact = [h for h in hits if h["full_name"].lower() == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        options = ", ".join(_describe(h) for h in exact[:3])
        raise ToolError(
            f"Several contacts are called {exact[0]['full_name']}: {options}. Ask "
            "which company, then call again with company. Nothing was changed."
        )

    top = hits[0]
    runner_up = hits[1]["score"] if len(hits) > 1 else 0.0
    strength = top["name_similarity"] if len(wanted.split()) > 1 else top["score"]
    if strength >= STRONG_MATCH and top["score"] - runner_up >= CLEAR_LEAD:
        return top
    if strength >= STRONG_MATCH:
        options = ", ".join(_describe(h) for h in hits[:3])
        raise ToolError(
            f"'{query}' could be more than one contact: {options}. "
            "Ask the user which one they mean. Nothing was changed."
        )
    raise ToolError(
        f"No contact is called '{query}'. The closest is {_describe(top)}. Ask "
        f"whether they meant {top['full_name']} or want to add '{query}' as a new "
        "lead. Nothing was changed."
    )


# Words people add when naming a deal that aren't part of its title.
_FILLER_WORDS = {"the", "deal", "opportunity"}


def _title_matches(wanted: str, title: str) -> bool:
    words = [w for w in wanted.lower().split() if w not in _FILLER_WORDS]
    return bool(words) and all(w in title.lower() for w in words)


def pick_deal(contact: dict[str, Any], deal_title: str | None = None) -> dict[str, Any]:
    """The deal the user means: the one they named, else the only one, else the only
    open one. A named deal that doesn't match is an error, never a different deal."""
    deals = contact["deals"]
    name = contact["full_name"]
    if not deals:
        raise ToolError(f"{name} has no deals in the CRM.")
    listing = ", ".join(f"{d['title']} ({d['stage']})" for d in deals)
    if deal_title:
        named = [d for d in deals if _title_matches(deal_title, d["title"])]
        if len(named) == 1:
            return named[0]
        raise ToolError(
            f"{name} has no single deal matching '{deal_title}'. Their deals: "
            f"{listing}. Ask which one they mean. Nothing was changed."
        )
    if len(deals) == 1:
        return deals[0]
    open_deals = [d for d in deals if d["stage"] in OPEN_STAGES]
    if len(open_deals) == 1:
        return open_deals[0]
    raise ToolError(
        f"{name} has several deals: {listing}. Ask which one, then call again with "
        "deal_title. Nothing was changed."
    )


async def find_contact(name: str, company: str | None = None) -> dict[str, Any]:
    hits = await api("GET", "/api/contacts", params={"q": name})
    return pick_contact(name, hits, company)
