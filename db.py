"""SQLite storage for Second Brain memories.

Everything the user tells us — a plain note ("went to school today") or an
expense ("netflix, 649") — lands in ONE table, `entries`. The common-but-
important fields (amount, currency, category, occurred_at, due_at) are real
columns so finance queries stay clean SQL; anything type-specific goes in the
JSON `payload` column.

The `type` of an entry ('note', 'expense', 'link', ...) is drawn from a small
controlled vocabulary held in the `types` table, and `entries.type` is a
foreign key onto it. This keeps the vocabulary consistent — a typo'd type is
rejected rather than silently fragmenting a dashboard's GROUP BY — while still
letting the vocabulary GROW: the store agent may register a genuinely new type
(and tells the user when it does). We seed the ones already in use; the rest
grow on demand.
"""

import json
import sqlite3
from datetime import date, datetime, timezone

DEFAULT_DB_PATH = "brain.db"

# The types we always start with. The vocabulary can grow beyond these (the
# store agent registers a new type when nothing fits), but `note` is the
# catch-all every entry can safely fall back to.
DEFAULT_TYPES = ("note", "expense", "link")

# The `entries` column definitions, shared verbatim by the fresh-DB schema and
# the migration's table rebuild so the two can never drift apart.
_ENTRIES_COLUMNS = """\
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- when we recorded it: a precise UTC instant, auto-stamped if omitted. This
    -- is a timestamp, NOT a calendar day — do not filter "today" on it (it can
    -- be a day off from the user's local date). Use occurred_at for that.
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    type        TEXT NOT NULL REFERENCES types(name),  -- controlled vocabulary
    raw_text    TEXT NOT NULL,   -- exactly what the user said
    -- the calendar day the event belongs to, in the user's LOCAL date; this is
    -- what "today"/"yesterday"/date queries filter on. Defaults to the local
    -- day (NOT UTC) so day-based recall stays correct even if it's omitted.
    occurred_at TEXT DEFAULT (date('now', 'localtime')),
    amount      REAL,            -- finance: how much
    currency    TEXT,            -- finance: e.g. 'INR'
    category    TEXT,            -- finance: e.g. 'subscription'
    due_at      TEXT,            -- reserved for the reminders phase
    payload     TEXT             -- JSON blob for type-specific fields
"""

SCHEMA = f"""
-- The allowed entry types. `entries.type` is a foreign key onto this, so the
-- set of types is a small controlled vocabulary rather than free-form text.
CREATE TABLE IF NOT EXISTS types (
    name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS entries (
{_ENTRIES_COLUMNS}
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

    The read-only mode is how the text-to-SQL `query_db` tool stays safe — the
    model can SELECT freely but cannot mutate the database.
    """
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")  # honor the FKs / cascades
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    """Create the schema (if missing) and migrate any old-shaped DB in place.

    Safe to call on every run: it is idempotent on an already-current database.
    """
    # 1. Ensure the vocabulary table exists and the defaults are present. This
    #    must come first because `entries.type` is a foreign key onto it.
    conn.execute("CREATE TABLE IF NOT EXISTS types (name TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT OR IGNORE INTO types (name) VALUES (?)",
        [(t,) for t in DEFAULT_TYPES],
    )
    conn.commit()

    # 2. Upgrade a legacy `entries` table (had `extra`, free-form `type`, no FK).
    _migrate_entries_if_needed(conn)

    # 3. Create anything still missing (the fresh-database path).
    conn.executescript(SCHEMA)
    conn.commit()


def _migrate_entries_if_needed(conn):
    """Bring a legacy `entries` table up to the current schema, preserving data.

    Old databases have an `extra` column and a free-form `type` with no foreign
    key (and, in a later revision, no default on `occurred_at`). SQLite can't
    add a foreign key or a column default with ALTER, so we rebuild the table:
    register every existing type into `types` (so the new FK is satisfiable),
    then copy every row into a fresh table with the current shape, mapping the
    old `extra` column onto `payload`. Ids are preserved, so `entry_tags` links
    stay valid. No-op once the table is already current.
    """
    info = {row["name"]: row for row in conn.execute("PRAGMA table_info(entries)")}
    if not info:
        return  # fresh database — nothing to migrate

    has_payload = "payload" in info
    has_type_fk = any(
        fk["table"] == "types"
        for fk in conn.execute("PRAGMA foreign_key_list(entries)")
    )
    occurred = info.get("occurred_at")
    has_occurred_default = occurred is not None and occurred["dflt_value"] is not None
    if has_payload and has_type_fk and has_occurred_default:
        return  # already current

    # Register every type already in use so the new foreign key is satisfiable.
    conn.execute(
        "INSERT OR IGNORE INTO types (name) SELECT DISTINCT type FROM entries"
    )
    conn.commit()

    payload_src = "payload" if has_payload else "extra"
    # Foreign keys must be OFF to DROP the referenced `entries` table; the
    # pragma can't be toggled inside a transaction, so set it before the script.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        f"""
        BEGIN;
        CREATE TABLE entries_new (
{_ENTRIES_COLUMNS}
        );
        INSERT INTO entries_new
            (id, created_at, type, raw_text, occurred_at,
             amount, currency, category, due_at, payload)
        SELECT id, created_at, type, raw_text, occurred_at,
               amount, currency, category, due_at, {payload_src}
        FROM entries;
        DROP TABLE entries;
        ALTER TABLE entries_new RENAME TO entries;
        COMMIT;
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")


def get_types(conn):
    """Return the current type vocabulary, sorted."""
    return [row["name"] for row in conn.execute("SELECT name FROM types ORDER BY name")]


def get_tags(conn):
    """Return the tags currently in use, most-used first (ties sorted by name).

    Ordering by usage keeps the most relevant tags at the front, which matters
    when the list is injected into an agent prompt.
    """
    return [
        row["name"]
        for row in conn.execute(
            "SELECT t.name FROM tags t "
            "LEFT JOIN entry_tags et ON et.tag_id = t.id "
            "GROUP BY t.id ORDER BY COUNT(et.entry_id) DESC, t.name"
        )
    ]


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
    payload=None,
    tags=None,
):
    """Insert one memory and return its new id.

    `occurred_at` defaults to today; `payload` (a dict) is stored as JSON;
    `tags` (a list of strings) are normalized, de-duplicated, and linked. The
    `type` is registered in the vocabulary first so the foreign key holds.
    """
    if occurred_at is None:
        occurred_at = date.today().isoformat()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload_json = json.dumps(payload) if payload is not None else None

    _ensure_type(conn, type)
    cur = conn.execute(
        """
        INSERT INTO entries
            (created_at, type, raw_text, occurred_at,
             amount, currency, category, due_at, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, type, raw_text, occurred_at,
         amount, currency, category, due_at, payload_json),
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


def _ensure_type(conn, name):
    """Register `name` in the type vocabulary if it isn't already there."""
    conn.execute("INSERT OR IGNORE INTO types (name) VALUES (?)", (name,))


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
    """Turn a sqlite3.Row into a plain dict, parsing the JSON `payload`."""
    d = dict(row)
    if d.get("payload") is not None:
        d["payload"] = json.loads(d["payload"])
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
