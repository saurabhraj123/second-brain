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

import db
import tasks
from prefs import load_preferences, save_preference
from tools import (
    SCHEMA_DOC,
    TASK_SCHEMA_DOC,
    add_attachment,
    complete_task,
    create_project,
    create_task,
    execute_sql,
    find_tasks,
    query_db,
    update_task,
)

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


def current_vocabulary():
    """The live type + tag vocabularies, read fresh so new ones show up.

    Injected into the specialist agents' prompts so they store and query using
    the vocabulary that actually exists rather than guessing.
    """
    conn = db.connect()
    try:
        db.init_db(conn)
        return db.get_types(conn), db.get_tags(conn)
    finally:
        conn.close()


def current_task_context():
    """The live task statuses + existing projects, so the task agent reuses them."""
    conn = db.connect()
    try:
        db.init_db(conn)
        return tasks.get_statuses(conn), tasks.get_projects(conn)
    finally:
        conn.close()


def _sql_instructions(ctx, agent):
    """Dynamic instructions so the SQL agent knows today's date and the vocabulary."""
    types, tags = current_vocabulary()
    return _with_prefs(
        f"Today is {date.today().isoformat()}.\n\n"
        "You turn a memory the user wants to keep into SQL and store it via the "
        "execute_sql tool. Write against this schema:\n\n"
        f"{SCHEMA_DOC}\n"
        f"The current entry types are: {', '.join(types)}.\n"
        f"Tags already in use: {', '.join(tags) if tags else '(none yet)'}.\n\n"
        "Rules:\n"
        "- INSERT into `entries`. Put the user's words in raw_text.\n"
        "- CHOOSING `type`: it is a foreign key onto the `types` table, so it "
        "MUST be one that already exists. STRONGLY prefer an existing type — "
        "reuse 'expense', never invent 'expenses'. Types are lowercase, "
        "singular, one word. If (and only if) none of the existing types "
        "genuinely fits, register a new one FIRST with "
        "`INSERT OR IGNORE INTO types (name) VALUES ('...')`, then use it, and "
        "note the new category in your closing confirmation (e.g. \"stored — I "
        "started a new category 'recipe'\") so the user knows. When nothing "
        "specific fits, fall back to 'note'.\n"
        "- ALWAYS set created_at to the SQL expression "
        "strftime('%Y-%m-%dT%H:%M:%SZ','now') (copy it verbatim and UNQUOTED) so "
        "the database stamps the real recording time — never write a literal "
        "date. Set occurred_at to when the event happened: a date (default "
        "today), or a full 'YYYY-MM-DDTHH:MM:SS' timestamp if the user mentions a "
        "time. For expenses, fill amount/currency/category.\n"
        "- TAGGING: add a tag only when there's an obvious theme — not every "
        "entry needs one. When a memory fits a tag ALREADY IN USE (listed above), "
        "REUSE that exact name rather than coining a near-duplicate ('job-search', "
        "not 'jobsearch'); only create a new tag when none fits. Tags are "
        "lowercase; link them via entry_tags as shown in the schema notes.\n"
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
    types, tags = current_vocabulary()
    return _with_prefs(
        f"Today is {today}.\n\n"
        "You answer questions about the user's stored memories AND their tasks by "
        "querying the database READ-ONLY with the query_db tool. Memory schema:\n\n"
        f"{SCHEMA_DOC}\n"
        "Task schema (for questions like 'show my open tasks', 'what's due', "
        "'what's in the web-app project'):\n\n"
        f"{TASK_SCHEMA_DOC}\n"
        f"Entry types in use: {', '.join(types)}.\n"
        f"Tags in use: {', '.join(tags) if tags else '(none yet)'}.\n\n"
        "Guidance:\n"
        "- Write SELECT statements only. For fuzzy questions, cast a WIDE net: "
        "use LIKE on raw_text (e.g. raw_text LIKE '%dsa%' OR raw_text LIKE "
        "'%google%') and/or join tags through entry_tags.\n"
        "- USE THE TAG LIST ABOVE: if the user's question maps to a tag that "
        "actually exists (e.g. they ask about 'job search' and a 'job-search' tag "
        "is listed), scope by that exact tag for precision — don't guess a tag "
        "name that isn't in the list. BUT most entries are UNTAGGED, so a tag "
        "filter alone will miss them: always ALSO search raw_text with LIKE, and "
        "never rely on tags as the only filter. Similarly, only filter by a `type` "
        "that appears in the types list above.\n"
        "- DAY-BASED questions ('today', 'yesterday', a specific date or range): "
        f"filter on `occurred_at` — it is the calendar day a memory belongs to, in "
        f"the user's LOCAL date (today = '{today}'). Match by date prefix, e.g. "
        f"occurred_at LIKE '{today}%'. Do NOT filter on `created_at` for these: it "
        "is a UTC recording timestamp and is often a day off from the user's local "
        "day, so it will silently miss entries saved 'today'. If you ever truly "
        "need the recording day, convert it with date(created_at,'localtime').\n"
        "- Treat dates as SOFT hints, not hard filters. If the user names a topic "
        "(e.g. 'google dsa'), search by that topic first via LIKE/tags, and use "
        "the date only to rank relevance. A closely related entry may be dated a "
        f"day or two earlier — don't let an exact date hide it (today = '{today}'). "
        "occurred_at may be a date OR a full timestamp, so match by date prefix.\n"
        "- If a query returns little, DROP the date filter and broaden the topic "
        "terms, then try again before giving up. If query_db returns ok=false, "
        "read the error and fix the SQL.\n"
        "- Report the concrete details you found — quote any full URL verbatim "
        "(from raw_text or the `payload` JSON), plus dates and tags. Prefer the "
        "most complete matching entry (e.g. the one that actually contains the "
        "link). If truly nothing matches, say so plainly."
    )


def _task_instructions(ctx, agent):
    """Dynamic instructions for the task-management agent."""
    today = date.today().isoformat()
    statuses, projects = current_task_context()
    return _with_prefs(
        f"Today is {today}.\n\n"
        "You manage the user's tasks/to-dos/projects with the create_task, update_task, "
        "complete_task, and create_project tools. Each returns JSON; if it comes back "
        '{"ok": false, ...} read the error and try again.\n\n'
        f"Statuses: {', '.join(statuses)}.\n"
        f"Existing projects: {', '.join(projects) if projects else '(only the default Inbox)'}.\n\n"
        "Rules:\n"
        "- CREATE PROJECT: if the user specifically requests to create a project or organization "
        "(without immediately creating a task, or as a placeholder), call the `create_project` "
        "tool. If they ask to create a project but do not specify an organization, first try to "
        "infer the correct organization from the context (e.g. existing orgs, matching name "
        "characteristics, or previous context). If it is completely ambiguous and does not clearly "
        "belong to 'Personal' or any existing organization, ask a short follow-up question (e.g., "
        "'Which organization should the X project belong to?') instead of creating it in Personal. "
        "If they only ask to create an organization, leave `project` empty and specify `org`.\n"
        "- CREATE: call create_task with a concise `title` (and `description` for "
        "any extra detail). Set `due_at` to the LOCAL day/time when the user gives "
        "one ('YYYY-MM-DD', or a full timestamp if they mention a time).\n"
        "- PROJECT: if the user NAMES a project ('in the web-app project', 'under "
        "Toddle'), ALWAYS pass it as `project` (and `org` if given) — even if it's "
        "not in the list above; a new project is created automatically. If they "
        "name one already in the list, reuse that exact name. Use just the "
        "project's NAME, not the surrounding words (e.g. 'web-app', not 'web-app "
        "project'; 'home reno', not 'the home reno project'). If NO project is "
        "mentioned and none is obvious, just omit `project` and it lands in Inbox — "
        "do NOT ask which project; they can reorganize later. Bias hard toward not "
        "interrogating the user.\n"
        "- REMINDER EXCEPTION: if the user phrases it as a reminder ('remind me "
        "to…') but gives NO date/time, DO ask one short follow-up: 'when should I "
        "remind you?' — a reminder without a time can't function. (A plain to-do "
        "with no date is fine; just create it.)\n"
        "- UPDATE / COMPLETE: complete_task marks a task done; update_task changes "
        "status/due/priority/project/title. Both take the task's id. When the user "
        "names a task instead of giving an id ('mark the tax report done', 'move "
        "the login task to next week'), DO NOT ask for an id — call find_tasks with "
        "keywords from what they said to look it up yourself, then act on the "
        "match. If exactly one task matches (prefer open/in-progress ones), just do "
        "it. Only ask the user to clarify if SEVERAL plausibly match, or say you "
        "can't find it if NONE do. If the id is already clear from the recent "
        "conversation, use that directly.\n"
        "- ATTACH: to add an image/link/file to a task, call add_attachment with "
        "the task's id and the URL (set `type` to 'image', 'link', or 'file'; "
        "'link' is the default). This is for material tied to a task — a standalone "
        "bookmark the user just wants to save is a memory, not a task attachment.\n"
        "- SUBTASK: to break a task into steps, create each step with "
        "`parent_task_id` set to the parent's id (you'll need that id — recall can "
        "find it). A subtask inherits its parent's project automatically.\n"
        "- RECURRING: if the user wants it repeated ('every day', 'every Monday', "
        "'weekly', 'every 2 weeks', 'monthly', 'every 3 days'), set `recur_freq` to "
        "'daily'/'weekly'/'monthly' and `recur_interval` to N (every-3-days = "
        "daily+3, every-2-weeks = weekly+2), AND set `due_at` to the first "
        "occurrence's date (compute it, e.g. the next Monday). Completing a "
        "recurring task automatically creates the next one — when you complete one "
        "and the result includes a next_occurrence, tell the user its due date.\n"
        "- If create_task reports a NEW project was created, mention it so the user "
        "knows.\n"
        "- ALWAYS include the task's id in your confirmation, e.g. \"Added task #6: "
        "Prepare the Q3 report\". The id is how later turns refer back to it (to "
        "attach a file, add subtasks, or mark it done), so it must appear in your "
        "reply. When the user refers to 'it' or 'that task', use the id from the "
        "recent conversation."
    )


task_agent = Agent(
    name="Task Manager",
    model=MODEL,
    instructions=_task_instructions,
    tools=[
        create_task,
        find_tasks,
        update_task,
        complete_task,
        add_attachment,
        create_project,
    ],
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
        "- STORE: the user is telling you something to remember — a note, event, "
        "expense, link, fact about their life, OR a fact/idea/requirement about a "
        "project, product, or system they work on (e.g. 'for the second brain "
        "project, all traces of a conversation should be in one place'). Call "
        "`store_memory` with the full detail, then confirm warmly in one line. If "
        "store_memory reports it started a NEW category, mention that in your "
        "confirmation so the user knows a new type was created.\n"
        "- TASK: the user wants to manage tasks, projects, or organizations — "
        "including creating, updating, completing, or attaching files to a task, "
        "or creating a new project or organization ('create project Y under Toddle', "
        "'create organization Z', 'add a task…', 'mark X done'). Call `manage_task` "
        "with the full detail (what to do, any due date, any project/org) — and when "
        "the user refers to an earlier task ('it', 'that task'), pass along its id "
        "from the recent conversation. Relay manage_task's confirmation, KEEPING "
        "any task id it reports (the user needs it to refer back).\n"
        "- RECALL: the user is asking about their own past or stored data, OR "
        "asking to LIST/look-up tasks ('show my open tasks', 'what's due today', "
        "'what's in the web-app project'). Task reads go through recall too. Call "
        "`recall_memories` with a clear description of what they're looking for, "
        "passing along the exact topic/theme words the user used (these often line "
        "up with stored tags or project names, so the lookup can scope precisely), "
        "then answer using ONLY what it returns — relay the specific details, "
        "including any full link/URL verbatim, rather than over-summarizing. You "
        "must NEVER say you don't know or don't remember before calling "
        "`recall_memories` first.\n"
        "- PREFERENCE: the user is telling you how YOU (the assistant) should "
        "behave from now on — a rule about your OWN responses ('from now on...', "
        "'always show amounts in INR', 'keep replies short', 'when I say X do Y'). "
        "Call `save_preference` with the rule, then confirm in one line.\n"
        "  PREFERENCE vs STORE — the word 'should' does NOT make something a "
        "preference. Ask WHO the statement is about: if it constrains how YOU "
        "reply, it's a PREFERENCE; if it's a fact or requirement about something "
        "ELSE — the user's project, product, system, or life (even when phrased "
        "with 'should', e.g. 'the project should log traces in one place') — it's "
        "a memory, so STORE it. When genuinely torn between the two, STORE it: "
        "facts belong in the database, where they can be queried later.\n"
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
        task_agent.as_tool(
            tool_name="manage_task",
            tool_description=(
                "Create, update, or complete an actionable to-do / reminder, or create/manage "
                "projects and organizations. Pass the full detail — what to do, any due date, "
                "and any project/org name. Use for 'remind me to…', 'add a task…', 'mark X "
                "done', 'create project Y', 'create organization Z'."
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
