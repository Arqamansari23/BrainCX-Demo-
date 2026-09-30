import logging
import textwrap
from datetime import timedelta
from typing import Any, Literal

from dotenv import load_dotenv
from google.genai import types
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    inference,
)
from livekit.agents.llm import ToolError
from livekit.plugins import google

from crm_client import api, find_contact, parse_due_date, pick_deal, spoken_date, today

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# The web app's token endpoint dispatches this name (AGENT_NAME in crm_api/config.py).
AGENT_NAME = "crm-agent"

Stage = Literal["new", "contacted", "qualified", "proposal", "won", "lost"]

GREETING = (
    "Greet the user in one short sentence as their CRM assistant and ask what "
    "they would like to update."
)


def instructions() -> str:
    now = today()
    # Spell out the next two weeks so the model can turn "next Friday" into a date.
    calendar = "\n".join(
        f"- {spoken_date(d)} = {d.isoformat()}"
        for d in (now + timedelta(days=i) for i in range(14))
    )
    return (
        textwrap.dedent(
            """\
            You are the voice assistant for VoiceCRM, a small sales CRM. You help a salesperson keep their pipeline up to date by voice: move deals between stages, schedule follow-ups, look up contacts, and add new leads.

            # Output rules

            You are speaking, so everything you say must sound natural out loud:

            - Plain spoken sentences only. Never use lists, markdown, JSON, code, or emojis.
            - Keep replies to one or two short sentences. Ask one question at a time.
            - Say dates naturally, like "Friday the second of October", and amounts naturally, like "twenty-four thousand dollars".
            - Never read out ids, tool names, field names, or raw tool output.

            # Pipeline stages, in order

            New, Contacted, Qualified, Proposal, Won, Lost. Map everyday words onto them: "signed" or "closed won" means Won, "sent the proposal" means Proposal, "dead" or "closed lost" means Lost.

            # How to act

            - When a request is clear, act on it straight away with your tools. Don't ask for confirmation first; these changes are easy to undo.
            - Do every action the user asks for in the same turn. "Move John Smith to Qualified and create a follow-up for tomorrow" needs both move_deal_stage and create_follow_up. A task that an automation creates never replaces a follow-up the user asked for.
            - Turn relative dates like "tomorrow" or "next Monday" into YYYY-MM-DD using the calendar below. If the user asks for a follow-up without a date, use tomorrow and say so.
            - After acting, confirm what changed in one sentence, using the contact, deal and stage from the tool result, for example "Done, John Smith's Acme CRM rollout deal is now in Qualified". If the result lists automation actions, mention them briefly, for example "the pipeline automation also added a Prepare proposal task for Saturday".
            - Once a request is done, stop there. Don't tack on offers like "would you like a follow-up?" or "what else?"; the user will say when they need more.
            - Only ask a question when the contact, the deal, or the date is unclear, or when a tool tells you to ask.
            - If a tool reports an error, tell the user plainly that the change was not saved, and why if the error says.

            # Never invent CRM facts

            Every name, company, deal, stage, amount and date you mention must come from a tool result in this conversation. If a contact isn't found, say so and offer to add them as a new lead. Never guess.

            Politely steer requests unrelated to the CRM back to sales work.

            # Today

            """
        )
        + f"Today is {spoken_date(now)} ({now.isoformat()}). Upcoming dates:\n{calendar}\n"
    )


