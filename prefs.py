"""Standing instructions — how the assistant should behave.

These live in a plain Markdown file (``memory.md``) that is read fresh on every
run and injected into the agents' instructions, so when the user says "from now
on, do X" it sticks across sessions.

This is deliberately separate from the database: the DB holds *facts and events*
the user wants to remember; this file holds *rules about how to behave*.
"""

import os
from datetime import date

from agents import function_tool

MEMORY_PATH = "memory.md"

_HEADER = """\
# Second Brain — Preferences

Standing instructions for how the assistant should behave. Read on every run.
Add rules below by hand, or just tell the assistant (e.g. "from now on, ...").

## My rules
"""


def load_preferences(path=MEMORY_PATH):
    """Return the preferences text, or '' if the file is missing or empty."""
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def append_preference(note, path=MEMORY_PATH):
    """Append a standing instruction as a dated bullet, creating the file if new."""
    note = note.strip()
    if not note:
        return
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(_HEADER)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {note}  _(added {date.today().isoformat()})_\n")


@function_tool
def save_preference(note: str) -> str:
    """Save a standing instruction about HOW to behave going forward — e.g.
    "always show amounts in INR", "tag gym entries as health", "keep replies
    short". Use this ONLY for rules/preferences, never for facts or events
    (those go to store_memory).
    """
    append_preference(note)
    return f"Saved preference: {note}"
