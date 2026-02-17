# RTFI Implementation Plan

**Date:** 2026-02-16
**Source:** [ANALYSIS.md](./ANALYSIS.md) — Comprehensive Enterprise Analysis
**Target Version:** 0.2.0

---

## Overview

This plan addresses all 27 items identified in the RTFI analysis, organized into 5 phases. Each phase builds on the previous one and produces a shippable increment. Items are grouped by dependency and risk — the earliest phases fix correctness bugs that make the core feature non-functional, while later phases harden the plugin for enterprise use.

### Phase Summary

| Phase | Name | Items | Goal | Est. Effort |
|-------|------|-------|------|-------------|
| 1 | **Core Fix** | C1, C2, C4 | Risk scoring actually works in production | 2–3 days |
| 2 | **Stability & Cleanup** | C3, H2, H3, H5, H7, H8, M8, M9 | Clean codebase, safe defaults, no dead code | 1–2 days |
| 3 | **Testing & CI** | H1, M7, L4 | Confidence that fixes stay fixed | 1–2 days |
| 4 | **Data & Scoring Improvements** | H4, H6, M1, M2, M3, L1, L2 | Correct data, accurate scoring, efficient queries | 2–3 days |
| 5 | **Enterprise Polish** | M4, M5, M6, L3, L5, L6 | Production-grade observability and configuration | 2–3 days |

**Total estimated effort: 8–13 days**

---

## Phase 1 — Core Fix

> **Goal:** Make risk scoring functional in production. This is the single most impactful change — without it, the plugin is an event logger with no alerting capability.

### 1.1 Persist and Restore Session State (C1)

**Problem:** Each hook invocation is a separate Python process. The `RiskEngine._sessions` dict starts empty every time, so `active_agents`, `steps_since_confirm`, and `tool_timestamps` are always zero.

**Approach:** Add a `session_state` JSON column to the `sessions` table that stores the mutable `SessionState` fields. Restore it at the start of each hook invocation.

**Files to modify:**
- `scripts/rtfi/storage/database.py` — Add `session_state` column, `save_session_state()`, `load_session_state()` methods
- `scripts/rtfi/scoring/engine.py` — Add `SessionState.to_dict()` / `SessionState.from_dict()` serialization
- `scripts/hook_handler.py` — On each hook call, load session state from DB into the engine before processing

**Schema migration:**
```sql
ALTER TABLE sessions ADD COLUMN session_state TEXT;
```

**Serialized state shape:**
```json
{
  "tokens": 45000,
  "active_agents": 2,
  "steps_since_confirm": 7,
  "tool_timestamps": ["2026-02-16T10:30:01", "2026-02-16T10:30:05", "..."]
}
```

**Implementation steps:**
1. Add `to_dict()` and `from_dict(data, session)` to `SessionState`
2. Add `save_session_state(session_id, state_dict)` and `load_session_state(session_id)` to `Database`
3. In `hook_handler.py`, after getting `session_id`, call `db.load_session_state()` and hydrate the engine
4. After `engine.process_event()`, call `db.save_session_state()` to persist updated state
5. Handle migration: if `session_state` column doesn't exist, add it on first run

**Acceptance criteria:**
- Risk score increases across consecutive tool calls in separate process invocations
- `tools_per_minute` reflects actual tool call rate over the last 60 seconds
- `active_agents` accumulates across multiple `Task` tool calls
- `steps_since_confirm` increments across tool calls and resets on checkpoint

### 1.2 Fix Session Finalization in `handle_stop` (C2)

**Problem:** `engine.end_session(session_id)` always returns `None` because the engine has no sessions in memory. The entire session finalization block — saving final risk score, outcome, audit logging — is dead code.

**Approach:** After C1 is implemented, `handle_stop` can load session state from DB, hydrate the engine, then call `end_session()`. As a belt-and-suspenders measure, also add a direct-from-DB fallback.

**Files to modify:**
- `scripts/hook_handler.py` — Rewrite `handle_stop` to load state from DB before ending session

**Implementation steps:**
1. At the top of `handle_stop`, load session state from DB and hydrate the engine (same pattern as `handle_pre_tool_use` after C1)
2. Call `engine.end_session(session_id)` — now it will find the session
3. Add fallback: if engine still returns `None`, load session directly from `db.get_session(session_id)` and finalize it
4. Always produce a `systemMessage` summary, even in the fallback path

**Acceptance criteria:**
- `handle_stop` produces a session summary with peak risk, tool calls, and agent spawns
- Session outcome is set to `COMPLETED` in the database
- Audit log contains `SESSION_END` entry with final stats
- `RTFI_SESSION_ID` environment variable is cleaned up