class CRMAssistant(Agent):
    """Voice assistant whose tools act on the CRM through its REST API."""

    def __init__(self) -> None:
        # No llm here: the session supplies it (Gemini Live in production, a text
        # LLM in tests and text simulations), since an Agent's own llm would win.
        super().__init__(instructions=instructions())

    @function_tool()
    async def lookup_contact(self, context: RunContext, name: str) -> dict[str, Any]:
        """Look up a contact and return their company, status, deals (with stage and
        value) and open follow-up tasks. Use when the user asks about a contact or deal.

        Args:
            name: The contact's name as the user said it, e.g. "John Smith". A company
                name such as "Acme" also works.
        """
        contact = await find_contact(name)
        return {
            "contact": contact["full_name"],
            "company": contact["company"],
            "status": contact["status"],
            "deals": contact["deals"],
            "open_tasks": contact["open_tasks"],
        }

    @function_tool()
    async def move_deal_stage(
        self,
        context: RunContext,
        contact_name: str,
        stage: Stage,
        deal_title: str | None = None,
    ) -> dict[str, Any]:
        """Move a contact's deal to another pipeline stage. Automations may run
        afterwards, for example creating the next task; the result lists what they did.

        Args:
            contact_name: The contact's name as the user said it, e.g. "John Smith".
            stage: The stage to move the deal to.
            deal_title: Only when the contact has several deals and the user said which.
        """
        # A half-finished write can't be rolled back, so don't let speech cut it off.
        context.disallow_interruptions()
        contact = await find_contact(contact_name)
        deal = pick_deal(contact, deal_title)
        return await api(
            "PATCH", f"/api/opportunities/{deal['id']}", json={"stage": stage}
        )

    @function_tool()
    async def create_follow_up(
        self,
        context: RunContext,
        contact_name: str,
        due_date: str,
        title: str = "Follow-up call",
    ) -> dict[str, Any]:
        """Create a follow-up task for a contact. Call this whenever the user asks for
        a follow-up, reminder or call-back, even if an automation created another task.

        Args:
            contact_name: The contact's name as the user said it.
            due_date: Due date as YYYY-MM-DD. Resolve words like "tomorrow" with the
                calendar in your instructions.
            title: A short description, e.g. "Follow-up call" or "Send pricing".
        """
        due = parse_due_date(due_date)
        context.disallow_interruptions()
        contact = await find_contact(contact_name)
        try:
            deal_id = pick_deal(contact)["id"]
        except ToolError:
            deal_id = (
                None  # no single obvious deal; the task still belongs to the contact
            )
        task = await api(
            "POST",
            "/api/tasks",
            json={
                "contact_id": contact["id"],
                "opportunity_id": deal_id,
                "title": title,
                "due_date": due.isoformat(),
            },
        )
        return {
            "created": True,
            "task": task["title"],
            "contact": task["contact"],
            "due": spoken_date(due),
        }

    @function_tool()
    async def create_lead(
        self,
        context: RunContext,
        full_name: str,
        company: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        deal_value: float | None = None,
    ) -> dict[str, Any]:
        """Add a new lead: a contact plus a new deal in the New stage. An automation
        then schedules an intro call. Only for people who aren't in the CRM yet.

        Args:
            full_name: The person's full name.
            company: Their company, if mentioned.
            email: Their email address, if the user gives one.
            phone: Their phone number, if the user gives one.
            deal_value: Estimated deal value in dollars, if mentioned.
        """
        context.disallow_interruptions()
        return await api(
            "POST",
            "/api/contacts",
            json={
                "full_name": full_name,
                "company": company,
                "email": email,
                "phone": phone,
                "deal_value": deal_value,
            },
        )


server = AgentServer()


def create_session(ctx: JobContext) -> AgentSession:
    if ctx.simulation_context():
        # Text simulations (lk agent simulate text) carry no audio, so the same
        # agent, prompt and tools run on a text LLM instead of Gemini Live.
        return AgentSession(llm=inference.LLM(model="openai/gpt-4.1-mini"))
    return AgentSession(
        # Gemini Live handles speech in and out, so there is no STT or TTS to configure.
        # See https://docs.livekit.io/agents/models/realtime/plugins/gemini/
        llm=google.realtime.RealtimeModel(
            model="gemini-3.1-flash-live-preview",
            voice="Enceladus",
            language="en-US",
            # Gemini streams its private reasoning as transcript by default, which
            # makes the agent read its own plan out loud.
            thinking_config=types.ThinkingConfig(include_thoughts=False),
        ),
        turn_handling=TurnHandlingOptions(
            # Gemini Live decides turns on its own signals; LiveKit's turn detector
            # would leave it waiting and replies would arrive a turn late.
            # See https://docs.livekit.io/agents/logic/turns/#realtime-models
            turn_detection="realtime_llm",
        ),
    )


@server.rtc_session(agent_name=AGENT_NAME)
async def crm_agent(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = create_session(ctx)
    await session.start(agent=CRMAssistant(), room=ctx.room)
    await ctx.connect()

    # Greet first. Gemini's session may still be connecting and the plugin allows the
    # first reply only a few seconds, so retry rather than opening in silence.
    for attempt in range(1, 4):
        handle = await session.generate_reply(instructions=GREETING)
        if handle.exception() is None:
            break
        logger.warning("greeting attempt %d failed: %s", attempt, handle.exception())


if __name__ == "__main__":
    cli.run_app(server)
