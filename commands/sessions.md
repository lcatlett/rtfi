---
name: sessions
description: List recent RTFI sessions with risk scores
allowed-tools:
  - Bash
argument-hint: "[--limit N]"
---

# RTFI Sessions Command

List recent sessions tracked by RTFI with their risk scores.

## Instructions

Run the RTFI CLI to list recent sessions:

```bash
python3 $CLAUDE_PLUGIN_ROOT/scripts/rtfi_cli.py sessions $ARGUMENTS
```

## Output Format

Display results in a formatted table showing:
- Session ID (truncated)
- Start time
- Peak risk score (color-coded: green <50, yellow 50-70, red >70)
- Total tool calls
- Agent spawns
- Outcome

## Usage Examples

- `/rtfi:sessions` - Show last 20 sessions
- `/rtfi:sessions --limit 50` - Show last 50 sessions
