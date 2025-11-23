#!/usr/bin/env python3
"""
Basic tests for Chad's tool execution layer.

Run from /ai with:
    pytest -q

These tests call chad_execute_plan() directly with synthetic plans.
They do NOT hit Bob (OpenAI) or start the Flask server.
"""

from pathlib import Path
import os

import pytest

import app as bob_app  # app.py in the same directory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_ID = "00001"
BASE_DATE = "2025-11-23"
BASE_NAME = f"{BASE_ID}_{BASE_DATE}"


def make_tool_plan(tool_name: str, args: dict | None = None) -> dict:
    """
    Minimal plan structure for a tool task, matching what Bob would produce.
    """
    return {
        "task": {
            "type": "tool",
            "summary": f"Run tool {tool_name}",
            "analysis_file": "",
            "edits": [],
            "tool": {
                "name": tool_name,
                "args": args or {},
            },
        }
    }


# ---------------------------------------------------------------------------
# get_current_datetime
# ---------------------------------------------------------------------------

def test_get_current_datetime_tool(tmp_path, monkeypatch):
    """
    Ensure get_current_datetime returns a human-readable string and writes an exec report.
    """
    # Use a temporary scratch directory to avoid polluting real data
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    plan = make_tool_plan("get_current_datetime", {})
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert report["tool_name"] == "get_current_datetime"
    assert "Local system date and time:" in (report["tool_result"] or "")

    # Exec JSON written
    exec_path = bob_app.QUEUE_DIR / f"{BASE_NAME}.exec.json"
    assert exec_path.exists()


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

def test_list_files_non_recursive(tmp_path, monkeypatch):
    """
    list_files should list entries under the (mocked) PROJECT_ROOT.
    """
    # Fake project root with some files/dirs
    root = tmp_path / "project"
    root.mkdir()
    (root / "file1.txt").write_text("hello", encoding="utf-8")
    (root / "dir1").mkdir()
    (root / "dir1" / "file2.py").write_text("print('hi')", encoding="utf-8")

    # Point PROJECT_ROOT to our temp project
    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)

    # Scratch dir isolated
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    plan = make_tool_plan("list_files", {"path": ".", "recursive": False, "max_entries": 50})
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    result = report["tool_result"] or ""
    # Should show our entries relative to PROJECT_ROOT
    assert "file1.txt" in result
    assert "dir1" in result
    # Non-recursive: should not show dir1/file2.py
    assert "dir1/file2.py" not in result


def test_list_files_recursive(tmp_path, monkeypatch):
    """
    list_files with recursive=True should include nested files.
    """
    root = tmp_path / "project"
    root.mkdir()
    (root / "dir1").mkdir()
    (root / "dir1" / "file2.py").write_text("print('hi')", encoding="utf-8")

    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    plan = make_tool_plan("list_files", {"path": ".", "recursive": True, "max_entries": 50})
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    result = report["tool_result"] or ""
    assert "dir1/file2.py" in result


def test_list_files_outside_jail(tmp_path, monkeypatch):
    """
    list_files should refuse to go outside PROJECT_ROOT.
    """
    root = tmp_path / "project"
    root.mkdir()

    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    plan = make_tool_plan("list_files", {"path": "../", "recursive": True})
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert "invalid" in report["message"] or "outside the project jail" in report["message"]
    assert not report["tool_result"]


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def test_read_file_happy_path(tmp_path, monkeypatch):
    """
    read_file should return UTF-8 contents of a file under PROJECT_ROOT.
    """
    root = tmp_path / "project"
    root.mkdir()
    target = root / "hello.txt"
    content = "Hello from test_read_file_happy_path"
    target.write_text(content, encoding="utf-8")

    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    plan = make_tool_plan("read_file", {"path": "hello.txt", "max_chars": 1000})
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert content in (report["tool_result"] or "")
    assert "read_file 'hello.txt'" in report["message"]


def test_read_file_nonexistent(tmp_path, monkeypatch):
    """
    read_file should handle non-existent paths gracefully.
    """
    root = tmp_path / "project"
    root.mkdir()

    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    plan = make_tool_plan("read_file", {"path": "does_not_exist.txt"})
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert not report["tool_result"]
    assert "does_not_exist" in report["message"]


# ---------------------------------------------------------------------------
# Markdown notes
# ---------------------------------------------------------------------------

def test_create_markdown_note_creates_file(tmp_path, monkeypatch):
    """
    create_markdown_note should create a .md file in MARKDOWN_NOTES_DIR.
    """
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()

    monkeypatch.setattr(bob_app, "MARKDOWN_NOTES_DIR", notes_dir, raising=False)
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    title = "Test Note"
    body = "# Title\n\nBody text"
    plan = make_tool_plan("create_markdown_note", {"title": title, "content": body})
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert "Created markdown note" in (report["tool_result"] or "")
    # Slug should be "test-note"
    note_path = notes_dir / "test-note.md"
    assert note_path.exists()
    assert note_path.read_text(encoding="utf-8") == body


