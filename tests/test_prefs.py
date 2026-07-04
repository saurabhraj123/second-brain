"""Tests for the standing-instructions file (prefs.py / memory.md).

This is the user's "how you should behave" memory — read on every run and
injected into the agents. Distinct from the DB, which holds facts and events.
"""

import prefs


def test_load_returns_empty_when_file_missing(tmp_path):
    assert prefs.load_preferences(str(tmp_path / "nope.md")) == ""


def test_append_then_load_roundtrips(tmp_path):
    path = str(tmp_path / "memory.md")

    prefs.append_preference("always show amounts in INR", path)

    assert "always show amounts in INR" in prefs.load_preferences(path)


def test_append_accumulates_multiple_preferences(tmp_path):
    path = str(tmp_path / "memory.md")

    prefs.append_preference("rule one", path)
    prefs.append_preference("rule two", path)

    text = prefs.load_preferences(path)
    assert "rule one" in text and "rule two" in text


def test_append_ignores_blank(tmp_path):
    path = str(tmp_path / "memory.md")

    prefs.append_preference("   ", path)

    assert prefs.load_preferences(path) == ""
