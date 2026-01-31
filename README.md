# RTFI - Real-Time Instruction Compliance Risk Scoring

A Claude Code plugin that predicts when AI sessions are at risk of ignoring your instructions, enabling proactive intervention before failures occur.

## Problem

LLMs ignore explicit instructions at unpredictable rates. You provide clear guidelines in CLAUDE.md, system prompts, or custom instructions - and the AI disregards them without notification. Discovery happens only after significant work is wasted.

## Solution

RTFI calculates a real-time **Compliance Risk Score** based on measurable session factors:

| Factor | Weight | Rationale |
|--------|--------|-----------|
| Context length | 25% | Longer context → earlier instructions deprioritized |
| Agent fanout | 30% | Parallel agents → highest risk factor |
| Autonomy depth | 25% | Steps since human confirmation |
| Decision velocity | 20% | Tool calls per minute |

When the score exceeds your threshold (default: 70), RTFI alerts you before failures occur.

## Installation

```bash
# Add plugin to Claude Code
claude --add-plugin /path/to/rtfi/plugin
```

## Commands

| Command | Description |
|---------|-------------|
| `/rtfi:sessions` | List recent sessions with risk scores |
| `/rtfi:risky` | Show sessions that exceeded threshold |
| `/rtfi:show <id>` | Detailed view of a specific session |
| `/rtfi:status` | RTFI status and statistics |

## Configuration

Create `.claude/rtfi.local.md` in your project or home directory:

```markdown
# RTFI Settings

## Threshold
Risk score threshold for alerts (0-100): 70

## Action Mode
What happens when threshold exceeded: alert
Options: alert, block, confirm
```

## How It Works

1. **Hooks track session activity** - Every tool call, agent spawn, and response
2. **Risk score calculated in real-time** - Deterministic formula, no LLM needed
3. **Alerts fire at threshold** - Warning appears in stderr
4. **Session data logged** - SQLite database at `~/.rtfi/rtfi.db`

## Data Storage

Sessions and events stored locally at `~/.rtfi/rtfi.db`. No cloud dependency.

## License

MIT
