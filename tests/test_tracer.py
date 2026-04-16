"""Tests for cc_tracer.tracer."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from cc_tracer.tracer import trace


@pytest.fixture
def trace_dir(tmp_path):
    return tmp_path / "traces"


def _read_records(trace_dir: Path, session_id: str) -> list[dict]:
    path = trace_dir / f"{session_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# -- SessionStart --

def test_session_start(trace_dir):
    data = {
        "session_id": "s1",
        "cwd": "/tmp",
        "hook_event_name": "SessionStart",
        "source": "startup",
        "model": "claude-sonnet-4-6",
    }
    result = trace(data, trace_dir)
    assert result is not None
    records = _read_records(trace_dir, "s1")
    assert len(records) == 1
    r = records[0]
    assert r["event"] == "SessionStart"
    assert r["source"] == "startup"
    assert r["model"] == "claude-sonnet-4-6"
    assert r["session_id"] == "s1"
    assert "ts" in r


def test_session_start_creates_latest_symlink(trace_dir):
    trace({"session_id": "s1", "cwd": "/tmp", "hook_event_name": "SessionStart", "source": "startup", "model": "m"}, trace_dir)
    latest = trace_dir / "latest"
    assert latest.is_symlink()
    assert os.readlink(str(latest)) == "s1.jsonl"


# -- UserPromptSubmit --

def test_user_prompt(trace_dir):
    trace({"session_id": "s1", "cwd": "/tmp", "hook_event_name": "UserPromptSubmit", "prompt": "hello world"}, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert r["event"] == "UserPromptSubmit"
    assert r["prompt"] == "hello world"


def test_prompt_truncation(trace_dir):
    long_prompt = "x" * 1000
    trace({"session_id": "s1", "cwd": "/tmp", "hook_event_name": "UserPromptSubmit", "prompt": long_prompt}, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert len(r["prompt"]) == 503  # 500 + "..."
    assert r["prompt"].endswith("...")


# -- PreToolUse / PostToolUse --

def test_tool_use_cycle(trace_dir):
    pre = {
        "session_id": "s1", "cwd": "/tmp", "hook_event_name": "PreToolUse",
        "tool_name": "Read", "tool_use_id": "tu_1", "tool_input": {"file_path": "/foo.py"},
    }
    post = {
        "session_id": "s1", "cwd": "/tmp", "hook_event_name": "PostToolUse",
        "tool_name": "Read", "tool_use_id": "tu_1",
        "tool_input": {"file_path": "/foo.py"}, "tool_response": {"content": "hello"},
    }
    trace(pre, trace_dir)
    trace(post, trace_dir)
    records = _read_records(trace_dir, "s1")
    assert len(records) == 2
    assert records[0]["tool_use_id"] == records[1]["tool_use_id"] == "tu_1"
    assert records[1]["response_size"] > 0


def test_bash_command_extraction(trace_dir):
    data = {
        "session_id": "s1", "cwd": "/tmp", "hook_event_name": "PreToolUse",
        "tool_name": "Bash", "tool_use_id": "tu_2", "tool_input": {"command": "ls -la"},
    }
    trace(data, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert r["command"] == "ls -la"


def test_non_bash_has_no_command(trace_dir):
    data = {
        "session_id": "s1", "cwd": "/tmp", "hook_event_name": "PreToolUse",
        "tool_name": "Read", "tool_use_id": "tu_3", "tool_input": {"file_path": "/a"},
    }
    trace(data, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert r["command"] is None


# -- Stop --

def test_stop(trace_dir):
    trace({"session_id": "s1", "cwd": "/tmp", "hook_event_name": "Stop", "stop_hook_active": False}, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert r["event"] == "Stop"
    assert r["stop_hook_active"] is False


def test_stop_with_transcript(trace_dir, tmp_path):
    """Stop event reads last assistant text from transcript."""
    # Create a fake transcript JSONL
    transcript = tmp_path / "transcript.jsonl"
    entries = [
        {"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Here is my response to your question."},
            {"type": "tool_use", "name": "Bash", "id": "tu_1", "input": {"command": "ls"}},
        ]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "All done! The task is complete."},
        ]}},
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

    data = {
        "session_id": "s1", "cwd": "/tmp", "hook_event_name": "Stop",
        "stop_hook_active": False, "transcript_path": str(transcript),
    }
    trace(data, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert r["llm_response"] == "All done! The task is complete."


def test_stop_transcript_truncation(trace_dir, tmp_path):
    """LLM response text is truncated to 1000 chars."""
    transcript = tmp_path / "transcript.jsonl"
    long_text = "x" * 2000
    entry = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": long_text},
    ]}}
    transcript.write_text(json.dumps(entry) + "\n")

    data = {
        "session_id": "s1", "cwd": "/tmp", "hook_event_name": "Stop",
        "transcript_path": str(transcript),
    }
    trace(data, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert len(r["llm_response"]) == 1003  # 1000 + "..."
    assert r["llm_response"].endswith("...")


def test_stop_no_transcript(trace_dir):
    """Stop without transcript_path still works."""
    trace({"session_id": "s1", "cwd": "/tmp", "hook_event_name": "Stop"}, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert "llm_response" not in r


# -- SessionEnd --

def test_session_end(trace_dir):
    trace({"session_id": "s1", "cwd": "/tmp", "hook_event_name": "SessionEnd", "reason": "clear"}, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert r["event"] == "SessionEnd"
    assert r["reason"] == "clear"


# -- Edge cases --

def test_missing_fields_no_crash(trace_dir):
    trace({"session_id": "s1", "cwd": "/tmp", "hook_event_name": "SessionStart"}, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert r["source"] is None
    assert r["model"] is None


def test_unknown_event_ignored(trace_dir):
    result = trace({"session_id": "s1", "cwd": "/tmp", "hook_event_name": "SomeNewEvent"}, trace_dir)
    assert result is None
    assert not (trace_dir / "s1.jsonl").exists()


def test_missing_session_id_ignored(trace_dir):
    result = trace({"cwd": "/tmp", "hook_event_name": "Stop"}, trace_dir)
    assert result is None


def test_subagent_context(trace_dir):
    data = {
        "session_id": "s1", "cwd": "/tmp", "hook_event_name": "Stop",
        "agent_id": "ag1", "agent_type": "Explore",
    }
    trace(data, trace_dir)
    r = _read_records(trace_dir, "s1")[0]
    assert r["agent_id"] == "ag1"
    assert r["agent_type"] == "Explore"
