# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-02-18

### Added
- **Web dashboard** (`scripts/rtfi_dashboard.py`): Single-file HTMX web server on port 7430. Live risk gauge updates every 2 seconds, factor bars show per-component breakdown, session list auto-refreshes every 10 seconds, session detail loads via HTMX navigation without full page reload. Dark professional theme; zero new runtime dependencies (Python stdlib + CDN HTMX).
- **`/rtfi:dashboard` command**: Slash command to launch the web dashboard.
- **Demo scenario generator** (`scripts/demo_scenario.py`): Three scripted scenarios (`fanout`, `velocity`, `combined`) that write synthetic events to the live database so the dashboard gauge visibly climbs from green → amber → red. Configurable delay for paced live presentations; `--fast` for instant replay.
- **Compliance check tool** (`scripts/demo_compliance_check.py`): Replays a session's event sequence through the scoring engine and checks each event against declared constraints (max agents, confirm interval, context guard, risk threshold). Produces a per-constraint PASS / WARN / FAIL report with exact violation location (step, tool, timestamp), score decomposition by factor, and the verbatim `systemMessage` RTFI sent to Claude at breach. Supports `--latest`, session ID prefix, `--json`, and `--constraints` for custom constraint sets.
- **`/rtfi:demo` command**: Slash command for the scenario generator; instructs Claude to prompt for dashboard-first and suggest compliance check after.
- **`/rtfi:check` command**: Slash command for the compliance check tool; instructs Claude to summarize verdict, primary risk driver, and breach warning.

## [1.0.0] - 2026-02-17

### Added
- **Structured JSON logging** (`~/.rtfi/rtfi.log`): All events serialized as JSON with timestamp, level, session_id, and hook_type fields — parseable by `jq`, Datadog, Splunk.
- **HMAC-signed audit trail** (`~/.rtfi/audit.log`): Every session start, threshold breach, and session end signed with HMAC-SHA256 using a machine-local key. `verify_audit_log()` validates integrity of all entries.
- **Config file support** (`~/.rtfi/config.env`): Layered configuration — env vars override config file, which overrides legacy `.claude/rtfi.local.md`, which overrides built-in defaults.
- **StatsD metrics export**: Optional metrics via `RTFI_STATSD_HOST`/`RTFI_STATSD_PORT` — emits `risk_score`, `tool_calls`, `agent_spawns`, `threshold_exceeded`, and `hook_latency_ms` gauges.
- **Setup wizard** (`python3 scripts/rtfi_cli.py setup`): Interactive first-run wizard that validates Python version, dependency availability, database connectivity, hook configuration, and writes a default `config.env`.
- **Architecture documentation** (`docs/ARCHITECTURE.md`): C4 context and component diagrams, data flow sequences, storage schema, security model, and deployment guide.
- **Architecture Decision Records** (`docs/adr/`): Five ADRs covering deterministic scoring algorithm, fresh-process hook model, agent time-decay, layered configuration, and HMAC audit trail.

### Changed
- Plugin schema updated to match Claude Code marketplace requirements (field renames, array structure corrections).
- Log rotation added: `rtfi.log` and `audit.log` rotate at 5 MB with 3 backups.
- Database and log directory permissions hardened to `0o700` (directory) / `0o600` (files).

## [0.3.0] - 2026-02-16

### Added
- **Agent time-decay** (H6): Active agent count now uses a 5-minute sliding window — agents that spawned more than 5 minutes ago no longer contribute to the fanout score. Prevents artificially elevated scores in long sessions.
- **Project isolation** (M1): Sessions can be filtered by `project_dir`; `CLAUDE_PROJECT_DIR` env var scopes queries to the current project.
- **UTC timestamps** (M2): All datetimes stored with explicit UTC timezone. Naive timestamps in existing databases are treated as UTC for backward compatibility.

### Changed
- Query optimization (M3): `find_session_by_prefix` uses indexed `LIKE` query; `get_high_risk_sessions` supports optional project filter.
- `to_dict` / `from_dict` now serializes agent spawn timestamps as ISO strings rather than a bare count, enabling accurate decay replay.

## [0.2.2] - 2026-02-16

### Added
- **Integration test suite** (`tests/test_integration.py`): Seven cross-process tests covering HMAC state persistence, agent fanout accumulation, session lifecycle, and malformed env var resilience.
- **GitHub Actions workflow** (`.github/workflows/ci.yml`): Runs full test suite on Python 3.10+ across pushes and pull requests.
- **Makefile**: `make test`, `make lint`, `make install` convenience targets.

## [0.2.1] - 2026-02-16

### Changed
- Removed dead code paths and unused imports throughout hook handler and CLI.
- File permissions hardened: database directory `0o700`, database file `0o600`.
- Log rotation added to prevent unbounded log growth.
- Input validation added for `tool_name` (max 256 chars) and `context_tokens` (non-negative int, < 10M).
- `CLAUDE_ENV_FILE` path validation: only `/tmp`, `/var/tmp`, and `~/.claude` prefixes accepted.
- stdin read capped at 1 MB to prevent memory exhaustion.

## [0.2.0] - 2026-02-15

### Fixed
- **Cross-process state persistence** (C1, C2): Session state (token counts, agent spawn timestamps, steps since confirm) is now persisted to SQLite after every hook invocation and rehydrated at the start of the next. Fixes risk scores resetting to zero between tool calls.

## [0.1.1] - 2026-02-16

### Fixed
- **Auto-install dependencies**: Hook handler and CLI now automatically install `pydantic` if missing
- **Startup errors**: Resolved `ModuleNotFoundError: No module named 'pydantic'` on plugin startup
- **Graceful degradation**: Plugin continues to work even if dependency installation fails

### Added
- **Setup script**: `scripts/setup.sh` for easy one-command installation
- **Troubleshooting guide**: Comprehensive guide at `docs/TROUBLESHOOTING.md`
- **Health check improvements**: Better error messages and dependency verification

### Changed
- **Installation process**: Updated README with clearer installation instructions
- **Dependency handling**: Dependencies are now installed automatically on first use

## [0.1.0] - 2026-01-31

### Added
- Initial release
- Real-time risk scoring based on session factors
- Hook system for tracking tool usage and session lifecycle
- CLI commands: sessions, risky, show, status, health
- Session analyzer agent for root cause analysis
- Risk scoring skill for threshold tuning guidance
- SQLite database for session storage
- Configurable threshold alerts (alert/block/confirm modes)