### 1.3 Guard Against Malformed Environment Variables (C4)

**Problem:** `load_settings()` calls `float(os.environ.get("RTFI_THRESHOLD", 70.0))` and `int(os.environ.get("RTFI_RETENTION_DAYS", 90))` without try/except. A malformed value like `RTFI_THRESHOLD=abc` crashes at module load, before the top-level exception handler in `main()` can catch it.

**Files to modify:**
- `scripts/hook_handler.py` — Wrap env var parsing in try/except in `load_settings()`

**Implementation steps:**
1. Wrap each `float()` / `int()` call in try/except `ValueError`, falling back to defaults
2. Add range validation: threshold must be 0–100, retention_days must be 1–3650
3. Log a warning when falling back to defaults

**Acceptance criteria:**
- `RTFI_THRESHOLD=abc` logs a warning and uses default 70.0
- `RTFI_THRESHOLD=-50` clamps to 0 or uses default
- `RTFI_RETENTION_DAYS=0` uses default 90
- Plugin starts successfully with any combination of malformed env vars

### Phase 1 Verification

After completing Phase 1, run this end-to-end test sequence in separate shell invocations to confirm cross-process state persistence:

```bash
# 1. Start session
echo '{}' | python3 scripts/hook_handler.py session_start
# Note the session ID from RTFI_SESSION_ID or the log

# 2. Simulate 5 tool calls (each is a separate process, like production)
for i in 1 2 3 4 5; do
  echo '{"tool_name": "Read", "context_tokens": 50000}' | \
    RTFI_SESSION_ID="<session-id>" python3 scripts/hook_handler.py pre_tool_use
done
# Verify: risk score should increase with each call (autonomy_depth growing)

# 3. Simulate agent spawn
echo '{"tool_name": "Task"}' | \
  RTFI_SESSION_ID="<session-id>" python3 scripts/hook_handler.py pre_tool_use
# Verify: agent_fanout should be > 0

# 4. End session
echo '{}' | \
  RTFI_SESSION_ID="<session-id>" python3 scripts/hook_handler.py stop
# Verify: should output session summary with non-zero peak risk
```

---

## Phase 2 — Stability & Cleanup

> **Goal:** Remove dead code, fix version inconsistencies, harden file permissions, and eliminate the supply chain risk of auto-installing packages during hook execution.

### 2.1 Align Python Version Requirement (C3)

**Problem:** `pyproject.toml` requires `>=3.11` but the runtime is Python 3.10.19.

**Approach:** Lower the requirement to `>=3.10`. The codebase already uses 3.10-compatible syntax (`X | Y` union types were added in 3.10).

**Files to modify:**
- `pyproject.toml` — Change `requires-python` to `">=3.10"`
- `pyproject.toml` — Change `[tool.mypy] python_version` to `"3.10"`
- `pyproject.toml` — Change `[tool.ruff] target-version` to `"py310"`
- `.claude-plugin/marketplace.json` — Change `requirements.python` to `">=3.10"`
- `marketplace.json` — Change `requirements.python` to `">=3.10"` (until H7 removes it)

### 2.2 Fix Version Inconsistency (H2)

**Problem:** `.claude-plugin/plugin.json` shows `0.1.0` while everything else is `0.1.1`.

**Files to modify:**
- `.claude-plugin/plugin.json` — Bump version to match current release

**Future improvement:** Consolidate version to a single source of truth (`scripts/rtfi/__init__.py`) and read it from there in build/release scripts.

### 2.3 Remove Dead Code (H3)

**Problem:** `scripts/rtfi/cli/main.py` (168 lines) duplicates `rtfi_cli.py` using undeclared `click` and `rich` dependencies.

**Files to delete:**
- `scripts/rtfi/cli/main.py`
- `scripts/rtfi/cli/__init__.py`

**Verification:** Run `grep -r "from rtfi.cli" scripts/` and `grep -r "import rtfi.cli" scripts/` to confirm nothing imports from this module.

### 2.4 Move Dependency Installation Out of Hook Execution (H5)

**Problem:** Auto-installing `pydantic` via `pip install --user` during hook execution is a supply chain risk and adds latency.

**Approach:** Keep the auto-install as a last-resort fallback but add a proper installation check to the plugin setup flow.

**Files to modify:**
- `scripts/hook_handler.py` — Change auto-install to only log a clear error message with install instructions (no auto-pip)
- `scripts/rtfi_cli.py` — Same change
- `scripts/setup.sh` — Ensure it installs pydantic reliably
- `commands/health.md` — Document that `/rtfi:health` checks dependencies

