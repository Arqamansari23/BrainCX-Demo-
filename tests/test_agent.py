# Turn-level tests for the CRM voice agent, using LiveKit's in-process testing
# framework (https://docs.livekit.io/testing/unit-tests/).
#
# Every tool is mocked, so these run without the CRM API or database. The model
# still sees the real tool schemas, so tool choice and arguments are under test.
# They drive the agent with a LiveKit Inference text LLM, so .env.local needs
# LiveKit credentials. Run with `uv run pytest`.
#
# End-to-end behaviour against the real API is covered by scenarios.yaml.

import json
from datetime import timedelta
from typing import Any

import pytest
from dotenv import load_dotenv
from livekit.agents import AgentSession, inference, llm, mock_tools
from livekit.agents.llm import ToolError

from agent import CRMAssistant
from crm_client import today

load_dotenv(".env.local")

AMBIGUOUS = ToolError(
    "'John' could be more than one contact: John Smith at Acme Corp, John Park at "
    "Globex. Ask the user which one they mean. Nothing was changed."
)
CRM_DOWN = ToolError(
    "The CRM can't be reached right now. Tell the user nothing was saved and "
    "suggest trying again in a moment."
)


def _llm() -> llm.LLM:
    return inference.LLM(model="openai/gpt-4.1-mini")


def _calls(result: Any, name: str) -> list[dict[str, Any]]:
    """Arguments of every call to the named tool in one turn."""
    return [
        json.loads(event.item.arguments)
        for event in result.events
        if event.type == "function_call" and event.item.name == name
    ]


# What lookup_contact returns for John Smith in the seed data.
JOHN_SMITH = {
    "contact": "John Smith",
    "company": "Acme Corp",
    "status": "lead",
    "deals": [
        {"id": 1, "title": "Acme CRM rollout", "stage": "contacted", "value": 24000}
    ],
    "open_tasks": [],
}


# Mocks get the tool's arguments by position, in the tool's own order (the
# RunContext first), so these mirror the real signatures.
def _moved(context: Any, contact_name: str, stage: str) -> dict[str, Any]:
    return {
        "contact": "John Smith",
        "deal": "Acme CRM rollout",
        "from_stage": "contacted",
        "to_stage": stage,
        "changed": True,
        "automation": ["created task 'Prepare proposal' due Saturday 3 October"],
    }


def _followed_up(context: Any, contact_name: str, due_date: str) -> dict[str, Any]:
    return {
        "created": True,
        "task": "Follow-up call",
        "contact": "John Smith",
        "due": due_date,
    }


@pytest.mark.asyncio
async def test_moves_deal_and_creates_follow_up() -> None:
    """The headline command must trigger both actions, with the right stage and date."""
    tomorrow = (today() + timedelta(days=1)).isoformat()
    async with _llm() as model, AgentSession(llm=model) as session:
        await session.start(CRMAssistant())
        with mock_tools(
            CRMAssistant,
            {
                "lookup_contact": lambda: JOHN_SMITH,
                "move_deal_stage": _moved,
                "create_follow_up": _followed_up,
                "create_lead": lambda: RuntimeError("must not create a lead"),
            },
        ):
            result = await session.run(
                user_input="Move John Smith to Qualified and create a follow-up for tomorrow."
            )

        moves = _calls(result, "move_deal_stage")
        assert moves, "expected a move_deal_stage call"
        assert moves[0]["stage"] == "qualified"
        assert "smith" in moves[0]["contact_name"].lower()

        follow_ups = _calls(result, "create_follow_up")
        assert follow_ups, "expected a create_follow_up call"
        assert follow_ups[0]["due_date"] == tomorrow
        assert not _calls(result, "create_lead")

        await (
            result.expect[-1]
            .is_message(role="assistant")
            .judge(
                model,
                intent=(
                    "Confirms that John Smith's deal is now in Qualified and that a "
                    "follow-up is scheduled for tomorrow."
                ),
            )
        )


@pytest.mark.asyncio
async def test_asks_which_contact_when_name_is_ambiguous() -> None:
    async with _llm() as model, AgentSession(llm=model) as session:
        await session.start(CRMAssistant())
        with mock_tools(
            CRMAssistant,
            {
                "lookup_contact": lambda: AMBIGUOUS,
                "move_deal_stage": lambda: AMBIGUOUS,
                "create_follow_up": lambda: AMBIGUOUS,
                "create_lead": lambda: RuntimeError("must not create a lead"),
            },
        ):
            result = await session.run(user_input="Move John to proposal.")

        await (
            result.expect[-1]
            .is_message(role="assistant")
            .judge(
                model,
                intent=(
                    "Asks whether the user means John Smith or John Park, and does not "
                    "claim that any deal was moved."
                ),
            )
        )


@pytest.mark.asyncio
async def test_reports_failure_when_crm_is_down() -> None:
    async with _llm() as model, AgentSession(llm=model) as session:
        await session.start(CRMAssistant())
        with mock_tools(
            CRMAssistant,
            {
                "lookup_contact": lambda: CRM_DOWN,
                "move_deal_stage": lambda: CRM_DOWN,
                "create_follow_up": lambda: CRM_DOWN,
                "create_lead": lambda: CRM_DOWN,
            },
        ):
            result = await session.run(user_input="Move Priya Patel to Proposal.")

        await (
            result.expect[-1]
            .is_message(role="assistant")
            .judge(
                model,
                intent=(
                    "Tells the user the CRM couldn't be reached, so the deal was not "
                    "moved or nothing was saved. Saying the deal 'wasn't moved' is "
                    "correct. It fails only if it says the move succeeded."
                ),
            )
        )
