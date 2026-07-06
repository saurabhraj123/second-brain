"""Task subsystem storage for Second Brain.

Tasks are a different shape from memories: they are mutable (open ->
in-progress -> done/cancelled), relational (a task lives in a project, a
project in an organization), and stateful. So they get their OWN tables here,
beside the append-only `entries` model in the same `brain.db`.

Every task is contained: task -> project -> organization, enforced by foreign
keys (no orphans). Capture stays frictionless via seeded defaults — a task with
no project given lands in the "Inbox" project under the "Personal" org.

Writes happen only through the typed functions here (never free-form UPDATE from
the model); reads reuse the read-only SQL path.
"""

import calendar
from datetime import date, timedelta

DEFAULT_ORG = "Personal"
DEFAULT_PROJECT = "Inbox"
TASK_STATUSES = ("open", "in-progress", "done", "cancelled")
RECUR_FREQS = ("daily", "weekly", "monthly")

TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS projects (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL,
    org_id INTEGER NOT NULL REFERENCES organizations(id),
    UNIQUE (name, org_id)
);

-- Controlled vocabulary of task statuses (same pattern as db.types).
CREATE TABLE IF NOT EXISTS task_statuses (
    name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'open' REFERENCES task_statuses(name),
    due_at        TEXT,        -- local calendar day/time (like entries.occurred_at)
    priority      TEXT,
    project_id    INTEGER NOT NULL REFERENCES projects(id),
    parent_id     INTEGER REFERENCES tasks(id),   -- NULL = top-level; set = subtask
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),  -- UTC
    completed_at  TEXT,
    recur_freq    TEXT,               -- NULL = one-off; else 'daily'|'weekly'|'monthly'
    recur_interval INTEGER DEFAULT 1, -- every N of that unit (e.g. daily+3 = every 3 days)
    payload       TEXT
);

-- Task-scoped attachments: an image/link/file with an optional description.
-- Deleting a task removes its attachments. (Standalone bookmarks are memories,
-- not attachments — they live in the `entries` link type.)
CREATE TABLE IF NOT EXISTS attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    type         TEXT NOT NULL DEFAULT 'link' CHECK (type IN ('image', 'link', 'file')),
    url          TEXT NOT NULL,
    description  TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# Columns a caller may set through update_task (project is resolved separately).
_UPDATABLE = (
    "title", "description", "status", "due_at", "priority", "parent_id",
    "recur_freq", "recur_interval",
)


def init_tasks(conn):
    """Create the task tables (if missing) and seed statuses + default org/project.

    Idempotent — safe to call on every run.
    """
    conn.executescript(TASK_SCHEMA)
    _migrate_task_columns(conn)
    conn.executemany(
        "INSERT OR IGNORE INTO task_statuses (name) VALUES (?)",
        [(s,) for s in TASK_STATUSES],
    )
    ensure_org(conn, DEFAULT_ORG)
    ensure_project(conn, DEFAULT_PROJECT, DEFAULT_ORG)
    conn.commit()


def _migrate_task_columns(conn):
    """Add columns introduced after the tasks table first shipped.

    SQLite's CREATE TABLE IF NOT EXISTS won't alter an existing table, so newer
    columns (the recurrence rule) are added here with ALTER — cheap and safe for
    nullable/defaulted columns (no table rebuild needed).
    """
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)")]
    if "recur_freq" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN recur_freq TEXT")
    if "recur_interval" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN recur_interval INTEGER DEFAULT 1")


def get_statuses(conn):
    """Return the task-status vocabulary."""
    return [r["name"] for r in conn.execute("SELECT name FROM task_statuses ORDER BY name")]


def get_projects(conn):
    """Return the existing projects as 'project (org)' strings, for prompt context."""
    return [
        f"{r['name']} ({r['org']})"
        for r in conn.execute(
            "SELECT p.name, o.name AS org FROM projects p "
            "JOIN organizations o ON o.id = p.org_id ORDER BY o.name, p.name"
        )
    ]


def ensure_org(conn, name):
    """Return the id of organization `name`, creating it if new."""
    conn.execute("INSERT OR IGNORE INTO organizations (name) VALUES (?)", (name,))
    return conn.execute(
        "SELECT id FROM organizations WHERE name = ?", (name,)
    ).fetchone()["id"]


def ensure_project(conn, name, org=None):
    """Return the id of project `name` under org `org` (default org), creating either if new."""
    org_id = ensure_org(conn, org or DEFAULT_ORG)
    conn.execute(
        "INSERT OR IGNORE INTO projects (name, org_id) VALUES (?, ?)", (name, org_id)
    )
    return conn.execute(
        "SELECT id FROM projects WHERE name = ? AND org_id = ?", (name, org_id)
    ).fetchone()["id"]


