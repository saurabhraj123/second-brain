"""Tests for the task subsystem storage layer (tasks.py).

Tasks live in their own tables beside the append-only memory `entries`, because
they are mutable and relational. These tests pin down the organization ->
project -> task hierarchy, the seeded defaults (Personal / Inbox), the status
vocabulary, and the create/update/complete round-trips.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import db
import tasks


@pytest.fixture
def conn():
    """A fresh in-memory database with the task schema + seeds applied."""
    c = db.connect(":memory:")
    tasks.init_tasks(c)
    yield c
    c.close()


# --- seeds: the vocabulary and the default org/project ---


def test_init_seeds_the_status_vocabulary(conn):
    assert set(tasks.get_statuses(conn)) == {"open", "in-progress", "done", "cancelled"}


def test_init_seeds_personal_org_and_inbox_project(conn):
    orgs = [r["name"] for r in conn.execute("SELECT name FROM organizations")]
    assert orgs == ["Personal"]

    row = conn.execute(
        "SELECT p.name AS project, o.name AS org FROM projects p "
        "JOIN organizations o ON o.id = p.org_id"
    ).fetchone()
    assert row["project"] == "Inbox"
    assert row["org"] == "Personal"


def test_init_is_idempotent(conn):
    tasks.init_tasks(conn)  # second call
    (orgs,) = conn.execute("SELECT COUNT(*) FROM organizations").fetchone()
    (projects,) = conn.execute("SELECT COUNT(*) FROM projects").fetchone()
    assert orgs == 1 and projects == 1


# --- create_task: hierarchy defaults and explicit placement ---


def test_create_task_defaults_to_inbox_under_personal(conn):
    task_id = tasks.create_task(conn, title="buy sticks")

    t = tasks.get_task(conn, task_id)
    assert t["title"] == "buy sticks"
    assert t["status"] == "open"
    assert t["project"] == "Inbox"
    assert t["org"] == "Personal"


def test_create_task_under_named_project_creates_it(conn):
    task_id = tasks.create_task(conn, title="fix login", project="web-app")

    t = tasks.get_task(conn, task_id)
    assert t["project"] == "web-app"
    # a brand-new project lands under the default org
    assert t["org"] == "Personal"


def test_create_task_reuses_an_existing_project(conn):
    tasks.create_task(conn, title="one", project="web-app")
    tasks.create_task(conn, title="two", project="web-app")

    (count,) = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE name = 'web-app'"
    ).fetchone()
    assert count == 1


def test_create_task_under_named_org(conn):
    task_id = tasks.create_task(conn, title="ship it", project="graphqlapi", org="Toddle")

    t = tasks.get_task(conn, task_id)
    assert t["project"] == "graphqlapi"
    assert t["org"] == "Toddle"


# --- integrity: the FKs are real ---


def test_status_is_constrained_by_foreign_key(conn):
    inbox_id = conn.execute("SELECT id FROM projects WHERE name = 'Inbox'").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (title, status, project_id) VALUES ('x', 'bogus', ?)",
            (inbox_id,),
        )


def test_task_requires_a_project(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tasks (title, status) VALUES ('orphan', 'open')")


# --- lifecycle: complete / update ---


def test_complete_task_sets_status_and_completed_at(conn):
    task_id = tasks.create_task(conn, title="water plants")

    tasks.complete_task(conn, task_id)

    t = tasks.get_task(conn, task_id)
    assert t["status"] == "done"
    assert t["completed_at"] is not None


def test_update_task_changes_fields(conn):
    task_id = tasks.create_task(conn, title="call dentist")

    tasks.update_task(conn, task_id, due_at="2026-07-10", priority="high")

    t = tasks.get_task(conn, task_id)
    assert t["due_at"] == "2026-07-10"
    assert t["priority"] == "high"


def test_update_task_can_move_it_to_another_project(conn):
    task_id = tasks.create_task(conn, title="reorg me")

    tasks.update_task(conn, task_id, project="web-app")

    assert tasks.get_task(conn, task_id)["project"] == "web-app"


# --- reads ---


def test_get_tasks_filters_by_status(conn):
    tasks.create_task(conn, title="open one")
    done_id = tasks.create_task(conn, title="done one")
    tasks.complete_task(conn, done_id)

    open_tasks = tasks.get_tasks(conn, status="open")
    assert [t["title"] for t in open_tasks] == ["open one"]


def test_get_tasks_filters_by_project(conn):
    tasks.create_task(conn, title="inbox task")
    tasks.create_task(conn, title="work task", project="web-app")

    work = tasks.get_tasks(conn, project="web-app")
    assert [t["title"] for t in work] == ["work task"]


# --- created_at is a real UTC recording timestamp (like entries) ---


def test_created_at_is_stamped_utc(conn):
    before = datetime.now(timezone.utc)
    task_id = tasks.create_task(conn, title="stamp me")
    after = datetime.now(timezone.utc)

    created = tasks.get_task(conn, task_id)["created_at"]
    ts = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before - timedelta(seconds=2) <= ts <= after + timedelta(seconds=2)
