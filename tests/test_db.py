"""Tests for the SQLite storage layer (db.py).

Every memory — a plain note or an expense — lands in one `entries` table.
These tests pin down the round-trips and the read-only safety guarantee
that the future `run_query` tool will depend on.
"""

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

import db


@pytest.fixture
def conn():
    """A fresh in-memory database with the schema applied."""
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_add_note_round_trips(conn):
    entry_id = db.add_entry(conn, type="note", raw_text="went to school today")

    assert isinstance(entry_id, int)
    rows = db.get_entries(conn)
    assert len(rows) == 1
    assert rows[0]["type"] == "note"
    assert rows[0]["raw_text"] == "went to school today"


def test_expense_stores_money_fields(conn):
    db.add_entry(
        conn,
        type="expense",
        raw_text="netflix subscription",
        amount=649.0,
        currency="INR",
        category="subscription",
    )

    row = db.get_entries(conn)[0]
    assert row["amount"] == 649.0
    assert row["currency"] == "INR"
    assert row["category"] == "subscription"


def test_payload_json_round_trips(conn):
    db.add_entry(
        conn,
        type="note",
        raw_text="ate a burger",
        payload={"place": "the diner", "mood": "happy"},
    )

    row = db.get_entries(conn)[0]
    assert row["payload"] == {"place": "the diner", "mood": "happy"}


def test_occurred_at_defaults_to_today(conn):
    db.add_entry(conn, type="note", raw_text="something happened")

    row = db.get_entries(conn)[0]
    assert row["occurred_at"] == date.today().isoformat()


def test_occurred_at_defaults_to_local_day_on_raw_insert(conn):
    # The store agent writes raw SQL and may omit occurred_at. It must still
    # land on the user's LOCAL calendar day, because that's what "today"
    # queries filter on — created_at is UTC and can be a day behind.
    conn.execute("INSERT INTO entries (type, raw_text) VALUES ('note', 'no date given')")
    conn.commit()

    row = db.get_entries(conn)[0]
    local_today = conn.execute("SELECT date('now', 'localtime')").fetchone()[0]
    assert row["occurred_at"] == local_today


def test_occurred_at_can_be_set_explicitly(conn):
    db.add_entry(
        conn, type="note", raw_text="paid rent", occurred_at="2026-06-01"
    )

    row = db.get_entries(conn)[0]
    assert row["occurred_at"] == "2026-06-01"


def test_get_entries_filters_by_type(conn):
    db.add_entry(conn, type="note", raw_text="a note")
    db.add_entry(conn, type="expense", raw_text="a buy", amount=10.0)

    notes = db.get_entries(conn, type="note")
    assert len(notes) == 1
    assert notes[0]["type"] == "note"


