---
name: demo
description: Run a synthetic high-risk scenario against the live database to demonstrate the RTFI dashboard gauge climbing in real time
allowed-tools:
  - Bash
argument-hint: "[--scenario fanout|velocity|combined] [--fast] [--delay N]"
---

# RTFI Demo Scenario

Run a synthetic session that drives the risk gauge from green → amber → red. Use this with the dashboard open to show a live customer demo.

## Instructions

1. Remind the user to have the dashboard running first (`/rtfi:dashboard` or `python3 "$CLAUDE_PLUGIN_ROOT/scripts/rtfi_dashboard.py"`).

2. Run the demo scenario:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/demo_scenario.py" $ARGUMENTS
```

3. After it completes, note the session ID printed at the end and suggest running `/rtfi:check <session-id>` for a full compliance report.

## Scenarios

| Scenario | What it demonstrates | How it breaches |
|----------|---------------------|-----------------|
| `combined` (default) | Realistic mix of violations | 5 agents + 13 tool calls without checkpoint |
| `fanout` | Agent fan-out violation | 5 parallel agents exceeding limit |
| `velocity` | Rapid tool calls + autonomy drift | 10+ steps without confirmation |

## Usage Examples

- `/rtfi:demo` — run the combined scenario with 0.6s delays (watch the gauge)
- `/rtfi:demo --scenario fanout` — pure agent fan-out
- `/rtfi:demo --scenario velocity` — rapid tool-call velocity spike
- `/rtfi:demo --fast` — no delays (instant, for testing)
- `/rtfi:demo --delay 1.5` — slower pacing for live presentations