def create_task(
    conn,
    *,
    title,
    description=None,
    project=None,
    org=None,
    due_at=None,
    priority=None,
    parent_id=None,
    status="open",
    recur_freq=None,
    recur_interval=1,
):
    """Create a task and return its id.

    With no `project`, the task lands in the default Inbox project under the
    default org — so a bare capture still satisfies the task -> project -> org
    invariant without the caller specifying anything. Pass `recur_freq`
    ('daily'/'weekly'/'monthly') + `recur_interval` to make it recurring;
    completing such a task spawns the next occurrence (see complete_task).
    """
    if recur_freq is not None and recur_freq not in RECUR_FREQS:
        raise ValueError(f"recur_freq must be one of {RECUR_FREQS}, got {recur_freq!r}")

    if parent_id is not None:
        # A subtask always lives in its parent's project — inherit it.
        parent = get_task(conn, parent_id)
        if parent is None:
            raise ValueError(f"no parent task with id {parent_id}")
        project_id = parent["project_id"]
    elif project is None:
        project_id = ensure_project(conn, DEFAULT_PROJECT, DEFAULT_ORG)
    else:
        project_id = ensure_project(conn, project, org)

    cur = conn.execute(
        """
        INSERT INTO tasks (title, description, status, due_at, priority,
                           project_id, parent_id, recur_freq, recur_interval)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (title, description, status, due_at, priority, project_id, parent_id,
         recur_freq, recur_interval),
    )
    conn.commit()
    return cur.lastrowid


def update_task(conn, task_id, **fields):
    """Update whitelisted columns of a task. `project` (and optional `org`) move it."""
    sets, params = [], []

    if "project" in fields:
        project = fields.pop("project")
        org = fields.pop("org", None)
        sets.append("project_id = ?")
        params.append(ensure_project(conn, project, org))

    for key, value in fields.items():
        if key not in _UPDATABLE:
            raise ValueError(f"cannot update unknown task field: {key}")
        sets.append(f"{key} = ?")
        params.append(value)

    if not sets:
        return
    params.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def _add_months(d, n):
    """Return date `d` advanced by `n` months, clamped to the target month's end."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _next_date(date_str, freq, interval=1):
    """Advance a 'YYYY-MM-DD[...]' date by one recurrence step; return 'YYYY-MM-DD'."""
    d = date.fromisoformat(date_str[:10])
    if freq == "daily":
        d = d + timedelta(days=interval)
    elif freq == "weekly":
        d = d + timedelta(weeks=interval)
    elif freq == "monthly":
        d = _add_months(d, interval)
    else:
        raise ValueError(f"unknown recurrence freq: {freq}")
    return d.isoformat()


def complete_task(conn, task_id):
    """Mark a task done (stamping completed_at, UTC).

    If the task is recurring and has a due date, spawn the next occurrence (same
    title/project/priority/rule, with the next due date) and return its id.
    Otherwise return None.
    """
    t = get_task(conn, task_id)
    if t is None:
        return None

    conn.execute(
        "UPDATE tasks SET status = 'done', "
        "completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (task_id,),
    )

    next_id = None
    if t["recur_freq"] and t["due_at"]:
        next_due = _next_date(t["due_at"], t["recur_freq"], t["recur_interval"] or 1)
        cur = conn.execute(
            """
            INSERT INTO tasks (title, description, status, due_at, priority,
                               project_id, recur_freq, recur_interval)
            VALUES (?, ?, 'open', ?, ?, ?, ?, ?)
            """,
            (t["title"], t["description"], next_due, t["priority"],
             t["project_id"], t["recur_freq"], t["recur_interval"]),
        )
        next_id = cur.lastrowid

    conn.commit()
    return next_id


def _row_to_task(row):
    """sqlite3.Row -> dict, parsing the JSON payload."""
    import json

    d = dict(row)
    if d.get("payload") is not None:
        d["payload"] = json.loads(d["payload"])
    return d


_SELECT = """
    SELECT t.*, p.name AS project, o.name AS org
    FROM tasks t
    JOIN projects p ON p.id = t.project_id
    JOIN organizations o ON o.id = p.org_id
"""


def get_task(conn, task_id):
    """Return one task (with project/org names), or None."""
    row = conn.execute(f"{_SELECT} WHERE t.id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def get_subtasks(conn, parent_id):
    """Return the direct subtasks of a task (newest first)."""
    return [
        _row_to_task(r)
        for r in conn.execute(f"{_SELECT} WHERE t.parent_id = ? ORDER BY t.id DESC", (parent_id,))
    ]


def subtask_progress(conn, parent_id):
    """Return {'done': m, 'total': n} over a task's direct subtasks."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(status = 'done'), 0) AS done "
        "FROM tasks WHERE parent_id = ?",
        (parent_id,),
    ).fetchone()
    return {"done": row["done"], "total": row["total"]}


def add_attachment(conn, task_id, *, url, type="link", description=None):
    """Attach an image/link/file to a task and return the attachment id."""
    cur = conn.execute(
        "INSERT INTO attachments (task_id, type, url, description) VALUES (?, ?, ?, ?)",
        (task_id, type, url, description),
    )
    conn.commit()
    return cur.lastrowid


def get_attachments(conn, task_id):
    """Return a task's attachments (oldest first)."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT id, task_id, type, url, description, created_at "
            "FROM attachments WHERE task_id = ? ORDER BY id",
            (task_id,),
        )
    ]


def get_tasks(conn, *, status=None, project=None, query=None):
    """Return tasks (newest first), filtered by status, project, and/or a free-text
    `query` matched against the title and description."""
    where, params = [], []
    if status is not None:
        where.append("t.status = ?")
        params.append(status)
    if project is not None:
        where.append("p.name = ?")
        params.append(project)
    if query:
        where.append("(t.title LIKE ? OR t.description LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]

    sql = _SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.id DESC"
    return [_row_to_task(r) for r in conn.execute(sql, params).fetchall()]
