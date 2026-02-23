#!/bin/bash
# RTFI hook runner — uses the dedicated venv if available, falls back to system python3
VENV_PYTHON="$HOME/.rtfi/venv/bin/python3"
if [ -x "$VENV_PYTHON" ]; then
    exec "$VENV_PYTHON" "$CLAUDE_PLUGIN_ROOT/scripts/hook_handler.py" "$@"
else
    exec python3 "$CLAUDE_PLUGIN_ROOT/scripts/hook_handler.py" "$@"
fi
