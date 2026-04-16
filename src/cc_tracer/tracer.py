#!/usr/bin/env python3
"""cc-tracer hook handler.

Reads Claude Code hook event JSON from stdin, appends a structured
trace record to ~/.cc-tracer/traces/{session_id}.jsonl.

Stdlib-only — no third-party imports — so startup is fast (~30ms).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

TRACE_DIR = Path.home() / ".cc-tracer" / "traces"
MAX_PROMPT = 500
MAX_INPUT_PREVIEW = 200
MAX_COMMAND = 300
MAX_LLM_RESPONSE = 1000
TRANSCRIPT_TAIL_LINES = 50


def _truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _input_preview(tool_input: dict | None) -> str | None:
    """Create a short preview of tool_input."""
    if not tool_input:
        return None
    return _truncate(json.dumps(tool_input, ensure_ascii=False), MAX_INPUT_PREVIEW)


def _extract_command(tool_name: str, tool_input: dict | None) -> str | None:
    """Extract shell command from Bash tool input."""
    if tool_name == "Bash" and tool_input:
        return _truncate(tool_input.get("command"), MAX_COMMAND)
    return None


def _read_last_assistant_text(transcript_path: str | None) -> str | None:
    """Read the transcript JSONL and extract text from the last assistant message.

    The transcript is a JSONL file where assistant messages have:
        {"type": "assistant", "message": {"role": "assistant", "content": [...]}}
    Content blocks can be {"type": "text", "text": "..."} or {"type": "tool_use", ...}.

    We read the tail of the file and find the last assistant entry with text content.
    """
    if not transcript_path:
        return None
    path = Path(transcript_path)
    if not path.exists():
        return None

    try:
        # Read last N lines efficiently (avoid reading entire large transcript)
        with open(path, "rb") as f:
            # Seek from end to find last N newlines
            try:
                f.seek(0, 2)  # end
                size = f.tell()
                # Read up to 256KB from the end — enough for tail lines
                read_size = min(size, 256 * 1024)
                f.seek(size - read_size)
                tail = f.read().decode("utf-8", errors="replace")
            except OSError:
                return None

        lines = tail.strip().split("\n")
        # Walk backwards to find the last assistant message with text
        for line in reversed(lines[-TRANSCRIPT_TAIL_LINES:]):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message", {})
            content = message.get("content", [])
            # Collect all text blocks from this assistant message
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        texts.append(t)
            if texts:
                full_text = "\n".join(texts)
                return _truncate(full_text, MAX_LLM_RESPONSE)
    except (OSError, KeyError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Event handlers — each returns a dict of event-specific fields
# ---------------------------------------------------------------------------

def _handle_session_start(data: dict) -> dict:
    return {"source": data.get("source"), "model": data.get("model")}


def _handle_session_end(data: dict) -> dict:
    return {"reason": data.get("reason")}


def _handle_user_prompt(data: dict) -> dict:
    return {"prompt": _truncate(data.get("prompt"), MAX_PROMPT)}


def _handle_stop(data: dict) -> dict:
    result = {"stop_hook_active": data.get("stop_hook_active", False)}
    llm_text = _read_last_assistant_text(data.get("transcript_path"))
    if llm_text:
        result["llm_response"] = llm_text
    return result


def _handle_pre_tool_use(data: dict) -> dict:
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input")
    return {
        "tool_name": tool_name,
        "tool_use_id": data.get("tool_use_id"),
        "tool_input_preview": _input_preview(tool_input),
        "command": _extract_command(tool_name, tool_input),
    }


def _handle_post_tool_use(data: dict) -> dict:
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input")
    response = data.get("tool_response")
    return {
        "tool_name": tool_name,
        "tool_use_id": data.get("tool_use_id"),
        "tool_input_preview": _input_preview(tool_input),
        "response_size": len(json.dumps(response, ensure_ascii=False)) if response else 0,
        "command": _extract_command(tool_name, tool_input),
    }


HANDLERS = {
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
    "UserPromptSubmit": _handle_user_prompt,
    "Stop": _handle_stop,
    "PreToolUse": _handle_pre_tool_use,
    "PostToolUse": _handle_post_tool_use,
}


def trace(data: dict, trace_dir: Path | None = None) -> Path | None:
    """Process one hook event and append a trace record.

    Returns the path to the trace file, or None if event was ignored.
    """
    event = data.get("hook_event_name")
    session_id = data.get("session_id")
    if not event or not session_id:
        return None

    handler = HANDLERS.get(event)
    if handler is None:
        return None  # event not tracked yet

    # Build record
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "session_id": session_id,
        "cwd": data.get("cwd"),
    }
    # Add subagent context if present
    if data.get("agent_id"):
        record["agent_id"] = data["agent_id"]
        record["agent_type"] = data.get("agent_type")

    record.update(handler(data))

    # Write
    tdir = trace_dir or TRACE_DIR
    tdir.mkdir(parents=True, exist_ok=True)
    trace_file = tdir / f"{session_id}.jsonl"
    with open(trace_file, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Update latest symlink on SessionStart
    if event == "SessionStart":
        latest = tdir / "latest"
        try:
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(trace_file.name)
        except OSError:
            pass  # non-critical

    return trace_file


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # bad input → do nothing, don't block Claude

    trace(data)
    sys.exit(0)


if __name__ == "__main__":
    main()
