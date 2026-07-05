# Task Manager — Design

**Date:** 2026-07-05
**Status:** Approved design, not yet implemented
**Project:** Second Brain (ai-memory)

## Purpose

Give the Second Brain a real notion of **tasks / todos** — something it currently lacks. Today a "remind me to…" lands in the `entries` table as a plain `note`: there is no completion state, `due_at` is dormant, and the write path is INSERT-only so nothing could be marked done anyway. This adds a task subsystem that supports an organization → project → task → subtask hierarchy, statuses, due dates, and attachments.

## Core decision: tasks are not memories

The existing `entries` model assumes **immutable, append-only facts**, queried by search — which is why the write path is INSERT-only and works well. Tasks break every part of that: they are **mutable** (open → in-progress → done/cancelled), **relational** (subtasks, project membership), and **stateful/time-sensitive** (due, overdue, recurring).

Therefore tasks get **their own tables**, living beside `entries` in the same `brain.db` — not shoehorned into `entries`. (This is the opposite of the earlier decision to keep notes/expenses/links in one flexible table: those were all the same immutable shape; tasks are a genuinely different shape.)

## Architecture

- **Writes go only through typed, parameterized tools** — `create_task`, `update_task`, `complete_task`, `add_attachment`, plus resolve-or-create for orgs/projects. The LLM never writes free-form `UPDATE`. This confines mutation to safe, tested operations and keeps the memory path's append-only safety fully intact.
- **Reads reuse the existing read-only SQL path** — the task tables are added to the schema doc so `recall_agent` can `SELECT` from them for questions like "what's open in the Toddle project?". Read-only access to the whole DB is already safe (`mode=ro`).
- **A `task_agent`** on the router handles task intents, mirroring `sql_agent` / `recall_agent`. Task writes are its tools; task reads flow through the existing recall path.

This extends the existing safety model cleanly: **the LLM may SELECT anything; only typed tools may mutate.**

## Schema

```sql
organizations (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  name  TEXT NOT NULL UNIQUE            -- seeded: "Personal"
);

projects (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  name    TEXT NOT NULL,
  org_id  INTEGER NOT NULL REFERENCES organizations(id)
                                        -- seeded: "Inbox" under "Personal"
);

task_statuses (
  name  TEXT PRIMARY KEY               -- controlled vocab (mirrors `types`)
);                                      -- seeded: open, in-progress, done, cancelled

tasks (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  title         TEXT NOT NULL,          -- short name: "buy sticks"
  description   TEXT,                   -- optional longer detail
  status        TEXT NOT NULL DEFAULT 'open' REFERENCES task_statuses(name),
  due_at        TEXT,                   -- local calendar day/time (see date handling)
  priority      TEXT,                   -- optional
  project_id    INTEGER NOT NULL REFERENCES projects(id),   -- defaults to Inbox
  parent_id     INTEGER REFERENCES tasks(id),               -- NULL = top-level; set = subtask
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),  -- UTC instant
  completed_at  TEXT,                   -- set when status -> done
  payload       TEXT                    -- JSON for type-specific extras
);

attachments (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  type         TEXT NOT NULL DEFAULT 'link' CHECK (type IN ('image','link','file')),
  url          TEXT NOT NULL,
  description  TEXT,                    -- also serves as a caption
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
```

Notes:

- **Statuses** are a controlled vocabulary (same pattern as the `types` table): consistent values so `GROUP BY status` and status filters stay clean.
- **Attachments are task-only** (clean `task_id` FK). Standalone bookmarks remain the memory `link` type — no duplication.
- **Dates** follow the existing convention: `created_at` is a UTC instant (never filter "today" on it); day-based filtering uses the local calendar day, consistent with the `occurred_at` fix.

## Hierarchy & capture flow

Containment is **mandatory** (a task is always under a project, a project always under an org — no orphans) but **frictionless** via seeded defaults (org "Personal", project "Inbox"). At capture the `task_agent`:

- **Infers the project silently** when it's obvious from wording/context.
- **Defaults to Inbox silently** for simple/personal/one-off items.
- **Asks a follow-up only on high-value ambiguity** — biased hard toward _not_ asking. An assistant that interrogates every quick capture is worse than one that drops it in Inbox to reorganize later.
- **Reminder exception:** a _"remind me to…"_ phrased task with **no date** always asks _"when should I remind you?"_ — a dateless reminder can't function. (A plain to-do with no date is fine; store it dateless.)

Orgs and projects use the same **reuse-or-create** discipline as `types`/tags: reuse an existing one by name, otherwise create it and tell the user.

## Phasing

- **Phase 1 — Core.** `organizations`, `projects`, `tasks`, `task_statuses`; default seeds; capture flow; write tools (`create_task`, `update_task`, `complete_task`); `task_agent` + router wiring; read support via the schema doc. Includes `due_at` and `priority` (cheap columns). → a usable task manager.
- **Phase 2 — Attachments (+ subtasks).** `attachments` table + `add_attachment`; "tasks with images" queries. Subtasks (`parent_id` handling + progress display, capped at one level) likely fold in here.
- **Phase 3 — Recurrence.** Simple frequencies (daily / weekly / monthly / every-N-days) stored on the task; **generate-on-completion**: completing a recurring task computes the next `due_at` and inserts the next instance. The date-math-heavy, edge-case-prone piece — isolated last, kept minimal (no series-editing in v1).

## Testing

Follow the existing TDD approach (`tests/`, pytest, in-memory SQLite fixtures):

- Storage layer: table creation, seeds, FK enforcement (task→project→org, status FK), `ON DELETE CASCADE` for attachments, idempotent init/migration.
- Typed tools: create/update/complete round-trips, resolve-or-create for orgs/projects, default-to-Inbox behavior, `completed_at` set on done.
- Capture-flow rules (reminder-needs-date) are prompt-level and verified via agent runs rather than unit tests.

## Out of scope (for now)

- Uploading actual file **bytes** (attachments store URLs/links only; binary storage would be its own project).
- Cross-cutting attachments on memories (revisit only if needed).
- Calendar-grade recurrence (RRULE), series editing, sub-sub-tasks.
- Reorganizing existing **memories** under the org/project hierarchy.
