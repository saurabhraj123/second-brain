"""Second Brain — a personal AI memory.

Two agents working together:

- `router_agent` is the voice you talk to. For each message it decides: are you
  just chatting/asking (then it talks, and asks follow-ups when something is
  missing), or are you telling it something to REMEMBER? To store, it calls the
  `store_memory` tool.
- `store_memory` is the SQL specialist (`sql_agent`) exposed as a tool. It turns
  the memory into SQL and runs `execute_sql`. If a statement fails, it reads the
  error and rewrites the query — the retry/feedback loop.

Run:
    uv run main.py                       # interactive chat loop
    uv run main.py "paid 649 for netflix today"   # one-shot

Requires OPENAI_API_KEY (put it in .env — see .env.example).
"""

import sys
from datetime import date

from agents import Agent, Runner
from dotenv import load_dotenv

from tools import SCHEMA_DOC, execute_sql, query_db

load_dotenv()  # load OPENAI_API_KEY from .env if present

MODEL = "gpt-5.4-mini"


def _sql_instructions(ctx, agent):
    """Dynamic instructions so the SQL agent always knows today's date."""
    return (
        f"Today is {date.today().isoformat()}.\n\n"
        "You turn a memory the user wants to keep into SQL and store it via the "
        "execute_sql tool. Write against this schema:\n\n"
        f"{SCHEMA_DOC}\n"
        "Rules:\n"
        "- INSERT into `entries`. Put the user's words in raw_text and pick a "
        "sensible `type` ('note', 'expense', 'link', ...).\n"
        "- Set created_at to the current time and occurred_at to the date the "
        "event happened (default today). For expenses, fill amount/currency/"
        "category.\n"
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
    return (
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
        f"day or two earlier — don't let an exact date hide it (today = '{today}').\n"
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


router_agent = Agent(
    name="Second Brain",
    model=MODEL,
    instructions=(
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
        "- ASK: a store or recall is ambiguous or missing info (e.g. an expense "
        "with no amount). Ask one short follow-up question instead of guessing.\n"
        "- CHAT: otherwise just talk, briefly and warmly.\n"
        "Never invent details the user didn't give you or that aren't in the "
        "database."
    ),
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
    ],
)


def ask(prompt, history=None):
    """Run one turn through the router agent. Returns the SDK result object."""
    user_turn = [{"role": "user", "content": prompt}]
    return Runner.run_sync(router_agent, (history or []) + user_turn, max_turns=12)


def main():
    # One-shot mode: prompt passed on the command line.
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:])).final_output)
        return

    # Interactive mode: a chat loop that remembers the session.
    print("Second Brain — chat (type 'exit' or Ctrl-C to quit)")
    history = []
    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue
        result = ask(user_input, history)
        print(f"brain> {result.final_output}")
        history = result.to_input_list()


if __name__ == "__main__":
    main()
