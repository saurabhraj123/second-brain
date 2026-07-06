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


def test_get_tasks_searches_title_and_description_by_query(conn):
    tasks.create_task(conn, title="Submit the tax documents")
    tasks.create_task(conn, title="Buy groceries", description="milk and tax-free eggs")
    tasks.create_task(conn, title="Call the dentist")

    # matches the title of one and the description of another
    found = {t["title"] for t in tasks.get_tasks(conn, query="tax")}
    assert found == {"Submit the tax documents", "Buy groceries"}


def test_get_tasks_query_combines_with_status(conn):
    open_id = tasks.create_task(conn, title="tax return")
    done_id = tasks.create_task(conn, title="tax refund")
    tasks.complete_task(conn, done_id)

    found = tasks.get_tasks(conn, query="tax", status="open")
    assert [t["id"] for t in found] == [open_id]


def test_get_tasks_filters_by_project(conn):
    tasks.create_task(conn, title="inbox task")
    tasks.create_task(conn, title="work task", project="web-app")

    work = tasks.get_tasks(conn, project="web-app")
    assert [t["title"] for t in work] == ["work task"]


# --- subtasks: a task under another task, in the same project ---


def test_top_level_task_has_no_parent(conn):
    task_id = tasks.create_task(conn, title="standalone")
    assert tasks.get_task(conn, task_id)["parent_id"] is None


def test_subtask_inherits_parent_project(conn):
    parent = tasks.create_task(conn, title="Interview prep", project="job-search")
    sub = tasks.create_task(conn, title="Revise trees", parent_id=parent)

    t = tasks.get_task(conn, sub)
    assert t["parent_id"] == parent
    assert t["project"] == "job-search"  # inherited, not Inbox


def test_subtask_project_follows_parent_even_if_project_given(conn):
    parent = tasks.create_task(conn, title="Trip", project="travel")
    sub = tasks.create_task(conn, title="Book hotel", parent_id=parent, project="ignored")

    assert tasks.get_task(conn, sub)["project"] == "travel"


def test_get_subtasks_returns_children(conn):
    parent = tasks.create_task(conn, title="Parent")
    tasks.create_task(conn, title="Child A", parent_id=parent)
    tasks.create_task(conn, title="Child B", parent_id=parent)

    titles = [s["title"] for s in tasks.get_subtasks(conn, parent)]
    assert sorted(titles) == ["Child A", "Child B"]


def test_subtask_progress_counts_done_over_total(conn):
    parent = tasks.create_task(conn, title="Parent")
    tasks.create_task(conn, title="A", parent_id=parent)
    done = tasks.create_task(conn, title="B", parent_id=parent)
    tasks.complete_task(conn, done)

    assert tasks.subtask_progress(conn, parent) == {"done": 1, "total": 2}


# --- attachments: task-scoped links/images/files ---


def test_add_attachment_round_trips(conn):
    task_id = tasks.create_task(conn, title="design review")

    tasks.add_attachment(
        conn, task_id, url="https://x/mock.png", type="image", description="the mockup"
    )

    atts = tasks.get_attachments(conn, task_id)
    assert len(atts) == 1
    assert atts[0]["url"] == "https://x/mock.png"
    assert atts[0]["type"] == "image"
    assert atts[0]["description"] == "the mockup"


def test_attachment_defaults_to_link_type(conn):
    task_id = tasks.create_task(conn, title="read later")
    tasks.add_attachment(conn, task_id, url="https://x/article")

    assert tasks.get_attachments(conn, task_id)[0]["type"] == "link"


def test_attachment_type_is_constrained(conn):
    task_id = tasks.create_task(conn, title="bad type")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO attachments (task_id, type, url) VALUES (?, 'video', 'u')",
            (task_id,),
        )


def test_deleting_a_task_cascades_its_attachments(conn):
    task_id = tasks.create_task(conn, title="doomed")
    tasks.add_attachment(conn, task_id, url="https://x/a")

    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()

    assert tasks.get_attachments(conn, task_id) == []


def test_get_attachments_empty_when_none(conn):
    task_id = tasks.create_task(conn, title="bare")
    assert tasks.get_attachments(conn, task_id) == []


# --- recurrence: completing a recurring task spawns the next occurrence ---


def test_next_date_daily_and_weekly():
    assert tasks._next_date("2026-07-06", "daily", 1) == "2026-07-07"
    assert tasks._next_date("2026-07-06", "daily", 3) == "2026-07-09"
    assert tasks._next_date("2026-07-06", "weekly", 1) == "2026-07-13"
    assert tasks._next_date("2026-07-06", "weekly", 2) == "2026-07-20"


def test_next_date_monthly_advances_by_interval():
    assert tasks._next_date("2026-01-15", "monthly", 1) == "2026-02-15"
    assert tasks._next_date("2026-01-15", "monthly", 2) == "2026-03-15"


def test_next_date_monthly_clamps_to_month_end():
    # Jan 31 + 1 month has no Feb 31 -> clamp to Feb 28 (2026 is not a leap year)
    assert tasks._next_date("2026-01-31", "monthly", 1) == "2026-02-28"


def test_create_stores_recurrence_rule(conn):
    task_id = tasks.create_task(
        conn, title="water plants", recur_freq="weekly", recur_interval=1, due_at="2026-07-06"
    )
    t = tasks.get_task(conn, task_id)
    assert t["recur_freq"] == "weekly"
    assert t["recur_interval"] == 1


def test_completing_recurring_task_spawns_next_instance(conn):
    task_id = tasks.create_task(
        conn, title="water plants", recur_freq="weekly", due_at="2026-07-06"
    )

    next_id = tasks.complete_task(conn, task_id)

    assert tasks.get_task(conn, task_id)["status"] == "done"
    assert next_id is not None
    nxt = tasks.get_task(conn, next_id)
    assert nxt["status"] == "open"
    assert nxt["title"] == "water plants"
    assert nxt["due_at"] == "2026-07-13"
    assert nxt["recur_freq"] == "weekly"


def test_completing_recurring_carries_project(conn):
    task_id = tasks.create_task(
        conn, title="weekly report", project="Toddle", recur_freq="weekly", due_at="2026-07-06"
    )
    nxt = tasks.get_task(conn, tasks.complete_task(conn, task_id))
    assert nxt["project"] == "Toddle"


def test_completing_non_recurring_task_returns_none(conn):
    task_id = tasks.create_task(conn, title="one-off")
    assert tasks.complete_task(conn, task_id) is None


def test_recurring_without_due_at_just_completes(conn):
    task_id = tasks.create_task(conn, title="vague recurring", recur_freq="weekly")
    assert tasks.complete_task(conn, task_id) is None
    assert tasks.get_task(conn, task_id)["status"] == "done"


# --- created_at is a real UTC recording timestamp (like entries) ---


def test_created_at_is_stamped_utc(conn):
    before = datetime.now(timezone.utc)
    task_id = tasks.create_task(conn, title="stamp me")
    after = datetime.now(timezone.utc)

    created = tasks.get_task(conn, task_id)["created_at"]
    ts = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before - timedelta(seconds=2) <= ts <= after + timedelta(seconds=2)