def test_readonly_connection_cannot_write(tmp_path):
    path = str(tmp_path / "brain.db")

    rw = db.connect(path)
    db.init_db(rw)
    db.add_entry(rw, type="note", raw_text="seed")
    rw.close()

    ro = db.connect(path, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute(
            "INSERT INTO entries (created_at, type, raw_text) "
            "VALUES ('now', 'note', 'should not write')"
        )
        ro.commit()
    ro.close()


# --- tags: cross-cutting labels, queryable across every type ---


def test_entry_is_retrievable_by_tag(conn):
    db.add_entry(
        conn,
        type="link",
        raw_text="Google homepage",
        payload={"url": "https://google.com"},
        tags=["job-search", "google"],
    )

    assert len(db.get_entries(conn, tag="job-search")) == 1
    assert db.get_entries(conn, tag="google")[0]["raw_text"] == "Google homepage"


def test_tag_filter_excludes_unrelated_entries(conn):
    db.add_entry(conn, type="note", raw_text="tagged", tags=["job-search"])
    db.add_entry(conn, type="note", raw_text="untagged")

    rows = db.get_entries(conn, tag="job-search")
    assert len(rows) == 1
    assert rows[0]["raw_text"] == "tagged"


def test_tags_are_normalized(conn):
    db.add_entry(conn, type="note", raw_text="msg", tags=["Google", " Search "])

    assert len(db.get_entries(conn, tag="google")) == 1
    assert len(db.get_entries(conn, tag="search")) == 1


def test_tag_vocabulary_is_deduplicated(conn):
    db.add_entry(conn, type="note", raw_text="one", tags=["google"])
    db.add_entry(conn, type="link", raw_text="two", tags=["google"])

    (count,) = conn.execute(
        "SELECT COUNT(*) FROM tags WHERE name = 'google'"
    ).fetchone()
    assert count == 1


def test_get_tags_orders_by_usage_then_name(conn):
    db.add_entry(conn, type="note", raw_text="one", tags=["google", "rare"])
    db.add_entry(conn, type="note", raw_text="two", tags=["google", "apple"])
    db.add_entry(conn, type="note", raw_text="three", tags=["google", "apple"])

    # google (3) > apple (2) > rare (1); ties would break alphabetically.
    assert db.get_tags(conn) == ["google", "apple", "rare"]


def test_get_tags_empty_when_no_tags(conn):
    assert db.get_tags(conn) == []


def test_entry_carries_its_tags_sorted(conn):
    db.add_entry(conn, type="note", raw_text="msg", tags=["job-search", "google"])

    assert db.get_entries(conn)[0]["tags"] == ["google", "job-search"]


def test_entry_without_tags_has_empty_tag_list(conn):
    db.add_entry(conn, type="note", raw_text="plain")

    assert db.get_entries(conn)[0]["tags"] == []


def test_one_tag_spans_multiple_types(conn):
    db.add_entry(conn, type="note", raw_text="applied to Google", tags=["job-search"])
    db.add_entry(conn, type="link", raw_text="careers page", tags=["job-search"])
    db.add_entry(
        conn, type="expense", raw_text="interview shirt", amount=1200.0,
        tags=["job-search"],
    )

    rows = db.get_entries(conn, tag="job-search")
    assert len(rows) == 3
    assert {r["type"] for r in rows} == {"note", "link", "expense"}


# --- types: a controlled vocabulary that `entries.type` is a foreign key onto ---


def test_default_types_are_seeded(conn):
    assert set(db.get_types(conn)) >= {"note", "expense", "link"}


def test_add_entry_registers_a_new_type(conn):
    db.add_entry(conn, type="recipe", raw_text="pasta with garlic")

    assert "recipe" in db.get_types(conn)
    assert db.get_entries(conn, type="recipe")[0]["raw_text"] == "pasta with garlic"


def test_unregistered_type_is_rejected_by_foreign_key(conn):
    # A raw INSERT (what the SQL agent does) with a type not in `types` must
    # fail, so the agent has to consciously register a new type first.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO entries (created_at, type, raw_text) "
            "VALUES ('2026-07-01T00:00:00Z', 'nonexistent', 'oops')"
        )


# --- migration: an old-shaped DB (extra column, free-form type, no FK) upgrades
#     in place, preserving every row, its ids, and its tags ---

_OLD_SCHEMA = """
CREATE TABLE entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    type        TEXT NOT NULL,
    raw_text    TEXT NOT NULL,
    occurred_at TEXT,
    amount      REAL,
    currency    TEXT,
    category    TEXT,
    due_at      TEXT,
    extra       TEXT
);
CREATE TABLE tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE entry_tags (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
    PRIMARY KEY (entry_id, tag_id)
);
"""


