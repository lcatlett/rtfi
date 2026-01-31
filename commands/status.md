---
name: status
description: Show RTFI status and statistics
allowed-tools:
  - Bash
---

# RTFI Status Command

Display RTFI status, configuration, and aggregate statistics.

## Instructions

Run the RTFI CLI to show status:

```bash
python3 $CLAUDE_PLUGIN_ROOT/scripts/rtfi_cli.py status
```

## Output Format

Display:
- Database location
- Current threshold setting
- Action mode (alert/block/confirm)
- Total sessions tracked
- High-risk session count
- Average peak risk score
- Total tool calls and agent spawns across all sessions

## Usage Examples

- `/rtfi:status` - Show current RTFI status and statistics
