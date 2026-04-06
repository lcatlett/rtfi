#!/bin/bash
# RTFI hook runner — no dependencies, stdlib only
exec python3 "$CLAUDE_PLUGIN_ROOT/scripts/hook_handler.py" "$@"
