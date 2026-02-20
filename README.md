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

### Quick Start

```bash
# Clone the repository
git clone https://github.com/lcatlett/rtfi.git
cd rtfi

# Run setup
bash scripts/setup.sh
```

### Install Dependencies

Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv pip install --system "pydantic>=2.0.0"
```

Using pip:

```bash
pip3 install "pydantic>=2.0.0"
```

### First-Run Setup

After installing dependencies, run the setup wizard to validate your environment and create the default config:

```bash
python3 scripts/rtfi_cli.py setup
```

## Commands

| Command | Description |
|---------|-------------|
| `/rtfi:sessions` | List recent sessions with risk scores |
| `/rtfi:risky` | Show sessions that exceeded threshold |
| `/rtfi:show <id>` | Detailed view of a specific session |
| `/rtfi:status` | RTFI status and statistics |
| `/rtfi:health` | Run health check |
| `/rtfi:setup` | First-run setup and validation |
| `/rtfi:dashboard` | Launch the web dashboard |
| `/rtfi:demo` | Run a synthetic high-risk scenario against the live database |
| `/rtfi:check` | Validate a session against declared constraints |

## Web Dashboard

RTFI includes a live web dashboard for customer demos and monitoring. It requires no extra dependencies — Python stdlib only, with HTMX loaded from CDN.

```bash
# Start the dashboard (opens browser automatically)
python3 scripts/rtfi_dashboard.py

# Specify a port or suppress browser
python3 scripts/rtfi_dashboard.py --port 7430 --no-browser
```

Open **http://localhost:7430**. The dashboard shows:

- **Live risk gauge** — glowing circle that updates every 2 seconds during an active Claude session, color-coded green / amber / red
- **Factor bars** — real-time breakdown of context length, agent fanout, autonomy depth, and decision velocity
- **Session history** — last 25 sessions, clickable rows with peak score badges
- **Session detail** — full event timeline with per-event risk scores; navigates via HTMX without page reload

Stop with `Ctrl+C`.

## Demo and Compliance Validation

Two scripts support live demos and post-session compliance analysis.

### Synthetic scenario (`demo_scenario.py`)

Drives the database with a scripted high-risk session so you can watch the gauge climb:

```bash
# Terminal 1 — keep the dashboard open
python3 scripts/rtfi_dashboard.py

# Terminal 2 — run the scenario (0.6s between events by default)
python3 scripts/demo_scenario.py                    # combined (breaches ~75)
python3 scripts/demo_scenario.py --scenario fanout  # 5 parallel agents
python3 scripts/demo_scenario.py --scenario velocity # rapid tool calls
python3 scripts/demo_scenario.py --fast             # instant (no delays)
```

### Compliance check (`demo_compliance_check.py`)

Replays a session's event sequence and checks it against declared constraints:

```bash
python3 scripts/demo_compliance_check.py --latest          # most recent session
python3 scripts/demo_compliance_check.py <session-id>      # by prefix
python3 scripts/demo_compliance_check.py --latest --json   # machine-readable
python3 scripts/demo_compliance_check.py --latest --constraints constraints.json
```

Default constraints checked: max 2 parallel agents, confirm every 5 steps, 80k token context guard, risk threshold ≤ 70. All thresholds are configurable via JSON file.

Output: per-constraint PASS / WARN / FAIL verdict, exact violation location (step number, tool, timestamp), score decomposition, and the verbatim `systemMessage` RTFI sent to Claude at threshold breach.

## Configuration

Settings are loaded in this priority order (highest wins):

1. Environment variables (`RTFI_THRESHOLD`, `RTFI_ACTION_MODE`, etc.)
2. Config file (`~/.rtfi/config.env`)
3. Legacy settings file (`.claude/rtfi.local.md`)
4. Built-in defaults

### Config File

Run `python3 scripts/rtfi_cli.py setup` to generate a default `~/.rtfi/config.env`, or create one manually:

```env
# Risk score threshold (0-100)
threshold=70.0

# Action when threshold exceeded: alert, block, confirm
action_mode=alert

# Data retention in days (1-3650)
retention_days=90

# Normalization thresholds (adjust for your workflow)
max_tokens=128000
max_agents=5
max_steps=10
max_tools_per_min=20.0
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RTFI_THRESHOLD` | `70.0` | Risk score alert threshold (0-100) |
| `RTFI_ACTION_MODE` | `alert` | `alert`, `block`, or `confirm` |
| `RTFI_RETENTION_DAYS` | `90` | How long to keep session data |
| `RTFI_MAX_TOKENS` | `128000` | Token normalization ceiling |
| `RTFI_MAX_AGENTS` | `5` | Agent count normalization ceiling |
| `RTFI_MAX_STEPS` | `10` | Autonomy depth normalization ceiling |
| `RTFI_MAX_TOOLS_PER_MIN` | `20.0` | Decision velocity normalization ceiling |
| `RTFI_STATSD_HOST` | *(unset)* | Enable StatsD metrics export |
| `RTFI_STATSD_PORT` | `8125` | StatsD UDP port |

## How It Works

1. **Hooks track session activity** - Every tool call, agent spawn, and response
2. **Risk score calculated in real-time** - Deterministic formula, no LLM needed
3. **Alerts fire at threshold** - Warning appears in session
4. **Session data logged** - SQLite database at `~/.rtfi/rtfi.db`
5. **Structured JSON logs** - Parseable by `jq`, Datadog, Splunk at `~/.rtfi/rtfi.log`
6. **Tamper-evident audit trail** - HMAC-signed entries at `~/.rtfi/audit.log`

## Data Storage

Sessions and events stored locally at `~/.rtfi/rtfi.db`. No cloud dependency.

## Troubleshooting

Having issues? See the [Troubleshooting Guide](docs/TROUBLESHOOTING.md) for common problems and solutions.

Quick health check:
```bash
python3 scripts/rtfi_cli.py health
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - Technical design, C4 diagrams, ADRs
- [Product Brief](docs/PRODUCT-BRIEF.md) - Problem statement and solution overview
- [Troubleshooting](docs/TROUBLESHOOTING.md) - Common issues and solutions
- [Changelog](CHANGELOG.md) - Full version history

## License

MIT
