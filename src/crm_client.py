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
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            status = response.status
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise ToolError(
            "The CRM can't be reached right now. Tell the user nothing was saved "
            "and suggest trying again in a moment."
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


def pick_contact(query: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the contact the user meant from scored search results, best first.

    An exact name match wins. Otherwise the top result has to be both a strong
    match and clearly ahead of the next one. Anything less raises a ToolError
    that lists the candidates, so the model asks the user which one they meant.
    """
    if not hits:
        raise ToolError(
            f"No contact in the CRM matches '{query}'. Say so, ask the user to repeat "
            "the name, and offer to create a new lead if they are new."
        )
    wanted = query.strip().lower()
    exact = [h for h in hits if h["full_name"].lower() == wanted]
    if len(exact) == 1:
        return exact[0]

    top = hits[0]
    runner_up = hits[1]["score"] if len(hits) > 1 else 0.0
    if not exact and top["score"] >= 0.6 and top["score"] - runner_up >= 0.25:
        return top

    options = ", ".join(_describe(h) for h in hits[:3])
    raise ToolError(
        f"'{query}' could be more than one contact: {options}. "
        "Ask the user which one they mean. Nothing was changed."
    )


def pick_deal(contact: dict[str, Any], deal_title: str | None = None) -> dict[str, Any]:
    """The deal the user means: the named one, the only one, or the only open one."""
    deals = contact["deals"]
    name = contact["full_name"]
    if not deals:
        raise ToolError(f"{name} has no deals in the CRM.")
    if deal_title:
        named = [d for d in deals if deal_title.strip().lower() in d["title"].lower()]
        if len(named) == 1:
            return named[0]
    if len(deals) == 1:
        return deals[0]
    open_deals = [d for d in deals if d["stage"] in OPEN_STAGES]
    if len(open_deals) == 1:
        return open_deals[0]
    listing = ", ".join(f"{d['title']} ({d['stage']})" for d in deals)
    raise ToolError(
        f"{name} has several deals: {listing}. Ask which one, then call again with "
        "deal_title. Nothing was changed."
    )


async def find_contact(name: str) -> dict[str, Any]:
    return pick_contact(name, await api("GET", "/api/contacts", params={"q": name}))