def _make_old_db(path):
    """Create a legacy-schema database with a few rows and a tag."""
    c = sqlite3.connect(path)
    c.executescript(_OLD_SCHEMA)
    c.execute(
        "INSERT INTO entries (id, created_at, type, raw_text, occurred_at, extra) "
        "VALUES (1, '2026-06-01T10:00:00Z', 'note', 'a plain note', '2026-06-01', NULL)"
    )
    c.execute(
        "INSERT INTO entries (id, created_at, type, raw_text, occurred_at, "
        "amount, currency, category, extra) VALUES "
        "(2, '2026-06-02T10:00:00Z', 'expense', 'netflix', '2026-06-02', "
        "649.0, 'INR', 'subscription', NULL)"
    )
    # A type outside the default seed set, and an entry with an `extra` payload.
    c.execute(
        "INSERT INTO entries (id, created_at, type, raw_text, occurred_at, extra) "
        "VALUES (3, '2026-06-03T10:00:00Z', 'idea', 'a startup idea', '2026-06-03', "
        "'{\"score\": 7}')"
    )
    c.execute("INSERT INTO tags (id, name) VALUES (1, 'work')")
    c.execute("INSERT INTO entry_tags (entry_id, tag_id) VALUES (3, 1)")
    c.commit()
    c.close()


def test_migration_preserves_rows_and_ids(tmp_path):
    path = str(tmp_path / "old.db")
    _make_old_db(path)

    conn = db.connect(path)
    db.init_db(conn)

    rows = db.get_entries(conn)
    assert len(rows) == 3
    assert {r["id"]: r["raw_text"] for r in rows} == {
        1: "a plain note",
        2: "netflix",
        3: "a startup idea",
    }
    conn.close()


def test_migration_maps_extra_onto_payload(tmp_path):
    path = str(tmp_path / "old.db")
    _make_old_db(path)

    conn = db.connect(path)
    db.init_db(conn)

    idea = db.get_entries(conn, type="idea")[0]
    assert idea["payload"] == {"score": 7}
    assert "extra" not in _table_columns(conn, "entries")
    assert "payload" in _table_columns(conn, "entries")
    conn.close()


def test_migration_backfills_existing_types_into_vocabulary(tmp_path):
    path = str(tmp_path / "old.db")
    _make_old_db(path)

    conn = db.connect(path)
    db.init_db(conn)

    # 'idea' was only ever a free-form string; it must now be a registered type
    # so the new foreign key holds for that row.
    assert "idea" in db.get_types(conn)
    conn.close()


def test_migration_preserves_tags(tmp_path):
    path = str(tmp_path / "old.db")
    _make_old_db(path)

    conn = db.connect(path)
    db.init_db(conn)

    tagged = db.get_entries(conn, tag="work")
    assert len(tagged) == 1
    assert tagged[0]["id"] == 3
    assert tagged[0]["tags"] == ["work"]
    conn.close()


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "old.db")
    _make_old_db(path)

    first = db.connect(path)
    db.init_db(first)
    first.close()

    # A second init_db must be a harmless no-op (data intact, still 3 rows).
    second = db.connect(path)
    db.init_db(second)
    assert len(db.get_entries(second)) == 3
    assert "idea" in db.get_types(second)
    second.close()


def _table_columns(conn, table):
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


# --- created_at captures the real recording time, not a guessed date ---


def _assert_is_now(created_at, before, after):
    ts = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert before - timedelta(seconds=2) <= ts <= after + timedelta(seconds=2)


def test_insert_omitting_created_at_is_auto_timestamped(conn):
    # This is what the storing agent does: omit created_at and let the
    # database stamp the real time (not midnight).
    before = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO entries (type, raw_text, occurred_at) "
        "VALUES ('note', 'time check', '2026-06-21')"
    )
    conn.commit()
    after = datetime.now(timezone.utc)

    _assert_is_now(db.get_entries(conn)[0]["created_at"], before, after)


def test_add_entry_stamps_real_created_at_time(conn):
    before = datetime.now(timezone.utc)
    db.add_entry(conn, type="note", raw_text="hi")
    after = datetime.now(timezone.utc)

    _assert_is_now(db.get_entries(conn)[0]["created_at"], before, after)