def test_append_to_markdown_note_appends(tmp_path, monkeypatch):
    """
    append_to_markdown_note should append content to an existing or new note.
    """
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()

    monkeypatch.setattr(bob_app, "MARKDOWN_NOTES_DIR", notes_dir, raising=False)
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    title = "Append Note"
    slug = "append-note"
    note_path = notes_dir / f"{slug}.md"
    note_path.write_text("Line 1\n", encoding="utf-8")

    # Append
    plan = make_tool_plan(
        "append_to_markdown_note",
        {"title": title, "content": "Line 2"},
    )
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert "Appended to markdown note" in (report["tool_result"] or "")
    contents = note_path.read_text(encoding="utf-8")
    assert "Line 1" in contents
    assert "Line 2" in contents


def test_append_to_markdown_note_creates_when_missing(tmp_path, monkeypatch):
    """
    If note does not exist, append_to_markdown_note should create it.
    """
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()

    monkeypatch.setattr(bob_app, "MARKDOWN_NOTES_DIR", notes_dir, raising=False)
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    title = "Fresh Note"
    slug = "fresh-note"

    plan = make_tool_plan(
        "append_to_markdown_note",
        {"title": title, "content": "First line"},
    )
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert "created" in (report["tool_result"] or "").lower()
    note_path = notes_dir / f"{slug}.md"
    assert note_path.exists()
    assert "First line" in note_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------

class _DummySMTP:
    """Fake SMTP client used for testing send_email without network."""

    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = False
        self.sent_messages = []

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = True
        self.user = user
        self.password = password

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_send_email_success(monkeypatch, tmp_path):
    """
    send_email should build a message and call SMTP when SMTP_* envs are set.
    It should always send to SMTP_TO / SMTP_TEST_TO, ignoring tool args.
    """
    # Patch SMTP to our dummy
    monkeypatch.setattr(bob_app.smtplib, "SMTP", _DummySMTP, raising=False)

    # Set required env vars
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "user@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "password123")
    monkeypatch.setenv("SMTP_FROM", "from@example.com")
    monkeypatch.setenv("SMTP_TO", "forced@example.com")

    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    # Minimal project root for attachment resolution (no attachments in this test)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)

    plan = make_tool_plan(
        "send_email",
        {
            "to": "ignored@example.org",  # should be ignored now
            "subject": "Test Subject",
            "body": "Hello from tests",
            "attachments": [],
        },
    )
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert "Email sent to forced@example.com" in (report["tool_result"] or "")
    assert "sent an email to 'forced@example.com'" in (report["message"] or "")


def test_send_email_missing_smtp(monkeypatch, tmp_path):
    """
    send_email should report missing SMTP settings instead of crashing.
    """
    # Ensure SMTP env is missing
    for key in [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)

    plan = make_tool_plan(
        "send_email",
        {
            "to": "dest@example.com",
            "subject": "Test Subject",
            "body": "Hello",
        },
    )
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    assert "SMTP settings are incomplete" in (report["message"] or "")
    assert not report["tool_result"]


def test_read_file_truncates_long_file(tmp_path, monkeypatch):
    """
    read_file should truncate long files and append '... (truncated)'.
    """
    root = tmp_path / "project"
    root.mkdir()
    target = root / "big.txt"

    # Make a file longer than 200 characters (stand-in for 16000 in real use)
    long_text = "X" * 300
    target.write_text(long_text, encoding="utf-8")

    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    # Use a small max_chars so test is fast & clear
    plan = make_tool_plan("read_file", {"path": "big.txt", "max_chars": 200})
    report = bob_app.chad_execute_plan(BASE_ID, BASE_DATE, BASE_NAME, plan)

    result = report["tool_result"] or ""
    assert result.endswith("... (truncated)")
    assert len(result) <= 220  # 200 chars + newline + suffix wiggle room


def test_send_email_forced_to_env(monkeypatch, tmp_path):
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg):
            sent["to"] = msg["To"]

    # IMPORTANT: patch THE smtplib INSIDE OUR APP, not global
    monkeypatch.setattr(bob_app.smtplib, "SMTP", FakeSMTP, raising=False)

    # Minimal required env
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "user@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "password123")
    monkeypatch.setenv("SMTP_FROM", "from@example.com")
    monkeypatch.setenv("SMTP_TO", "forced@example.com")

    # Required dirs
    monkeypatch.setattr(bob_app, "SCRATCH_DIR", tmp_path / "scratch", raising=False)
    bob_app.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(bob_app, "PROJECT_ROOT", root, raising=False)

    # Dummy plan with bogus "to", which must be ignored
    plan = make_tool_plan(
        "send_email",
        {
            "to": "ignored@example.org",
            "subject": "Test Subject",
            "body": "Hello from tests",
        },
    )

    report = bob_app.chad_execute_plan("00001", "2025-11-23", "00001_2025-11-23", plan)

    # Check that it forced the TO
    assert sent["to"] == "forced@example.com"

