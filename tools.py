"""Tools the agents call to read and write the Second Brain database.

`run_sql` is the testable core: it executes one statement and *never* raises on
a SQL error — failures come back as a readable result so the storing agent can
read the message, rewrite its query, and try again (the feedback loop).

`execute_sql` is the thin `@function_tool` wrapper the SQL agent actually calls;
`SCHEMA_DOC` is the schema description we hand the SQL agent so it writes valid
statements.
"""

import json

from agents import function_tool

import db
import tasks

# Handed to the SQL agent so it knows what it's writing against.
SCHEMA_DOC = """\
types  -- the controlled vocabulary of entry types (one row per allowed type)
  name         TEXT PRIMARY KEY   -- e.g. 'note', 'expense', 'link'

entries  -- one row per memory (note, expense, link, ...)
  id           INTEGER PRIMARY KEY (auto)
  created_at   TEXT   -- recording time, a UTC instant. ALWAYS set this to the SQL
                      --   expression strftime('%Y-%m-%dT%H:%M:%SZ','now') (unquoted)
                      --   so the database fills the real current UTC time. This is
                      --   a timestamp, NOT a calendar day: never filter 'today' on
                      --   it (it can be a day off from the user's local date).
  type         TEXT   -- FK -> types(name). MUST already exist in `types`, else
                      --   the INSERT fails. Reuse an existing type; only create
                      --   a genuinely new one (see the store rules).
  raw_text     TEXT   -- the user's words / a short description
  occurred_at  TEXT   -- the calendar day the event belongs to, in the user's LOCAL
                      --   date 'YYYY-MM-DD' (defaults to the local today if you omit
                      --   it), OR a full 'YYYY-MM-DDTHH:MM:SS' if the user gives a
                      --   time. THIS is the column to filter for 'today'/'yesterday'.
  amount       REAL   -- expenses: numeric amount (else NULL)
  currency     TEXT   -- expenses: e.g. 'INR' (else NULL)
  category     TEXT   -- expenses: e.g. 'food', 'subscription' (else NULL)
  due_at       TEXT   -- reserved; leave NULL
  payload      TEXT   -- JSON string for type-specific display fields, e.g. '{"url": "..."}'

tags        (id INTEGER PK, name TEXT UNIQUE)   -- lowercase labels, stored once
entry_tags  (entry_id INTEGER, tag_id INTEGER)  -- links entries to tags (many-to-many)

To register a NEW type (only when no existing type fits), before the entry INSERT:
  INSERT OR IGNORE INTO types (name) VALUES ('reminder');   -- lowercase, singular

To tag an entry, run these as separate execute_sql calls:
  1. INSERT INTO entries (...) VALUES (...);          -- note the returned lastrowid
  2. INSERT OR IGNORE INTO tags (name) VALUES ('google');
  3. INSERT INTO entry_tags (entry_id, tag_id)
       VALUES (<entry lastrowid>, (SELECT id FROM tags WHERE name='google'));
"""


def run_sql(conn, query):
    """Execute one SQL statement on `conn` and return a result dict.

    Never raises on a SQL error — returns {"ok": False, "error": "..."} instead,
    and leaves the connection usable so the caller can retry a corrected query.
    """
    try:
        cur = conn.execute(query)
        if cur.description is not None:  # a row-returning statement (SELECT)
            return {"ok": True, "rows": [dict(r) for r in cur.fetchall()]}
        conn.commit()
        return {"ok": True, "rowcount": cur.rowcount, "lastrowid": cur.lastrowid}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def query_readonly(query, path=db.DEFAULT_DB_PATH):
    """Run a query over a READ-ONLY connection, returning a result dict.

    Ensures the database exists first (so recall on a brand-new DB returns
    empty rather than erroring), then opens it read-only — any write the model
    attempts comes back as an error instead of mutating anything.
    """
    rw = db.connect(path)
    db.init_db(rw)  # make sure the file + tables exist
    rw.close()

    conn = db.connect(path, readonly=True)
    try:
        return run_sql(conn, query)
    finally:
        conn.close()


@function_tool
def execute_sql(query: str) -> str:
    """Run ONE SQL statement against the Second Brain database to store a memory.

    Returns JSON: {"ok": true, "lastrowid": N, ...} on success, or
    {"ok": false, "error": "..."} on failure. If it fails, read the error,
    fix the SQL, and call this again. Use only INSERT and SELECT.
    """
    conn = db.connect()  # read-write: this tool is allowed to write
    try:
        db.init_db(conn)  # ensure the tables exist
        return json.dumps(run_sql(conn, query))
    finally:
        conn.close()


@function_tool
def query_db(query: str) -> str:
    """Run ONE read-only SQL SELECT to look up the user's stored memories.

    Returns JSON: {"ok": true, "rows": [...]} on success, or
    {"ok": false, "error": "..."} on failure. Writes are refused — use SELECT
    only. If it fails, read the error, fix the SQL, and call this again.
    """
    return json.dumps(query_readonly(query))


