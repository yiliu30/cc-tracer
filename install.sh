#!/usr/bin/env bash
# cc-tracer installer
# Creates venv, installs package, registers hooks in ~/.claude/settings.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="/home/yiliu7/workspace/venvs/cct"
SETTINGS="$HOME/.claude/settings.json"
TRACER_CMD="$VENV_DIR/bin/python $SCRIPT_DIR/src/cc_tracer/tracer.py"

echo "=== cc-tracer installer ==="

# 1. Create venv with uv
echo "[1/3] Creating venv at $VENV_DIR ..."
uv venv "$VENV_DIR" --python 3.13 2>/dev/null || uv venv "$VENV_DIR"
VIRTUAL_ENV="$VENV_DIR" uv pip install -e "$SCRIPT_DIR"
echo "  Installed cc-tracer into $VENV_DIR"
echo "  CLI: $VENV_DIR/bin/cc-trace"

# 2. Create trace directory
echo "[2/3] Creating trace directory ..."
mkdir -p "$HOME/.cc-tracer/traces"

# 3. Register hooks in settings.json
echo "[3/3] Registering hooks in $SETTINGS ..."

# Build the hooks JSON
HOOKS_JSON=$(cat <<ENDJSON
{
  "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": "$TRACER_CMD"}]}],
  "SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": "$TRACER_CMD"}]}],
  "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "$TRACER_CMD"}]}],
  "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "$TRACER_CMD"}]}],
  "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "$TRACER_CMD"}]}],
  "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "$TRACER_CMD"}]}]
}
ENDJSON
)

if [ ! -f "$SETTINGS" ]; then
    echo '{}' > "$SETTINGS"
fi

# Merge hooks into existing settings (preserving everything else)
TEMP=$(mktemp)
jq --argjson hooks "$HOOKS_JSON" '.hooks = ($hooks + (.hooks // {}))' "$SETTINGS" > "$TEMP" && mv "$TEMP" "$SETTINGS"

echo ""
echo "=== Done ==="
echo "  Traces will be written to: ~/.cc-tracer/traces/"
echo "  View traces: $VENV_DIR/bin/cc-trace list"
echo "  Or add to PATH: export PATH=\"$VENV_DIR/bin:\$PATH\""
echo ""
echo "  Start a new Claude Code session to begin tracing!"
