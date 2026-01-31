---
name: health
description: Check RTFI health and verify plugin is functioning correctly
allowed-tools:
  - Bash
---

# RTFI Health Check Command

Verify RTFI is functioning correctly and display diagnostic information.

## Instructions

Run the health check script:

```bash
python3 $CLAUDE_PLUGIN_ROOT/scripts/rtfi_cli.py health
```

## Output Format

Display:
- Plugin status (OK/ERROR)
- Database connectivity
- Log file locations
- Current settings
- Session count
- Any errors detected

## Usage Examples

- `/rtfi:health` - Run health check
