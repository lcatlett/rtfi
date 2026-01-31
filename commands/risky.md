---
name: risky
description: Show sessions that exceeded the risk threshold
allowed-tools:
  - Bash
argument-hint: "[--threshold N] [--limit N]"
---

# RTFI Risky Sessions Command

Show sessions where the risk score exceeded the threshold, indicating high probability of instruction non-compliance.

## Instructions

Run the RTFI CLI to list high-risk sessions:

```bash
python3 $CLAUDE_PLUGIN_ROOT/scripts/rtfi_cli.py risky $ARGUMENTS
```

## Output Format

Display results sorted by peak risk score (highest first), showing:
- Session ID
- Start time
- Peak risk score
- Tool calls
- Agent spawns

## Usage Examples

- `/rtfi:risky` - Show sessions exceeding default threshold (70)
- `/rtfi:risky --threshold 50` - Show sessions exceeding 50
- `/rtfi:risky --limit 10` - Show top 10 risky sessions
