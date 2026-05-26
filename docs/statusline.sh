#!/usr/bin/env bash
set -u

input="$(/bin/cat)"
venv_python="$HOME/.rtfi/venv/bin/python3"
script_path=""

if [ ! -x "$venv_python" ]; then
  printf 'RTFI --'
  exit 0
fi

if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "${CLAUDE_PLUGIN_ROOT}/scripts/rtfi_statusline.py" ]; then
  script_path="${CLAUDE_PLUGIN_ROOT}/scripts/rtfi_statusline.py"
else
  script_path="$("$venv_python" - 2>/dev/null <<'INNERPY'
from pathlib import Path

def version_key(path: Path):
    parts = []
    for part in path.name.replace('-', '.').split('.'):
        parts.append(int(part) if part.isdigit() else part)
    return parts

base = Path.home() / '.claude' / 'plugins' / 'cache' / 'rtfi' / 'rtfi'
if not base.is_dir():
    raise SystemExit(0)

for version_dir in sorted((p for p in base.iterdir() if p.is_dir()), key=version_key, reverse=True):
    candidate = version_dir / 'scripts' / 'rtfi_statusline.py'
    if candidate.is_file():
        print(candidate)
        break
INNERPY
)"
fi

if [ -z "$script_path" ] || [ ! -f "$script_path" ]; then
  printf 'RTFI --'
  exit 0
fi

printf '%s' "$input" | "$venv_python" "$script_path"
