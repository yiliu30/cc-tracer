# cc-tracer

Claude Code session tracer — hooks-based observability for understanding how Claude Code works.

## What it does

Captures key lifecycle events from Claude Code sessions and writes structured JSONL trace files. Lets you see:

- **User prompts** — what you sent
- **Tool calls** — which tools were used, with args and timing
- **Shell commands** — exact Bash commands Claude ran
- **LLM responses** — what Claude replied at each turn
- **Session boundaries** — start/end with model info

## How it works

Uses [Claude Code hooks](https://code.claude.com/docs/en/hooks-guide) — shell commands that fire at lifecycle points. Six core events are traced:

| Hook | Captures |
|------|---------|
| `SessionStart` | Model, start reason |
| `UserPromptSubmit` | Prompt text |
| `PreToolUse` | Tool name, args, Bash command |
| `PostToolUse` | Tool response size, duration |
| `Stop` | LLM response text (from transcript) |
| `SessionEnd` | End reason |

Trace files live at `~/.cc-tracer/traces/{session_id}.jsonl`.

## Install

```bash
bash install.sh
```

This:
1. Creates a virtualenv at `~/workspace/venvs/cct/` (using `uv`)
2. Installs the package with the `cc-trace` CLI
3. Registers the 6 hooks in `~/.claude/settings.json`

Then start a new Claude Code session — tracing begins automatically.

## Usage

```bash
# List all traced sessions
cc-trace list

# Show timeline for latest session
cc-trace view

# Show timeline for a specific session
cc-trace view <session-id>

# Tool usage stats
cc-trace stats

# Live-follow the current session
cc-trace tail
```

### Example `cc-trace view` output

```
Session: abc123
12 events

  10:30:01  SessionStart        model=claude-opus-4-6  source=startup
  10:30:02  UserPromptSubmit    What files are in this project?
  10:30:02  PreToolUse          Bash  $ ls -la
  10:30:02  PostToolUse         Bash  342B  45ms
  10:30:03  Stop                All done — I found 12 files in your project...
  10:30:03  SessionEnd          reason=prompt_input_exit
```

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)
- [jq](https://jqlang.github.io/jq/) (for install.sh hook merging)
- Claude Code with hooks support

## Development

```bash
uv run --with pytest pytest tests/ -v
```
