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
