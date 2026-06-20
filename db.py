"""SQLite storage for Second Brain memories.

Everything the user tells us — a plain note ("went to school today") or an
expense ("netflix, 649") — lands in ONE table, `entries`. The common-but-
important fields (amount, currency, category, occurred_at, due_at) are real
columns so finance queries stay clean SQL; anything type-specific goes in the
JSON `extra` column. Adding a new *kind* of memory needs no schema change —
just a new `type` string.
"""

import json
import sqlite3
from datetime import date, datetime, timezone

DEFAULT_DB_PATH = "brain.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,   -- when we recorded it (UTC, ISO-8601)
    type        TEXT NOT NULL,   -- 'note', 'expense', ... (free-form)
    raw_text    TEXT NOT NULL,   -- exactly what the user said
    occurred_at TEXT,            -- date the event happened (YYYY-MM-DD)
    amount      REAL,            -- finance: how much
    currency    TEXT,            -- finance: e.g. 'INR'
    category    TEXT,            -- finance: e.g. 'subscription'
    due_at      TEXT,            -- reserved for the reminders phase
    extra       TEXT             -- JSON blob for type-specific fields
);

-- Tags are cross-cutting labels (e.g. 'job-search', 'google') that apply
-- across every type. They hold no content — only the labelling — so a tag
-- like 'job-search' can span a note, a link, and an expense at once.
CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE   -- normalized (lower/stripped), stored once
);

CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
    PRIMARY KEY (entry_id, tag_id)
);
"""


def connect(path=DEFAULT_DB_PATH, *, readonly=False):
    """Open a connection. `readonly=True` physically blocks writes.

    The read-only mode is how the future text-to-SQL `run_query` tool stays
    safe — the model can SELECT freely but cannot mutate the database.
    """
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")  # honor entry_tags cascades
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    """Create the `entries` table if it does not already exist."""
    conn.executescript(SCHEMA)
    conn.commit()


def add_entry(
    conn,
    *,
    type,
    raw_text,
    occurred_at=None,
    amount=None,
    currency=None,
    category=None,
    due_at=None,
    extra=None,
    tags=None,
):
    """Insert one memory and return its new id.

    `occurred_at` defaults to today; `extra` (a dict) is stored as JSON;
    `tags` (a list of strings) are normalized, de-duplicated, and linked.
    """
    if occurred_at is None:
        occurred_at = date.today().isoformat()
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    extra_json = json.dumps(extra) if extra is not None else None

    cur = conn.execute(
        """
        INSERT INTO entries
            (created_at, type, raw_text, occurred_at,
             amount, currency, category, due_at, extra)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, type, raw_text, occurred_at,
         amount, currency, category, due_at, extra_json),
    )
    entry_id = cur.lastrowid

    for name in _normalize_tags(tags):
        tag_id = _upsert_tag(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO entry_tags (entry_id, tag_id) VALUES (?, ?)",
            (entry_id, tag_id),
        )

    conn.commit()
    return entry_id


def _normalize_tags(tags):
    """Lower/strip tags, drop blanks, de-duplicate while keeping order."""
    seen = []
    for raw in tags or []:
        name = raw.strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def _upsert_tag(conn, name):
    """Return the id of `name`, inserting it into `tags` if it's new."""
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    return conn.execute(
        "SELECT id FROM tags WHERE name = ?", (name,)
    ).fetchone()["id"]


def _row_to_dict(row):
    """Turn a sqlite3.Row into a plain dict, parsing the JSON `extra`."""
    d = dict(row)
    if d.get("extra") is not None:
        d["extra"] = json.loads(d["extra"])
    return d


def get_entries(conn, *, type=None, tag=None, limit=None):
    """Fetch entries (most recent first), optionally filtered by type and/or tag.

    Each returned entry carries a `tags` list (sorted, possibly empty).
    """
    sql = "SELECT * FROM entries"
    where = []
    params = []
    if type is not None:
        where.append("type = ?")
        params.append(type)
    if tag is not None:
        where.append(
            "id IN (SELECT et.entry_id FROM entry_tags et "
            "JOIN tags t ON t.id = et.tag_id WHERE t.name = ?)"
        )
        params.append(tag.strip().lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    entries = [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    _attach_tags(conn, entries)
    return entries


def _attach_tags(conn, entries):
    """Populate each entry's `tags` list (sorted by name) in one query."""
    for entry in entries:
        entry["tags"] = []
    if not entries:
        return

    by_id = {entry["id"]: entry for entry in entries}
    placeholders = ",".join("?" for _ in by_id)
    rows = conn.execute(
        f"SELECT et.entry_id, t.name FROM entry_tags et "
        f"JOIN tags t ON t.id = et.tag_id "
        f"WHERE et.entry_id IN ({placeholders}) "
        f"ORDER BY t.name",
        tuple(by_id),
    ).fetchall()
    for row in rows:
        by_id[row["entry_id"]]["tags"].append(row["name"])