**Implementation steps:**
1. Replace the `subprocess.check_call([... "pip", "install" ...])` block with a clear error message: `"RTFI: Missing dependency 'pydantic'. Run: pip3 install pydantic>=2.0.0"`
2. Keep the graceful exit (`sys.exit(0)` with `{"continue": True}`) so Claude Code isn't blocked
3. Update `scripts/setup.sh` to be the canonical installation path
4. Add a dependency check to the `health` command output

### 2.5 Remove Duplicate `marketplace.json` (H7)

**Problem:** Both `marketplace.json` (root) and `.claude-plugin/marketplace.json` exist with identical content.

**Files to delete:**
- `marketplace.json` (root) — `.claude-plugin/marketplace.json` is the canonical location

**Verification:** Confirm Claude Code plugin loader reads from `.claude-plugin/` directory.

### 2.6 Add Log Rotation (H8)

**Problem:** `rtfi.log` and `audit.log` grow unbounded.

**Files to modify:**
- `scripts/hook_handler.py` — Replace `FileHandler` with `RotatingFileHandler`

**Configuration:**
```python
from logging.handlers import RotatingFileHandler

# 5MB max per file, keep 3 backups = 20MB max total per log
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "rtfi.log", maxBytes=5_000_000, backupCount=3),
    ],
)

audit_handler = RotatingFileHandler(LOG_DIR / "audit.log", maxBytes=5_000_000, backupCount=3)
```

### 2.7 Restrict File Permissions (M8)

**Problem:** `~/.rtfi/` directory and files are created with default permissions.

**Files to modify:**
- `scripts/hook_handler.py` — Set directory mode on creation
- `scripts/rtfi/storage/database.py` — Set directory mode on creation

**Implementation:**
```python
LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
# After creating database file:
self.db_path.chmod(0o600)
```

### 2.8 Add stdin Size Limit (M9)

**Problem:** `sys.stdin.read()` reads unbounded input.

**Files to modify:**
- `scripts/hook_handler.py` — Limit stdin read size

**Implementation:**
```python
MAX_INPUT_SIZE = 1_000_000  # 1MB — generous for hook JSON
stdin_data = sys.stdin.read(MAX_INPUT_SIZE)
```

### Phase 2 Verification

```bash
# Confirm dead code removed
python3 -c "from rtfi.cli import main" 2>&1  # Should fail (module removed)

# Confirm version consistency
grep -r '"version"' .claude-plugin/ pyproject.toml scripts/rtfi/__init__.py

# Confirm log rotation
ls -la ~/.rtfi/*.log  # Should exist with reasonable sizes

# Confirm permissions
stat -f "%Lp" ~/.rtfi/          # Should be 700
stat -f "%Lp" ~/.rtfi/rtfi.db   # Should be 600

# Confirm health check works without auto-install
python3 scripts/rtfi_cli.py health
```

---

## Phase 3 — Testing & CI

> **Goal:** Add integration tests that validate cross-process behavior (matching production) and set up CI to prevent regressions.

### 3.1 Add Integration Tests That Match Production Behavior (H1)

**Problem:** Existing tests call handler functions sequentially within the same Python process, which preserves in-memory state between calls. This validates behavior that **cannot occur in production**, where each hook is a separate process. After Phase 1 fixes, we need tests that prove cross-process state persistence works.

**Approach:** Add integration tests that invoke `hook_handler.py` as a subprocess (via `subprocess.run`), exactly like Claude Code does in production. Keep existing tests as unit tests.

**Files to modify:**
- `tests/test_hook_handler.py` — Add `# Unit tests` header to existing tests
- `tests/test_integration.py` — New file with subprocess-based tests

**Implementation steps:**
1. Create `tests/test_integration.py` with a `subprocess`-based test harness
2. Each test invokes `python3 scripts/hook_handler.py <hook_type>` with JSON on stdin
3. Test sequence: `session_start` → multiple `pre_tool_use` → `stop`, each as a separate subprocess
4. Assert that risk scores increase across invocations (proving state persistence from C1)
5. Assert that `stop` produces a session summary with non-zero peak risk
6. Use a temporary database (`RTFI_DB_PATH=/tmp/rtfi_test_<uuid>.db`) to avoid polluting real data

**Key test cases:**