# --- Tasks -----------------------------------------------------------------
#
# Task WRITES go through these typed tools (never free-form UPDATE from the
# model), which keeps the memory path's append-only safety intact. Task READS
# ("show my open tasks") reuse query_db against the tables documented below.

# Handed to the recall agent so it can answer questions about tasks read-only.
TASK_SCHEMA_DOC = """\
organizations (id, name)                       -- e.g. 'Personal' (default), 'Toddle'
projects      (id, name, org_id -> organizations.id)   -- e.g. 'Inbox' (default)
task_statuses (name)                           -- 'open' | 'in-progress' | 'done' | 'cancelled'
tasks
  id           INTEGER PRIMARY KEY
  title        TEXT
  description  TEXT
  status       TEXT   -- FK -> task_statuses(name)
  due_at       TEXT   -- the local calendar day/time it's due (like entries.occurred_at)
  priority     TEXT
  project_id   INTEGER -> projects.id
  parent_id    INTEGER -> tasks.id   -- NULL = top-level; set = subtask
  created_at   TEXT   -- UTC recording instant (do NOT filter 'today' on it)
  completed_at TEXT   -- set when done
attachments
  id, task_id -> tasks.id, type ('image'|'link'|'file'), url, description, created_at
  -- e.g. "tasks with images": SELECT DISTINCT t.* FROM tasks t
  --      JOIN attachments a ON a.task_id=t.id WHERE a.type='image';
To list tasks, join through projects/organizations for their names, e.g.
  SELECT t.title, t.status, t.due_at, p.name AS project, o.name AS org
  FROM tasks t JOIN projects p ON p.id=t.project_id
               JOIN organizations o ON o.id=p.org_id
  WHERE t.status='open';
"""


def _task_write(fn):
    """Run a task-mutating callable, returning the {ok, ...} result dict.

    Opens a read-write connection, ensures the schema exists, and turns any
    error into a readable result (never raises) so the agent can recover.
    """
    conn = db.connect()
    try:
        db.init_db(conn)
        return fn(conn)
    except Exception as e:  # e.g. an invalid status trips the FK
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()


@function_tool
def create_task(
    title: str,
    description: str = "",
    project: str = "",
    org: str = "",
    due_at: str = "",
    priority: str = "",
    parent_task_id: int = 0,
) -> str:
    """Create a task/to-do and return it as JSON.

    Only `title` is required. Omit `project`/`org` to file it in the default
    Inbox under Personal. `due_at` is the local day/time it's due ('YYYY-MM-DD'
    or a full timestamp). Pass `parent_task_id` to make it a SUBTASK of that task
    (it inherits the parent's project; `project`/`org` are ignored). Returns
    {"ok": true, "task": {...}}.
    """
    def op(conn):
        task_id = tasks.create_task(
            conn,
            title=title,
            description=description or None,
            project=project or None,
            org=org or None,
            due_at=due_at or None,
            priority=priority or None,
            parent_id=parent_task_id or None,
        )
        return {"ok": True, "task": tasks.get_task(conn, task_id)}

    return json.dumps(_task_write(op))


@function_tool
def update_task(
    task_id: int,
    status: str = "",
    due_at: str = "",
    priority: str = "",
    project: str = "",
    title: str = "",
    description: str = "",
) -> str:
    """Update fields of an existing task (found via a prior lookup). Pass only the
    fields to change; blanks are ignored. `status` must be one of open/
    in-progress/done/cancelled. Returns {"ok": true, "task": {...}}.
    """
    def op(conn):
        if tasks.get_task(conn, task_id) is None:
            return {"ok": False, "error": f"no task with id {task_id}"}
        fields = {
            k: v
            for k, v in {
                "status": status,
                "due_at": due_at,
                "priority": priority,
                "project": project,
                "title": title,
                "description": description,
            }.items()
            if v
        }
        tasks.update_task(conn, task_id, **fields)
        return {"ok": True, "task": tasks.get_task(conn, task_id)}

    return json.dumps(_task_write(op))


@function_tool
def complete_task(task_id: int) -> str:
    """Mark a task done (sets status='done' and stamps completed_at). Returns
    {"ok": true, "task": {...}}.
    """
    def op(conn):
        if tasks.get_task(conn, task_id) is None:
            return {"ok": False, "error": f"no task with id {task_id}"}
        tasks.complete_task(conn, task_id)
        return {"ok": True, "task": tasks.get_task(conn, task_id)}

    return json.dumps(_task_write(op))


@function_tool
def add_attachment(
    task_id: int, url: str, type: str = "link", description: str = ""
) -> str:
    """Attach an image, link, or file (by URL) to an existing task. `type` is one
    of 'image' / 'link' / 'file' (default 'link'). Returns
    {"ok": true, "attachment": {...}}.
    """
    def op(conn):
        if tasks.get_task(conn, task_id) is None:
            return {"ok": False, "error": f"no task with id {task_id}"}
        att_id = tasks.add_attachment(
            conn, task_id, url=url, type=type, description=description or None
        )
        return {
            "ok": True,
            "attachment": {"id": att_id, "task_id": task_id, "type": type, "url": url},
        }

    return json.dumps(_task_write(op))
