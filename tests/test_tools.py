"""Tests for the SQL execution core used by the storing agent (tools.run_sql).

The agent's retry/feedback loop rests on one guarantee: a bad query must come
back as a readable error (not an exception that kills the run), and the
connection must stay usable so the agent can immediately try a fixed query.
"""

import db
from tools import query_readonly, run_sql


def _conn():
    c = db.connect(":memory:")
    db.init_db(c)
    return c


def test_valid_insert_persists_and_reports_success():
    conn = _conn()

    result = run_sql(
        conn,
        "INSERT INTO entries (created_at, type, raw_text) "
        "VALUES ('2026-06-21T00:00:00', 'note', 'ate a burger')",
    )

    assert result["ok"] is True
    assert result["lastrowid"] >= 1
    rows = db.get_entries(conn)
    assert len(rows) == 1
    assert rows[0]["raw_text"] == "ate a burger"


def test_invalid_sql_returns_error_without_raising():
    conn = _conn()

    result = run_sql(conn, "INSERT INTO nonexistent_table (x) VALUES (1)")

    assert result["ok"] is False
    assert result["error"]  # a non-empty, readable message for the model


def test_connection_usable_after_error_so_agent_can_retry():
    conn = _conn()

    bad = run_sql(conn, "INSERT INTO entries (bogus_col) VALUES (1)")
    assert bad["ok"] is False

    # The feedback loop depends on retrying on the same connection.
    good = run_sql(
        conn,
        "INSERT INTO entries (created_at, type, raw_text) "
        "VALUES ('2026-06-21T00:00:00', 'note', 'retry worked')",
    )
    assert good["ok"] is True
    assert db.get_entries(conn)[0]["raw_text"] == "retry worked"


def test_select_returns_rows():
    conn = _conn()
    run_sql(
        conn,
        "INSERT INTO entries (created_at, type, raw_text) "
        "VALUES ('2026-06-21T00:00:00', 'note', 'hello')",
    )

    result = run_sql(conn, "SELECT raw_text FROM entries")

    assert result["ok"] is True
    assert result["rows"] == [{"raw_text": "hello"}]


# --- read-only recall path (query_readonly) ---


def test_query_readonly_on_fresh_db_returns_empty(tmp_path):
    path = str(tmp_path / "fresh.db")  # does not exist yet

    result = query_readonly("SELECT * FROM entries", path)

    assert result["ok"] is True
    assert result["rows"] == []


def test_query_readonly_returns_matching_rows(tmp_path):
    path = str(tmp_path / "b.db")
    rw = db.connect(path)
    db.init_db(rw)
    db.add_entry(rw, type="link", raw_text="google dsa github", tags=["google"])
    rw.close()

    result = query_readonly(
        "SELECT raw_text FROM entries WHERE raw_text LIKE '%dsa%'", path
    )

    assert result["ok"] is True
    assert result["rows"] == [{"raw_text": "google dsa github"}]


def test_query_readonly_refuses_writes(tmp_path):
    path = str(tmp_path / "b.db")
    rw = db.connect(path)
    db.init_db(rw)
    rw.close()

    result = query_readonly(
        "INSERT INTO entries (created_at, type, raw_text) "
        "VALUES ('t', 'note', 'x')",
        path,
    )

    assert result["ok"] is False
    assert result["error"]