```python
import subprocess, json, os, uuid, tempfile

class TestCrossProcessStatePersistence:
    """Integration tests that invoke hook_handler.py as subprocess (matching production)."""

    def setup_method(self):
        self.db_path = Path(tempfile.mkdtemp()) / "test.db"
        self.session_id = str(uuid.uuid4())[:8]
        self.env = {
            **os.environ,
            "RTFI_DB_PATH": str(self.db_path),
            "RTFI_SESSION_ID": self.session_id,
        }

    def _invoke_hook(self, hook_type: str, input_data: dict) -> dict:
        result = subprocess.run(
            ["python3", "scripts/hook_handler.py", hook_type],
            input=json.dumps(input_data),
            capture_output=True, text=True, env=self.env, timeout=10,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def test_risk_score_accumulates_across_processes(self):
        """Risk score should increase with each tool call (separate process)."""
        self._invoke_hook("session_start", {})
        scores = []
        for i in range(5):
            result = self._invoke_hook("pre_tool_use", {
                "tool_name": "Read", "context_tokens": 50000
            })
            if "risk_score" in result:
                scores.append(result["risk_score"])
        # Scores should be monotonically increasing (autonomy_depth grows)
        assert scores == sorted(scores)
        assert scores[-1] > scores[0]

    def test_stop_produces_session_summary(self):
        """handle_stop should return a summary with non-zero stats."""
        self._invoke_hook("session_start", {})
        for i in range(3):
            self._invoke_hook("pre_tool_use", {"tool_name": "Read"})
        result = self._invoke_hook("stop", {})
        assert "systemMessage" in result or "system_message" in result
```

**Acceptance criteria:**
- `pytest tests/test_integration.py -v` passes with all tests green
- Tests run each hook as a separate subprocess (no shared Python state)
- Tests use isolated temp databases
- Tests clean up temp files in teardown

### 3.2 Add CI/CD Pipeline (M7)

**Problem:** No automated linting, type checking, or testing.

**Files to create:**
- `.github/workflows/ci.yml` — GitHub Actions workflow

**Workflow configuration:**

```yaml
name: CI
on: [push, pull_request]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]"
      - run: ruff check scripts/ tests/
      - run: ruff format --check scripts/ tests/
      - run: mypy scripts/rtfi/
      - run: pytest tests/ -v --tb=short
```

**Acceptance criteria:**
- CI runs on every push and PR
- Tests run on Python 3.10, 3.11, and 3.12
- Linting, formatting, type checking, and tests must all pass

### 3.3 Add Makefile (L4)

**Problem:** No standard shortcuts for common development tasks.

**Files to create:**
- `Makefile`

**Contents:**

```makefile
.PHONY: test lint typecheck format install dev clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --tb=short

lint:
	ruff check scripts/ tests/

typecheck:
	mypy scripts/rtfi/

format:
	ruff format scripts/ tests/

clean:
	rm -rf __pycache__ .mypy_cache .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
```

**Acceptance criteria:**
- `make test`, `make lint`, `make typecheck`, `make format` all work
- `make dev` installs all dev dependencies in one step

### Phase 3 Verification

```bash
# Run full CI locally
make dev
make lint
make typecheck
make test

# Confirm integration tests work
pytest tests/test_integration.py -v --tb=short
```

---

## Phase 4 — Data & Scoring Improvements

> **Goal:** Fix data integrity issues, improve scoring accuracy, and optimize database queries.

### 4.1 Enable Foreign Key Enforcement (H4)

**Problem:** SQLite foreign keys are defined in the schema but never enforced — SQLite requires `PRAGMA foreign_keys = ON` after every `connect()`.

**Files to modify:**
- `scripts/rtfi/storage/database.py` — Add pragma after each `sqlite3.connect()` call

**Implementation:**

