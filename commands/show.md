---
name: show
description: Show detailed information about a specific session
allowed-tools:
  - Bash
argument-hint: "<session-id>"
---

# RTFI Show Session Command

Display detailed information about a specific RTFI session, including all tracked events.

## Instructions

Run the RTFI CLI to show session details:

```bash
python3 $CLAUDE_PLUGIN_ROOT/scripts/rtfi_cli.py show $ARGUMENTS
```

## Output Format

Display:
- Full session ID
- Start and end times
- Outcome status
- Peak and final risk scores
- Total tool calls and agent spawns
- Event timeline (if available)

## Usage Examples

- `/rtfi:show abc123` - Show session starting with "abc123"
- `/rtfi:show 550e8400-e29b-41d4-a716-446655440000` - Show by full ID
