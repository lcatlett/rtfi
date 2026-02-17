# RTFI Plugin — Comprehensive Enterprise Analysis

**Date:** 2026-02-16
**Scope:** End-to-end analysis of the RTFI Claude Code plugin against enterprise best practices
**Version Analyzed:** 0.1.1

---

## Table of Contents

1. [Architecture & Code Quality Review](#1-architecture--code-quality-review)
2. [Security & Privacy Assessment](#2-security--privacy-assessment)
3. [Red Flags & Critical Issues](#3-red-flags--critical-issues)
4. [Enterprise Readiness](#4-enterprise-readiness)
5. [User Experience (UX) Improvements](#5-user-experience-ux-improvements)
6. [Developer Experience (DX) Improvements](#6-developer-experience-dx-improvements)
7. [Specific Recommendations](#7-specific-recommendations)

---

## 1. Architecture & Code Quality Review

### 1.1 Code Structure & Modularity

The plugin follows a reasonable modular layout:

```
scripts/
  hook_handler.py       # Hook entry point (405 lines)
  rtfi_cli.py           # CLI entry point (245 lines)
  rtfi/
    models/events.py    # Pydantic data models
    scoring/engine.py   # Risk calculation engine
    storage/database.py # SQLite persistence
    cli/main.py         # Dead code (click+rich CLI)
```

**Strengths:**
- Clean separation between models, scoring engine, and storage
- Pydantic v2 models provide data validation and type safety
- Hook handler is self-contained with a simple dispatch pattern
- The risk scoring formula is deterministic and easy to reason about

**Weaknesses:**

⚠️ **Process-per-hook architecture fundamentally breaks in-memory state.** This is the most critical architectural issue in the entire codebase. Each hook invocation (`python3 hook_handler.py <type>`) spawns a **new Python process**. This means:

```python
# hook_handler.py lines 178-182
SESSION_ID_ENV = "RTFI_SESSION_ID"
db = Database()
settings = load_settings()
engine = RiskEngine(threshold=settings["threshold"])
```

The `RiskEngine._sessions` dict starts **empty every single invocation**. The `SessionState` dataclass that tracks `tool_timestamps` (for velocity), `active_agents`, and `steps_since_confirm` is reconstructed from scratch each time. This means:

- **Decision velocity is always ~0** — timestamps from prior tool calls are lost
- **Agent fanout resets** — previous agent spawns aren't remembered
- **Autonomy depth resets** — steps since confirm is always 0-1
- **`handle_stop` → `engine.end_session()`** always returns `None` because the engine has no sessions

The `handle_pre_tool_use` works around this partially by auto-creating sessions, but **the risk score factors are never cumulative across calls**, making the core feature non-functional in production.

⚠️ **`os.environ[SESSION_ID_ENV]` doesn't persist across processes.** The `CLAUDE_ENV_FILE` mechanism could bridge this gap, but it's only written during `session_start`, and it's unclear whether Claude Code re-reads it before subsequent hook invocations.

### 1.2 Error Handling & Graceful Degradation

**Strengths:**
- Top-level try/except in `main()` ensures hooks never crash Claude Code
- Always returns `{"continue": True, "decision": "approve"}` on failure
- Logging to `~/.rtfi/rtfi.log` for debugging
- Separate audit log for compliance events
- Auto-install fallback for pydantic dependency

**Weaknesses:**
- `handle_post_tool_use` silently swallows `ValueError` with a bare `pass` — no logging, no indication that session state was lost
- `handle_stop` returns bare `{"decision": "approve"}` when session isn't found — no system message explaining what happened
- `load_settings()` uses `float()` and `int()` on environment variables without try/except — a malformed `RTFI_THRESHOLD=abc` will crash at module load
- The auto-install mechanism (`pip install --user`) during hook execution can silently fail in restricted environments and adds latency to the first invocation

### 1.3 Database Schema & Persistence

**Strengths:**
- Clean `CREATE TABLE IF NOT EXISTS` schema with appropriate data types
- Indexes on `session_id` and `timestamp` for event queries
- `INSERT OR REPLACE` for idempotent session upserts
- Session retention/purge mechanism

**Weaknesses:**

```python
# database.py — _connect method
def _connect(self):
    """Get a database connection."""
    conn = sqlite3.connect(str(self.db_path))
    conn.row_factory = sqlite3.Row
    return conn
```

- **Every method opens and closes a new connection** — no connection pooling or reuse. While acceptable for SQLite, it adds unnecessary overhead per hook call.
- **Foreign key constraints are defined but NOT enforced** — SQLite requires `PRAGMA foreign_keys = ON` per connection, which is never set.
- **Naive datetime handling** — `datetime.now()` without timezone throughout. Causes issues in multi-timezone environments and makes log correlation unreliable.
- **`get_recent_sessions(limit=10000)`** in the CLI loads all sessions into memory. The `show` command fetches up to 1000 sessions to do prefix matching client-side instead of using a SQL `LIKE` query.

### 1.4 Hook Implementation

**Strengths:**
- 5000ms timeout is generous enough for Python startup + SQLite I/O
- Wildcard `"*"` matcher correctly intercepts all tool calls
- Clean stdin/stdout JSON protocol

**Weaknesses:**
- **Python cold start on every hook** — each tool call spawns a full Python interpreter, imports pydantic, initializes SQLite, and loads settings. This likely consumes 200-500ms of the 5000ms budget.
- No caching of settings between calls
- The `PostToolUse` hook does little of value — it creates a `RESPONSE` event but the score computed from it is discarded (not returned to Claude Code)

---

## 2. Security & Privacy Assessment

### 2.1 Input Validation

**Strengths:**
- `validate_hook_data()` sanitizes tool names (length check, type check) and context tokens (range check)
- `validate_env_file_path()` restricts file writes to `/tmp`, `$TMPDIR`, and `~/.claude` directories — a solid defense against path traversal
- SQL injection is mitigated by parameterized queries throughout `database.py`

**Concerns:**

1. **Auto-installing packages via `pip install --user` during hook execution** is a supply chain risk vector. A compromised PyPI mirror or MITM attack could inject malicious code. In enterprise environments with network restrictions or custom registries, this will also fail silently.

2. **No input validation on stdin JSON size** — `sys.stdin.read()` reads unbounded input. A malformed or malicious hook input could cause memory exhaustion.

3. **Logging writes to a world-accessible directory** (`~/.rtfi/`) without restrictive file permissions. The `LOG_DIR.mkdir(parents=True, exist_ok=True)` doesn't set mode.

4. **Audit log format is pipe-delimited plaintext** — easy to tamper with. No integrity protection (signing, checksums) on audit records.

### 2.2 Data Storage Practices

- SQLite database at `~/.rtfi/rtfi.db` stores session metadata and risk events. No PII is collected beyond session IDs (UUIDs) and tool names.
- No encryption at rest for the database or logs.
- Retention policy exists (`purge_old_sessions`) but is only triggered on `session_start` — if no new sessions start, old data persists indefinitely.
- Tool names are stored in events, which could reveal project-specific information (file paths via Read/Write tool names).

### 2.3 Dependency Management

- Only runtime dependency is `pydantic>=2.0.0` — minimal attack surface.
- No `requirements.txt` lockfile with pinned hashes for reproducible builds.
- The dead `cli/main.py` imports `click` and `rich` which are **undeclared dependencies** — they would fail at runtime if ever invoked.

---

## 3. Red Flags & Critical Issues

### 🔴 Critical: Risk Scores Are Non-Functional in Production

The process-per-hook architecture means the `RiskEngine`'s in-memory `SessionState` — which tracks tool timestamps, agent counts, and autonomy depth — resets on **every hook call**. The risk score computed in each invocation only reflects the current single tool call, not the cumulative session state.

**Evidence:** In `handle_pre_tool_use`, when the engine doesn't have the session (which is *every time* in production), it catches `ValueError` and re-creates the session:

```python
# hook_handler.py lines 247-256
    try:
        score = engine.process_event(event)
        db.save_event(event)
    except ValueError:
        # Session not found, re-initialize
        session = Session(id=session_id)
        engine.start_session(session)
        db.save_session(session)
        score = engine.process_event(event)
        db.save_event(event)
```

This means every tool call starts with `active_agents=0`, `steps_since_confirm=0`, and `tools_per_minute=0`. The threshold will **never be exceeded** under normal usage.

### 🔴 Critical: `handle_stop` Never Finalizes Sessions

`engine.end_session(session_id)` will always return `None` because the engine was just initialized in the same process invocation and has no sessions in its `_sessions` dict. The entire session finalization block (saving final risk score, outcome, audit logging) is dead code in production.

### 🔴 Critical: Python Version Mismatch

`pyproject.toml`, `marketplace.json`, and mypy config all require Python ≥3.11, but the runtime environment is Python 3.10.19. The `str | None` type union syntax used in `validate_env_file_path()` was available in 3.10, but this mismatch indicates the plugin hasn't been tested on the actual deployment runtime.

### 🟡 High: Version Inconsistency

`.claude-plugin/plugin.json` still shows `"version": "0.1.0"` while all other version-bearing files were bumped to `0.1.1`.

### 🟡 High: Tests Cannot Run

`pytest` is not installed in the runtime environment. The tests are declared as optional dev dependencies in `pyproject.toml` but there's no `pip install -e ".[dev]"` step documented or automated.

### 🟡 High: Tests Pass Only In-Process

The existing tests call `handle_session_start()`, `handle_pre_tool_use()`, and `handle_stop()` sequentially **within the same Python process**, which preserves the `engine._sessions` dict between calls. This means the tests validate behavior that **cannot occur in production** where each handler runs in a separate process.

### 🟡 Moderate: `agent_fanout` Only Increments

```python
# engine.py
        if event.event_type == EventType.AGENT_SPAWN:
            state.active_agents += 1
```

`active_agents` is incremented on every `Task` tool call but never decremented when agents complete. Even if the process-per-hook issue were fixed, the agent count would only grow, never shrink.

### 🟡 Moderate: Silent Failures

- `handle_post_tool_use` silently catches and ignores `ValueError` when the session isn't found
- `handle_stop` returns a bare `{"decision": "approve"}` with no system message when the session isn't found (which is always, in production)
- Malformed `RTFI_THRESHOLD` environment variable crashes at module load before the exception handler in `main()` can catch it

---

## 4. Enterprise Readiness

### 4.1 Scalability

- **Single-user, single-machine only.** SQLite doesn't support concurrent writes from multiple processes well. With hooks firing on every tool call, there's a real risk of `SQLITE_BUSY` errors during fast tool sequences.
- **No connection pooling** — each hook opens/closes a SQLite connection.
- **O(n) session lookup** in CLI commands (loads all sessions into memory for prefix matching and stats calculation).
- **No data archival** — retention purge only runs on `session_start`, and the database can grow unbounded between sessions.

### 4.2 Multi-User / Multi-Project Support

- Database is shared at `~/.rtfi/rtfi.db` regardless of project. No project-level isolation.
- No concept of project ID in sessions — can't filter sessions by project.
- Settings file search includes `$CLAUDE_PROJECT_DIR/.claude/rtfi.local.md` which provides per-project config, but the database doesn't partition by project.

### 4.3 Configuration Management

- Environment variables (`RTFI_THRESHOLD`, `RTFI_ACTION_MODE`, `RTFI_RETENTION_DAYS`) provide basic configuration.
- Markdown-based settings file parsing is fragile — relies on exact line prefixes like `"Risk score threshold"`.
- No validation on threshold range (could be negative, >100, or non-numeric from env var).
- No configuration schema or documentation of all supported settings.

### 4.4 Observability

**Strengths:**
- Dual logging (operational `rtfi.log` + compliance `audit.log`)
- Health check command (`/rtfi:health`)
- Status command with aggregate statistics

**Weaknesses:**
- No structured logging (plain text format)
- No log rotation configured — logs grow unbounded
- No metrics export (Prometheus, StatsD, etc.)
- No alerting integration beyond in-session system messages
- Audit log lacks integrity protection

---

## 5. User Experience (UX) Improvements

### 5.1 Error Messages & Feedback

- Risk warnings are informative: `"RTFI WARNING: Risk score 75.2 exceeds threshold 70. Factors: context=0.45, agents=0.60..."` — this is well-designed.
- Session start message `"RTFI: Session tracking started (threshold: 70)"` is clear.
- However, the session summary at stop **never appears** (due to the `handle_stop` bug), so users get no end-of-session feedback.

### 5.2 Discoverability

- **5 commands** (`sessions`, `risky`, `show`, `status`, `health`) are well-structured with appropriate argument hints.
- Command markdown files include usage examples.
- The `session-analyzer` agent provides natural language interaction for investigating high-risk sessions.
- The `risk-scoring` skill provides good reference documentation for threshold tuning.

### 5.3 Documentation

- `docs/ARCHITECTURE.md` is thorough with ASCII diagrams and phased rollout plan.
- `skills/risk-scoring/SKILL.md` provides excellent interpretive guidance for risk scores.
- `docs/TROUBLESHOOTING.md` covers common issues.
- **Missing:** No setup/installation documentation for enterprise deployment (e.g., behind corporate proxy, in restricted Python environments).

### 5.4 Onboarding

- Plugin installation presumably through Claude Code marketplace, but post-install setup relies on `scripts/setup.sh` or auto-install of pydantic — neither is clearly documented in the plugin flow.
- No first-run experience or guided configuration.

---

## 6. Developer Experience (DX) Improvements

### 6.1 Installation & Configuration

- No `Makefile` or standardized development setup commands.
- Dev dependencies require `pip install -e ".[dev]"` but this isn't documented.
- `pyproject.toml` declares dev tools (pytest, mypy, ruff) but there's no CI configuration (GitHub Actions, etc.).

### 6.2 Code Maintainability

- **Dead code**: `scripts/rtfi/cli/main.py` (168 lines) duplicates `rtfi_cli.py` functionality using undeclared `click` and `rich` dependencies. Should be removed.
- **Duplicate marketplace.json**: Both `marketplace.json` (root) and `.claude-plugin/marketplace.json` exist with identical content — unclear which is canonical.
- **Module-level side effects**: `hook_handler.py` creates directories, configures logging, initializes database connections, and loads settings all at import time. This makes testing and reuse difficult.
- **No type stubs or inline type annotations** for the hook handler functions' return types (they return `dict` but could use `TypedDict`).

### 6.3 Testing Coverage

- **3 test files** with reasonable coverage of individual components:
  - `test_hook_handler.py`: 16 tests covering validation, settings, and handler flows
  - `test_scoring.py`: 8 tests covering score calculation, engine lifecycle, and callbacks
  - `test_storage.py`: 5 tests covering database CRUD operations
- **Critical gap**: Tests validate in-process behavior that doesn't match production architecture (separate processes per hook)
- **No integration tests** that simulate the actual hook invocation pattern (subprocess calls with stdin/stdout JSON)
- **No performance/timing tests** to validate hook execution stays within 5000ms budget
- **No CI/CD pipeline** defined

### 6.4 Development Workflow

- ruff and mypy configured in `pyproject.toml` but no pre-commit hooks
- No `make test`, `make lint`, `make typecheck` shortcuts
- No `.github/workflows/` CI configuration

---

## 7. Specific Recommendations

### 🔴 Critical — Must Fix Before Production Use

| # | Issue | Recommendation |
|---|-------|---------------|
| C1 | **Risk scores reset every hook call** — process-per-hook architecture loses all in-memory state | **Reconstruct session state from the database** at the start of each hook invocation. Load `tool_timestamps`, `active_agents`, `steps_since_confirm` from the `risk_events` table. Alternatively, store `SessionState` fields in the `sessions` table and reload them. |
| C2 | **`handle_stop` never finalizes sessions** — engine has no sessions in memory | After fixing C1, `end_session` will work. In the interim, load session from database directly in `handle_stop` instead of relying on the engine. |
| C3 | **Python 3.10 vs 3.11 requirement** | Either upgrade the runtime to 3.11+ or change `requires-python` to `>=3.10` and replace any 3.11-specific syntax. |
| C4 | **Malformed env vars crash at module load** | Wrap `float(os.environ.get(...))` and `int(os.environ.get(...))` in try/except with defaults in `load_settings()`. |

### 🟡 High — Should Fix for Enterprise Deployment

| # | Issue | Recommendation |
|---|-------|---------------|
| H1 | **Tests validate non-production behavior** | Add integration tests that invoke `hook_handler.py` as subprocess (matching production). Mark existing in-process tests as unit tests. |
| H2 | **Version inconsistency** | Bump `.claude-plugin/plugin.json` to 0.1.1. Consolidate version to a single source of truth. |
| H3 | **Dead code: `cli/main.py`** | Remove `scripts/rtfi/cli/main.py` and `scripts/rtfi/cli/__init__.py`. |
| H4 | **No foreign key enforcement** | Add `conn.execute("PRAGMA foreign_keys = ON")` after each `sqlite3.connect()` call. |
| H5 | **`pip install --user` in hook execution** | Move to a proper installation step. Document dependency installation in plugin setup. Remove auto-install from hook handler or limit it to first-run setup script only. |
| H6 | **`agent_fanout` never decrements** | Track agent completion (e.g., when Task tool results are received in `PostToolUse`) or store agent spawn timestamps and use a time-decay model. |
| H7 | **Duplicate `marketplace.json`** | Remove root `marketplace.json` — `.claude-plugin/marketplace.json` is the canonical location. |
| H8 | **No log rotation** | Add `RotatingFileHandler` with a max size (e.g., 10MB) and backup count. |

### 🟢 Medium — Nice to Have for Better Experience

| # | Issue | Recommendation |
|---|-------|---------------|
| M1 | **No project isolation in database** | Add `project_dir` column to `sessions` table. Populate from `$CLAUDE_PROJECT_DIR`. |
| M2 | **Naive datetimes** | Use `datetime.now(timezone.utc)` throughout. Store as ISO 8601 with timezone. |
| M3 | **O(n) session prefix lookup** | Use SQL `WHERE id LIKE ?` query instead of loading all sessions into memory. |
| M4 | **No structured logging** | Switch to JSON-formatted log entries for easier parsing and aggregation. |
| M5 | **Audit log integrity** | Add HMAC signatures to audit log entries to detect tampering. |
| M6 | **Settings file parsing is fragile** | Use a proper format (TOML/YAML) or at minimum document exact expected line formats. |
| M7 | **No CI/CD pipeline** | Add GitHub Actions workflow for linting (ruff), type checking (mypy), and tests (pytest). |
| M8 | **Restrict file permissions** | Create `~/.rtfi/` directory with `mode=0o700` and database/log files with `0o600`. |
| M9 | **No stdin size limit** | Add `sys.stdin.read(MAX_INPUT_SIZE)` to prevent memory exhaustion from malformed input. |

### ⚪ Low — Future Enhancements

| # | Issue | Recommendation |
|---|-------|---------------|
| L1 | **No connection pooling** | Implement a simple connection cache or use a single connection per hook invocation. |
| L2 | **PostToolUse hook adds little value** | Consider removing it or using it to track agent completion (for H6). |
| L3 | **No metrics export** | Add optional Prometheus/StatsD metrics for enterprise monitoring. |
| L4 | **No Makefile/taskrunner** | Add `Makefile` with `test`, `lint`, `typecheck`, `format` targets. |
| L5 | **No first-run experience** | Add a `/rtfi:setup` command that validates dependencies, creates config, and runs health check. |
| L6 | **Configurable normalization thresholds** | The hardcoded max values (128k tokens, 5 agents, 10 steps, 20 tools/min) should be configurable per-project. |

---

## Summary

The RTFI plugin has a **strong conceptual foundation** — the risk scoring formula is well-researched and the plugin architecture (hooks, commands, agents, skills) follows Claude Code best practices. However, the **process-per-hook execution model** fundamentally undermines the core feature: **risk scores never accumulate across tool calls**, meaning the threshold will essentially never trigger in production.

Fixing C1/C2 by reconstructing session state from the database at each hook invocation is the single most impactful improvement. Until then, the plugin is effectively an event logger with no functional risk alerting.

### Priority Order for Implementation

1. **C1 + C2** — Reconstruct session state from DB (unlocks core functionality)
2. **C4** — Env var crash guard (prevents total hook failure)
3. **C3** — Python version alignment (prevents deployment failures)
4. **H1** — Integration tests (prevents regression of C1/C2 fix)
5. **H3 + H7** — Remove dead code and duplicates (reduces confusion)
6. **H4 + H8** — Foreign keys and log rotation (data integrity)
7. **M1-M9** — Polish and enterprise hardening
8. **L1-L6** — Future enhancements