```python
def _connect(self) -> sqlite3.Connection:
    """Create a connection with pragmas enabled."""
    conn = sqlite3.connect(self.db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

Replace all `sqlite3.connect(self.db_path)` calls with `self._connect()`.

**Acceptance criteria:**
- Inserting a `risk_event` with a non-existent `session_id` raises `IntegrityError`
- Add a unit test confirming foreign key enforcement

### 4.2 Track Agent Completion / Time-Decay for `agent_fanout` (H6)

**Problem:** `active_agents` only increments on `Task` tool calls, never decrements. Even with C1 fix, agent count grows monotonically.

**Approach:** Store agent spawn timestamps and use a **time-decay model** — agents are considered "active" for 5 minutes after spawn (configurable). This avoids needing to detect agent completion events, which Claude Code doesn't reliably expose.

**Files to modify:**
- `scripts/rtfi/scoring/engine.py` — Change `active_agents: int` to `agent_spawn_timestamps: list[datetime]`, add `active_agents` as a computed property
- `scripts/rtfi/models/events.py` — No changes needed (RiskScore.calculate still takes `active_agents: int`)

**Implementation steps:**

1. Replace `active_agents: int = 0` with `agent_spawn_timestamps: list[datetime] = field(default_factory=list)` in `SessionState`
2. Add property:
   ```python
   @property
   def active_agents(self) -> int:
       """Count agents spawned within the last 5 minutes."""
       now = datetime.now()
       five_min_ago = now.timestamp() - 300
       return len([t for t in self.agent_spawn_timestamps if t.timestamp() > five_min_ago])
   ```
3. Change `state.active_agents += 1` to `state.agent_spawn_timestamps.append(event.timestamp)`
4. Update `to_dict()` / `from_dict()` (from C1) to serialize `agent_spawn_timestamps`
5. Add pruning: remove timestamps older than 10 minutes in `prune_old_timestamps()`

**Acceptance criteria:**
- `active_agents` returns 0 when no agents have been spawned in the last 5 minutes
- `active_agents` returns correct count of recently-spawned agents
- Agent count naturally decays over time without explicit completion events

### 4.3 Add Project Isolation to Database (M1)

**Problem:** All sessions go into one table regardless of which project they belong to. Multi-project users can't filter.

**Files to modify:**
- `scripts/rtfi/storage/database.py` — Add `project_dir` column to `sessions` schema
- `scripts/rtfi/models/events.py` — Add `project_dir: str | None = None` to `Session` model
- `scripts/hook_handler.py` — Populate `project_dir` from `$CLAUDE_PROJECT_DIR`
- `scripts/rtfi_cli.py` — Add `--project` filter to `sessions` and `risky` commands

**Schema migration:**
```sql
ALTER TABLE sessions ADD COLUMN project_dir TEXT;
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_dir);
```

**Acceptance criteria:**
- Sessions are tagged with the project directory
- CLI commands can filter by `--project`
- Existing sessions without `project_dir` still display correctly (column is nullable)

### 4.4 Use Timezone-Aware Datetimes (M2)

**Problem:** `datetime.now()` returns naive datetimes. Comparing timestamps across timezones or daylight saving transitions will produce incorrect results.

**Files to modify:**
- `scripts/rtfi/models/events.py` — Change `datetime.now` to `datetime.now(timezone.utc)`
- `scripts/rtfi/scoring/engine.py` — Change all `datetime.now()` to `datetime.now(timezone.utc)`
- `scripts/rtfi/storage/database.py` — Ensure `fromisoformat()` handles timezone suffixes

**Implementation steps:**
1. Add `from datetime import timezone` to all affected files
2. Replace `datetime.now()` with `datetime.now(timezone.utc)` in:
   - `events.py` line 81 (RiskEvent default), line 93 (Session default)
   - `engine.py` lines 25, 34, 60 (tools_per_minute, prune, end_session)
   - `database.py` line 163 (purge_old_sessions)
3. Store ISO 8601 with `+00:00` suffix in database
4. Handle backward compatibility: `fromisoformat()` in Python 3.10 doesn't parse `Z` suffix, so use `+00:00`

**Acceptance criteria:**
- All timestamps in the database include timezone information
- Existing naive timestamps are handled gracefully (treated as UTC)

### 4.5 Optimize Session Prefix Lookup (M3)

**Problem:** `cmd_show` in `rtfi_cli.py` loads up to 1000 sessions into memory to do a prefix match.

**Files to modify:**
- `scripts/rtfi/storage/database.py` — Add `find_session_by_prefix(prefix: str)` method
- `scripts/rtfi_cli.py` — Use new method instead of loading all sessions

**Implementation:**

```python
def find_session_by_prefix(self, prefix: str) -> Session | None:
    """Find a session by ID prefix."""
    with self._connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sessions WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
        if row:
            return self._row_to_session(row)
    return None
