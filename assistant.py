"""Second Brain — the UI-agnostic agent core.

Defines the agents shared by every front-end (the CLI in main.py and the Gradio
web UI in app.py):

- `router_agent` ("Second Brain"): the single voice. Routes each message to
  store / recall / ask-follow-up / chat.
- `sql_agent` (the `store_memory` tool): turns a memory into SQL and runs
  `execute_sql`, reading the DB error and retrying on failure — the feedback loop.
- `recall_agent` (the `recall_memories` tool): answers questions about stored
  data with read-only SELECTs via `query_db`.

Standing instructions the user gives ("from now on, ...") are saved to memory.md
via the `save_preference` tool and injected into every run (see prefs.py).
"""

import os
import uuid
from datetime import date

from agents import Agent, RunConfig, enable_verbose_stdout_logging
from dotenv import load_dotenv

from prefs import load_preferences, save_preference
from tools import SCHEMA_DOC, execute_sql, query_db

load_dotenv()  # load OPENAI_API_KEY from .env if present

# Tracing: the Agents SDK already uploads traces to the OpenAI dashboard
# (https://platform.openai.com/traces). Set SECOND_BRAIN_VERBOSE=1 to ALSO print
# a live trace of every agent + tool call to the terminal.
if os.getenv("SECOND_BRAIN_VERBOSE") == "1":
    enable_verbose_stdout_logging()

MODEL = "gpt-5.4-mini"
WORKFLOW_NAME = "Second Brain"


def new_session_id():
    """A trace group id so one conversation's turns cluster together."""
    return "second-brain-" + uuid.uuid4().hex[:12]


def make_run_config(group_id=None):
    """Name and group this run's trace for the OpenAI Traces dashboard."""
    return RunConfig(workflow_name=WORKFLOW_NAME, group_id=group_id)


def _with_prefs(text):
    """Append the user's standing instructions (memory.md) to an agent prompt."""
    prefs_text = load_preferences()
    if prefs_text:
        text += (
            "\n\n# The user's standing instructions (ALWAYS follow these):\n"
            + prefs_text
        )
    return text


def _sql_instructions(ctx, agent):
    """Dynamic instructions so the SQL agent always knows today's date."""
    return _with_prefs(
        f"Today is {date.today().isoformat()}.\n\n"
        "You turn a memory the user wants to keep into SQL and store it via the "
        "execute_sql tool. Write against this schema:\n\n"
        f"{SCHEMA_DOC}\n"
        "Rules:\n"
        "- INSERT into `entries`. Put the user's words in raw_text and pick a "
        "sensible `type` ('note', 'expense', 'link', ...).\n"
        "- ALWAYS set created_at to the SQL expression "
        "strftime('%Y-%m-%dT%H:%M:%SZ','now') (copy it verbatim and UNQUOTED) so "
        "the database stamps the real recording time — never write a literal "
        "date. Set occurred_at to when the event happened: a date (default "
        "today), or a full 'YYYY-MM-DDTHH:MM:SS' timestamp if the user mentions a "
        "time. For expenses, fill amount/currency/category.\n"
        "- Add tags when there's an obvious theme (lowercase), linking via "
        "entry_tags as shown in the schema notes.\n"
        "- Run ONE statement per execute_sql call. If it returns ok=false, READ "
        "the error, fix the SQL, and try again (a few attempts at most).\n"
        "- Use only INSERT and SELECT — never UPDATE, DELETE, or DROP.\n"
        "- When finished, reply with a one-line confirmation of what you stored."
    )


sql_agent = Agent(
    name="SQL Writer",
    model=MODEL,
    instructions=_sql_instructions,
    tools=[execute_sql],
)


def _recall_instructions(ctx, agent):
    """Dynamic instructions for the read-only lookup agent."""
    today = date.today().isoformat()
    return _with_prefs(
        f"Today is {today}.\n\n"
        "You answer questions about the user's stored memories by querying the "
        "database READ-ONLY with the query_db tool. Schema:\n\n"
        f"{SCHEMA_DOC}\n"
        "Guidance:\n"
        "- Write SELECT statements only. For fuzzy questions, cast a WIDE net: "
        "use LIKE on raw_text (e.g. raw_text LIKE '%dsa%' OR raw_text LIKE "
        "'%google%') and/or join tags through entry_tags.\n"
        "- Treat dates as SOFT hints, not hard filters. If the user names a topic "
        "(e.g. 'google dsa'), search by that topic first via LIKE/tags, and use "
        "the date only to rank relevance. A closely related entry may be dated a "
        f"day or two earlier — don't let an exact date hide it (today = '{today}'). "
        "occurred_at may be a date OR a full timestamp, so match by date prefix, "
        "e.g. occurred_at LIKE '2026-06-21%'.\n"
        "- If a query returns little, DROP the date filter and broaden the topic "
        "terms, then try again before giving up. If query_db returns ok=false, "
        "read the error and fix the SQL.\n"
        "- Report the concrete details you found — quote any full URL verbatim "
        "(from raw_text or the `extra` JSON), plus dates and tags. Prefer the "
        "most complete matching entry (e.g. the one that actually contains the "
        "link). If truly nothing matches, say so plainly."
    )


recall_agent = Agent(
    name="Memory Lookup",
    model=MODEL,
    instructions=_recall_instructions,
    tools=[query_db],
)


def _router_instructions(ctx, agent):
    return _with_prefs(
        "You are Second Brain, the user's personal memory assistant. For each "
        "message, decide which one applies:\n"
        "- STORE: the user is telling you something to remember (a note, event, "
        "expense, link, fact about their life). Call `store_memory` with the full "
        "detail, then confirm warmly in one line.\n"
        "- RECALL: the user is asking about their own past or stored data (what / "
        "when / where / how much, 'did I', 'show me', totals, insights). Call "
        "`recall_memories` with a clear description of what they're looking for, "
        "then answer using ONLY what it returns — relay the specific details, "
        "including any full link/URL verbatim, rather than over-summarizing. You "
        "must NEVER say you don't know or don't remember before calling "
        "`recall_memories` first.\n"
        "- PREFERENCE: the user is telling you HOW to behave from now on "
        "('from now on...', 'always...', 'when I say X do Y', 'you should...'). "
        "Call `save_preference` with the rule, then confirm in one line. This is "
        "different from STORE — preferences are behaviour rules, not facts/events.\n"
        "- ASK: a store or recall is ambiguous or missing info (e.g. an expense "
        "with no amount). Ask one short follow-up question instead of guessing.\n"
        "- CHAT: otherwise just talk, briefly and warmly.\n"
        "Never invent details the user didn't give you or that aren't in the "
        "database."
    )


router_agent = Agent(
    name="Second Brain",
    model=MODEL,
    instructions=_router_instructions,
    tools=[
        sql_agent.as_tool(
            tool_name="store_memory",
            tool_description=(
                "Persist something the user wants to remember (note, event, "
                "expense, link, etc.). Pass the full natural-language detail, "
                "including any amount, date, or theme the user mentioned."
            ),
        ),
        recall_agent.as_tool(
            tool_name="recall_memories",
            tool_description=(
                "Look up the user's stored memories, expenses, links, and notes "
                "to answer a question about their past or their data. Pass a "
                "clear description of what to find."
            ),
        ),
        save_preference,
    ],
)
