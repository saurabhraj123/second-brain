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


def test_extra_json_round_trips(conn):
    db.add_entry(
        conn,
        type="note",
        raw_text="ate a burger",
        extra={"place": "the diner", "mood": "happy"},
    )

    row = db.get_entries(conn)[0]
    assert row["extra"] == {"place": "the diner", "mood": "happy"}


def test_occurred_at_defaults_to_today(conn):
    db.add_entry(conn, type="note", raw_text="something happened")

    row = db.get_entries(conn)[0]
    assert row["occurred_at"] == date.today().isoformat()


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
        extra={"url": "https://google.com"},
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