```

**Acceptance criteria:**
- `cmd_show("abc")` finds sessions starting with "abc" without loading all sessions
- Works with both full IDs and short prefixes

### 4.6 Connection Caching (L1)

**Problem:** Each `Database` method opens a new `sqlite3.connect()` call. Within a single hook invocation, this means 2–4 separate connections.

**Files to modify:**
- `scripts/rtfi/storage/database.py` — Cache connection for the lifetime of the `Database` instance

**Implementation:**

```python
class Database:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
```

Replace all `with sqlite3.connect(self.db_path) as conn:` with `conn = self._connect()` and add explicit `conn.commit()` calls. Add `db.close()` at the end of `hook_handler.py`'s `main()`.

**Acceptance criteria:**
- Only one SQLite connection per hook invocation
- Connection is properly closed at the end
- All existing tests still pass

### 4.7 Repurpose PostToolUse Hook (L2)

**Problem:** The `PostToolUse` hook currently does minimal work and returns `{"continue": True}`. With H6's time-decay model, explicit agent completion tracking is less critical, but PostToolUse can still add value.

**Approach:** Use PostToolUse to capture tool execution results — specifically, detect when a `Task` tool has completed (the PostToolUse event after a `Task` PreToolUse) and optionally track context token changes from tool output.

**Files to modify:**
- `scripts/hook_handler.py` — Enhance `handle_post_tool_use` to emit a `RESPONSE` event with updated context tokens

**Acceptance criteria:**
- PostToolUse tracks context token updates from tool output
- No performance regression (PostToolUse should still complete quickly)

### Phase 4 Verification

```bash
# Test foreign key enforcement
python3 -c "
from scripts.rtfi.storage.database import Database
import tempfile, sqlite3
db = Database(db_path=tempfile.mktemp(suffix='.db'))
try:
    # This should raise IntegrityError with foreign keys ON
    conn = db._connect()
    conn.execute(\"INSERT INTO risk_events (session_id, timestamp, event_type) VALUES ('nonexistent', '2026-01-01', 'tool_call')\")
    conn.commit()
    print('FAIL: Foreign key not enforced')
except sqlite3.IntegrityError:
    print('PASS: Foreign key enforced')
"

# Test agent decay
make test  # Integration tests should cover agent decay behavior

# Test prefix lookup
python3 scripts/rtfi_cli.py show abc  # Should use SQL LIKE, not load all sessions
```

---

## Phase 5 — Enterprise Polish

> **Goal:** Production-grade observability, tamper-proof audit logging, proper configuration management, and customizable scoring parameters.

### 5.1 Structured Logging (M4)

**Problem:** Log messages are free-form text strings, making them difficult to parse, aggregate, and search in centralized logging systems.

**Approach:** Switch to JSON-formatted log entries using Python's built-in `logging` module with a custom JSON formatter.

**Files to modify:**
- `scripts/hook_handler.py` — Add `JsonFormatter` class, apply to all handlers

**Implementation:**

```python
import json as json_module

class JsonFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "session_id"):
            log_entry["session_id"] = record.session_id
        if hasattr(record, "hook_type"):
            log_entry["hook_type"] = record.hook_type
        return json_module.dumps(log_entry)
```

**Acceptance criteria:**
- All log entries are valid JSON
- Each entry includes timestamp, level, message, and session context
- Logs are parseable by standard tools (`jq`, Datadog, Splunk, etc.)

### 5.2 Audit Log Integrity with HMAC Signatures (M5)

**Problem:** Audit log entries can be silently modified. For compliance-sensitive environments, logs need integrity verification.

**Approach:** Add an HMAC-SHA256 signature to each audit log entry using a machine-specific key. This doesn't prevent deletion but detects modification.

**Files to modify:**
- `scripts/hook_handler.py` — Add HMAC signing to audit log entries, add verification utility

**Implementation:**

```python
import hmac, hashlib

def _get_audit_key() -> bytes:
    """Get or create a machine-specific audit signing key."""
    key_path = LOG_DIR / ".audit_key"
    if key_path.exists():
        return key_path.read_bytes()
    key = os.urandom(32)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key

def sign_audit_entry(entry: str) -> str:
    """Add HMAC signature to an audit log entry."""
    key = _get_audit_key()
    sig = hmac.new(key, entry.encode(), hashlib.sha256).hexdigest()
    return f"{entry} [sig:{sig}]"
```

**Acceptance criteria:**
- Every audit log entry includes `[sig:<hex>]` suffix
- A verification function can validate all entries in the audit log
- Key is stored with `0o600` permissions
- Tampered entries fail verification

### 5.3 Proper Configuration File Format (M6)

**Problem:** The settings loading function (`load_settings`) parses a fragile markdown-like format. It's error-prone and undocumented.

**Approach:** Switch to TOML format (Python 3.11+ has `tomllib` built-in; for 3.10 use `tomli` or stick with a simple `.env`-style format). Given the 3.10 target, use a simple INI-style or `.env` file read with standard library.

**Files to modify:**
- `scripts/hook_handler.py` — Update `load_settings()` to read from `~/.rtfi/config.toml` or `~/.rtfi/config.env`

**Implementation (using simple key=value .env format):**

```python
def load_settings() -> dict:
    """Load settings from config file and environment variables."""
    config = {
        "threshold": 70.0,
        "retention_days": 90,
        "action_mode": "alert",
        "log_level": "INFO",
    }

    config_path = LOG_DIR / "config.env"
    if config_path.exists():
        for line in config_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                config[key.strip().lower()] = value.strip()

    # Environment variables override config file
    for key, env_var, parser, default in [
        ("threshold", "RTFI_THRESHOLD", float, 70.0),
        ("retention_days", "RTFI_RETENTION_DAYS", int, 90),
        ("action_mode", "RTFI_ACTION_MODE", str, "alert"),
    ]:
        env_val = os.environ.get(env_var)
        if env_val:
            try:
                config[key] = parser(env_val)
            except (ValueError, TypeError):
                logger.warning(f"Invalid {env_var}={env_val!r}, using default {default}")
                config[key] = default

    return config
```

**Acceptance criteria:**
- Settings can be configured via `~/.rtfi/config.env` file
- Environment variables override file settings
- Invalid values fall back to defaults with a warning
- Example config file is included in `scripts/setup.sh`

### 5.4 Optional Metrics Export (L3)

**Problem:** No way to integrate RTFI data with enterprise monitoring systems (Prometheus, Grafana, Datadog, etc.).

**Approach:** Add an optional StatsD-compatible metrics export. When `RTFI_STATSD_HOST` is set, emit metrics on each hook invocation.

**Files to modify:**
- `scripts/hook_handler.py` — Add optional StatsD client
- `scripts/rtfi/metrics.py` — New module (lightweight, no external dependencies)

**Metrics to emit:**
- `rtfi.risk_score` (gauge) — Current risk score
- `rtfi.tool_calls` (counter) — Tool calls per session
- `rtfi.agent_spawns` (counter) — Agent spawns per session
- `rtfi.threshold_exceeded` (counter) — Threshold breach events
- `rtfi.hook_latency_ms` (timer) — Hook execution time

**Implementation (UDP-based StatsD, no dependencies):**

```python
import socket

class StatsD:
    """Minimal StatsD client using UDP (no dependencies)."""

    def __init__(self, host: str = "localhost", port: int = 8125, prefix: str = "rtfi"):
        self.host = host
        self.port = port
        self.prefix = prefix
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def gauge(self, name: str, value: float) -> None:
        self._send(f"{self.prefix}.{name}:{value}|g")

    def incr(self, name: str, count: int = 1) -> None:
        self._send(f"{self.prefix}.{name}:{count}|c")

    def timing(self, name: str, ms: float) -> None:
        self._send(f"{self.prefix}.{name}:{ms}|ms")

    def _send(self, data: str) -> None:
        try:
            self._sock.sendto(data.encode(), (self.host, self.port))
        except OSError:
            pass  # Fire-and-forget — never block hook execution
```

**Acceptance criteria:**
- Metrics are emitted only when `RTFI_STATSD_HOST` is set
- No external dependencies required
- Metric emission never blocks or slows hook execution
- Metrics are visible in StatsD-compatible systems

### 5.5 First-Run Setup Command (L5)

**Problem:** No guided setup experience for new users. They must manually install dependencies, understand the file structure, and troubleshoot issues.

**Approach:** Add a `/rtfi:setup` command that validates the environment, installs dependencies, creates the config file, and runs a health check.

**Files to create:**
- `commands/setup.md` — New command definition

**Command definition:**

```markdown
---
name: setup
description: First-run setup and environment validation for RTFI
arguments: []
---

Run the RTFI setup wizard:

$CLAUDE_PLUGIN_ROOT/scripts/rtfi_cli.py setup
```

**Files to modify:**
- `scripts/rtfi_cli.py` — Add `setup` subcommand

**Setup flow:**
1. Check Python version (≥3.10)
2. Check/install pydantic
3. Create `~/.rtfi/` directory with correct permissions
4. Create default `~/.rtfi/config.env` if it doesn't exist
5. Initialize database
6. Run health check
7. Print summary and next steps

**Acceptance criteria:**
- `/rtfi:setup` works on a fresh machine with Python 3.10+
- Creates all necessary directories and files
- Provides clear success/failure output
- Is idempotent (safe to run multiple times)

### 5.6 Configurable Normalization Thresholds (L6)

**Problem:** The risk score normalization thresholds are hardcoded: 128k tokens, 5 agents, 10 steps, 20 tools/min. Different projects have different profiles — a CI automation project might legitimately use 20+ agents, while a code review project might use none.

**Approach:** Make thresholds configurable via the config file (M6) and environment variables, with sensible defaults.

**Files to modify:**
- `scripts/rtfi/models/events.py` — Add optional parameters to `RiskScore.calculate()`
- `scripts/hook_handler.py` — Pass configured thresholds to `RiskScore.calculate()` via the engine

**Implementation:**

```python
# In RiskScore.calculate(), add optional normalization parameters:
@classmethod
def calculate(
    cls,
    tokens: int,
    active_agents: int,
    steps_since_confirm: int,
    tools_per_minute: float,
    threshold: float = 70.0,
    max_tokens: int = 128000,
    max_agents: int = 5,
    max_steps: int = 10,
    max_tools_per_min: float = 20.0,
) -> "RiskScore":
    factors = {
        "context_length": min(1.0, tokens / max_tokens),
        "agent_fanout": min(1.0, active_agents / max_agents),
        "autonomy_depth": min(1.0, steps_since_confirm / max_steps),
        "decision_velocity": min(1.0, tools_per_minute / max_tools_per_min),
    }
    # ... rest unchanged
```

**Config file entries:**
```env
# Normalization thresholds (defaults shown)
MAX_TOKENS=128000
MAX_AGENTS=5
MAX_STEPS=10
MAX_TOOLS_PER_MIN=20
```

**Acceptance criteria:**
- Thresholds can be set in `~/.rtfi/config.env` and via environment variables
- Default values match current behavior (no regression)
- Invalid values fall back to defaults with a warning
- `RiskScore.calculate()` signature is backward compatible

### Phase 5 Verification

```bash
# Test structured logging
python3 scripts/hook_handler.py session_start <<< '{}'
cat ~/.rtfi/rtfi.log | python3 -m json.tool  # Should be valid JSON

# Test audit log integrity
python3 scripts/hook_handler.py session_start <<< '{}'
grep '\[sig:' ~/.rtfi/audit.log  # Should have HMAC signatures

# Test config file
cat ~/.rtfi/config.env  # Should exist with documented format

# Test setup command
python3 scripts/rtfi_cli.py setup

# Test custom thresholds
RTFI_MAX_AGENTS=10 python3 scripts/hook_handler.py pre_tool_use <<< '{"tool_name": "Read"}'
```

---

## Item Cross-Reference

Every item from [ANALYSIS.md](./ANALYSIS.md) Section 7 is covered:

| Item | Description | Phase | Section |
|------|-------------|-------|---------|
| C1 | Risk scores reset every hook call | 1 | 1.1 |
| C2 | `handle_stop` never finalizes sessions | 1 | 1.2 |
| C3 | Python 3.10 vs 3.11 requirement | 2 | 2.1 |
| C4 | Malformed env vars crash at module load | 1 | 1.3 |
| H1 | Tests validate non-production behavior | 3 | 3.1 |
| H2 | Version inconsistency | 2 | 2.2 |
| H3 | Dead code: `cli/main.py` | 2 | 2.3 |
| H4 | No foreign key enforcement | 4 | 4.1 |
| H5 | `pip install --user` in hook execution | 2 | 2.4 |
| H6 | `agent_fanout` never decrements | 4 | 4.2 |
| H7 | Duplicate `marketplace.json` | 2 | 2.5 |
| H8 | No log rotation | 2 | 2.6 |
| M1 | No project isolation in database | 4 | 4.3 |
| M2 | Naive datetimes | 4 | 4.4 |
| M3 | O(n) session prefix lookup | 4 | 4.5 |
| M4 | No structured logging | 5 | 5.1 |
| M5 | Audit log integrity | 5 | 5.2 |
| M6 | Settings file parsing is fragile | 5 | 5.3 |
| M7 | No CI/CD pipeline | 3 | 3.2 |
| M8 | Restrict file permissions | 2 | 2.7 |
| M9 | No stdin size limit | 2 | 2.8 |
| L1 | No connection pooling | 4 | 4.6 |
| L2 | PostToolUse hook adds little value | 4 | 4.7 |
| L3 | No metrics export | 5 | 5.4 |
| L4 | No Makefile/taskrunner | 3 | 3.3 |
| L5 | No first-run experience | 5 | 5.5 |
| L6 | Configurable normalization thresholds | 5 | 5.6 |

---

## Release Strategy

| Phase Complete | Version | Notes |
|----------------|---------|-------|
| Phase 1 | **0.2.0** | Core risk scoring functional — major milestone |
| Phase 2 | **0.2.1** | Cleanup and stability — patch release |
| Phase 3 | **0.2.2** | Testing and CI — patch release |
| Phase 4 | **0.3.0** | Improved scoring and data model — minor release |
| Phase 5 | **1.0.0** | Enterprise-ready — major release |

Each phase should be a separate PR (or set of PRs) with its own verification checklist. Phase 1 is the critical path — everything else can be reordered or deferred based on user feedback.
