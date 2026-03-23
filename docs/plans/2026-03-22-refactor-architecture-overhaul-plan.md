---
title: RTFI Architecture Overhaul
type: refactor
status: completed
date: 2026-03-22
---

# RTFI Architecture Overhaul

## Overview

End-to-end overhaul of the RTFI Claude Code plugin: fix 15 correctness bugs, consolidate Python from 7+ modules into 2 core files, rebuild the dashboard from the POC with Chart.js and a JSON API, and rewrite the test suite. All enterprise features (StatsD, HMAC audit trails, layered config) are preserved.

## Problem Statement

The RTFI plugin has accumulated significant technical debt since its initial release:

1. **Correctness bugs**: Checkpoint events never fire in production (autonomy depth only grows), `save_session()` silently clears `session_state`, statusline shows peak score not live score, thresholds are hardcoded in 3 places, session IDs fragment across hook invocations
2. **Over-abstraction**: 7+ Python modules for ~1,500 lines of domain logic — `models/events.py`, `scoring/engine.py`, `storage/database.py`, `metrics.py`, `cli/main.py`, plus dead Click CLI code
3. **Dashboard**: Current HTMX-based dashboard is functional but lacks charts, analytics, and the design quality of the POC (`rtfi_analytics_dashboard.html`)
4. **Inconsistency**: Three different risk level taxonomies across statusline, dashboard, and CLI; version defined in 3 places with different values

## Proposed Solution

**Approach B: Module Consolidation + Dashboard Rebuild** (chosen via brainstorm 2026-03-22).

Flatten Python abstractions without removing capabilities. Rebuild dashboard from the POC forward with Chart.js and a JSON API. Fix all correctness bugs as part of the refactor.

## Technical Approach

### Architecture

**Target file structure:**

```
scripts/
  hook_handler.py          # Hook entry point: routing, I/O, validation, HMAC audit
  rtfi_core.py             # Domain: models, scoring engine, database, state, config, StatsD
  rtfi_cli.py              # CLI: argument parsing, output formatting (imports rtfi_core)
  rtfi_dashboard.py         # JSON API server + static HTML serving (rebuilt)
  rtfi_statusline.py        # Statusline helper (fixed: live score, config-aware)
  run_hook.sh               # Bash shim (unchanged)
  setup.sh                  # Setup script (unchanged)
  demo_scenario.py          # Demo driver (updated imports)
  demo_compliance_check.py  # Compliance auditor (updated imports)
tests/
  conftest.py               # sys.path setup (updated)
  test_core.py              # Unit tests for rtfi_core.py (rewritten)
  test_hook_handler.py      # Unit tests for hook routing (rewritten)
  test_integration.py       # Integration tests via subprocess (updated paths)
  test_dashboard.py          # Dashboard API tests (new)
commands/
  checkpoint.md             # NEW: /rtfi:checkpoint manual command
```

**Public API of `rtfi_core.py` (`__all__`):**

```python
__all__ = [
    # Models
    "EventType", "SessionOutcome", "RiskScore", "RiskEvent", "Session",
    # Engine
    "RiskEngine", "SessionState",
    # Database
    "Database",
    # Config
    "load_settings", "DEFAULT_SETTINGS",
    # Metrics
    "get_statsd",
    # Version
    "__version__",
]
```

**Internal section order** (avoids forward references):

1. Constants, version, StatsD client
2. Enums: `EventType`, `SessionOutcome`
3. Pydantic models: `RiskScore`, `RiskEvent`, `Session`
4. Database: `SCHEMA`, `Database` class
5. Engine: `SessionState`, `RiskEngine`
6. Config: `load_settings()`, `DEFAULT_SETTINGS`
7. `get_statsd()` factory

### Implementation Phases

#### Phase 1: Core Consolidation & Bug Fixes

Consolidate modules, fix all correctness bugs, establish the new public API.

**1.1 Create `rtfi_core.py`**

- Merge contents of `scripts/rtfi/models/events.py`, `scripts/rtfi/scoring/engine.py`, `scripts/rtfi/storage/database.py`, `scripts/rtfi/metrics.py` into `scripts/rtfi_core.py` following the section order above
- Define `__all__` for public API surface
- Update `SCHEMA` constant to include `session_state TEXT` and `project_dir TEXT` columns directly (no ALTER TABLE migration needed for fresh installs; keep migration for existing DBs)
- Single version source of truth: `__version__ = "1.2.0"` in `rtfi_core.py`, referenced by all other files
- Make `AGENT_DECAY_SECONDS` configurable: accept it as a parameter in `SessionState` constructor (default 300), pass from `load_settings()` config

**1.2 Fix `save_session()` data loss (H1)**

Fix approach: **Option A — INSERT OR IGNORE + UPDATE**

```python
# scripts/rtfi_core.py
def save_session(self, session: Session, session_state: dict | None = None) -> None:
    """Save session. Preserves session_state column on updates."""
    with self._conn() as conn:
        # Insert if new (all columns including session_state)
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, started_at, ..., session_state) VALUES (?, ?, ..., ?)",
            (session.id, ..., json.dumps(session_state) if session_state else None),
        )
        # Update if existing (never touches session_state unless explicitly provided)
        cols = "started_at=?, ended_at=?, ..., outcome=?, project_dir=?"
        params = [session.started_at.isoformat(), ...]
        if session_state is not None:
            cols += ", session_state=?"
            params.append(json.dumps(session_state))
        conn.execute(f"UPDATE sessions SET {cols} WHERE id=?", params + [session.id])
```

This eliminates the ordering hazard entirely. `save_session_state()` can remain as a convenience for state-only updates.

**1.3 Fix `handle_stop()` session_state preservation**

After fixing H1, `handle_stop()` preserves session_state automatically (save_session no longer wipes it). Additionally, call `_persist_state()` before finalizing to capture the final state snapshot for post-session analytics.

**1.4 Fix session ID propagation (H2)**

- Write `RTFI_SESSION_ID=uuid` to CLAUDE_ENV_FILE (no `export` prefix — dotenv format is more universally parseable)
- Also write to `~/.rtfi/current_session` as a fallback for shells that don't inherit env
- Add fallback session lookup: when `RTFI_SESSION_ID` is missing, query `SELECT id FROM sessions WHERE project_dir = ? AND outcome = 'in_progress' AND started_at > datetime('now', '-2 hours') ORDER BY started_at DESC LIMIT 1`
- Audit fallback usage with a `SESSION_FALLBACK_LOOKUP` audit event

**1.5 Add checkpoint detection (H3)**

Auto-detection approach: detect tool names that indicate user interaction. Since Claude Code tool names vary, use a configurable allowlist:

```python
CHECKPOINT_TOOLS = {"AskUserQuestion", "AskMultipleChoice"}  # default
# Configurable via RTFI_CHECKPOINT_TOOLS="AskUserQuestion,AskMultipleChoice,CustomTool"
```

When `tool_name in checkpoint_tools`, emit `EventType.CHECKPOINT` instead of `EventType.TOOL_CALL`. This resets `steps_since_confirm = 0`.

Add `/rtfi:checkpoint` command (`commands/checkpoint.md`): calls `rtfi_cli.py checkpoint` which writes a CHECKPOINT event to the DB for the current session (reads `RTFI_SESSION_ID` from env or `~/.rtfi/current_session`) and updates `session_state.steps_since_confirm = 0`.

**1.6 Fix statusline (H4)**

- Replace `peak_risk_score` query with live score calculation from `session_state` JSON
- Read config from `~/.rtfi/config.env` for normalization ceilings (max_tokens, max_agents, etc.)
- Remove dead `~/.rtfi/current_session` fallback (now live — written by session_start)
- Output both live and peak: `{"score": 42, "peak": 67, "level": "ELEVATED"}`

**1.7 Unify risk level taxonomy**

Canonical levels used by ALL components (statusline, dashboard, CLI, hook warnings):

| Score Range | Level | Color |
|---|---|---|
| 0-29 | NORMAL | green |
| 30-69 | ELEVATED | amber |
| 70-100 | HIGH RISK | red |

Threshold for alerts/blocks remains configurable (default 70). The taxonomy above is for display only.

**1.8 Fix hardcoded thresholds**

- `rtfi_dashboard.py`: read threshold from config via `load_settings()`
- `database.py` → `rtfi_core.py`: add `threshold` parameter to `get_stats()`, pass from caller
- `rtfi_statusline.py`: read threshold from `~/.rtfi/config.env`

**1.9 Fix `AGENT_DECAY_SECONDS` dead config**

- Add `RTFI_AGENT_DECAY_SECONDS` to `load_settings()` env overrides
- Pass to `RiskEngine` constructor, which passes to `SessionState`
- `SessionState.active_agents` property uses instance attribute instead of module constant

**1.10 Fix audit key race condition**

Replace `key_path.write_bytes(key)` + `key_path.chmod(0o600)` with:

```python
fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, key)
os.close(fd)
```

**1.11 Clean up**

- Delete `scripts/rtfi/` subpackage entirely (all code now in `rtfi_core.py`)
- Delete `scripts/rtfi/cli/main.py` (dead Click CLI)
- Delete or update `HANDOFF.md` (stale — describes dashboard as "not yet built")
- Update `rtfi_cli.py` imports to use `rtfi_core`
- Update `demo_scenario.py` and `demo_compliance_check.py` imports
- Sync version: `pyproject.toml` → `1.2.0`, `plugin.json` → `1.2.0`

**Deliverables:**
- `scripts/rtfi_core.py` — consolidated domain module
- Updated `scripts/hook_handler.py` — new imports, H1-H4 fixes, checkpoint detection
- Updated `scripts/rtfi_cli.py` — new imports + `checkpoint` subcommand
- Updated `scripts/rtfi_statusline.py` — live score, config-aware
- New `commands/checkpoint.md` — manual checkpoint command
- Deleted `scripts/rtfi/` directory

**Success criteria:**
- All existing integration tests pass with updated imports
- `save_session()` preserves `session_state` column (regression test)
- Checkpoint resets `steps_since_confirm` (unit test)
- Statusline score matches hook handler score under same config
- Single version source of truth in `rtfi_core.py`

---

#### Phase 2: Dashboard Rebuild

Replace the HTMX fragment server with a JSON API + single-page HTML dashboard based on the POC design.

**2.1 JSON API Server**

Rebuild `scripts/rtfi_dashboard.py` as a clean JSON API server using `http.server` + `socketserver.ThreadingTCPServer` (same stdlib approach, no new deps).

**Routes:**

| Method | Path | Response | Description |
|---|---|---|---|
| GET | `/` | HTML | Serve the dashboard HTML file |
| GET | `/api/config` | JSON | `{threshold, max_tokens, max_agents, max_steps, max_tools_per_min, agent_decay_seconds, action_mode, version}` |
| GET | `/api/live` | JSON | `{score: {total, context_length, agent_fanout, autonomy_depth, decision_velocity, threshold_exceeded}, session: {id, started_at, outcome, peak_risk_score, total_tool_calls, total_agent_spawns}, is_live}` or `{score: null, session: null, is_live: false}` when no session |
| GET | `/api/sessions?limit=50&offset=0` | JSON | `{sessions: [...], total: int}` — paginated, sorted by started_at DESC |
| GET | `/api/session/{id}` | JSON | `{session: {...}, events: [...], state: {...}}` — full session detail with all events |
| GET | `/api/stats` | JSON | `{total_sessions, high_risk_sessions, total_tool_calls, total_agent_spawns, avg_risk_score}` — uses configured threshold |
| GET | `/api/chart-data` | JSON | `{daily: [...], tool_usage: [...], risk_distribution: [...]}` — pre-aggregated chart data |

Error response format: `{"error": "message", "status": 404}`

**2.2 Dashboard HTML**

Single HTML file based on the POC (`rtfi_analytics_dashboard.html`) design system:

- 12-column bento grid layout with design tokens from POC
- Chart.js 4.5.1 with SRI hash (`integrity="sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ"`)
- HTMX removed — replaced with `fetch()` polling

**Charts (full analytics suite):**

1. **Daily Session Volume & Risk Trend** — mixed bar+line, dual Y-axes (sessions count + avg risk)
2. **Session Outcomes** — doughnut chart (completed vs. in_progress vs. abandoned)
3. **Peak Risk Distribution** — histogram, 10 bins, color-coded by severity
4. **Tool Usage vs. Risk Level** — mixed bar+line (top 8 tools by call count + avg risk per tool)
5. **Risk Factor Profile** — radar chart (4 factors with weights in labels)

**Live monitoring mode:**

- Live gauge with ring indicator (same design as current, improved with POC tokens)
- Factor breakdown bars (4 bars with weights)
- Polls `/api/live` every 2 seconds when session is `in_progress`
- Green dot indicator for active sessions

**Post-session analytics mode:**

- Session table with sortable columns, risk badges, outcome badges
- Click session row to drill into detail view with event timeline
- Charts update based on time range filter (7d, 30d, 90d, all)
- Modal drill-down system from POC (`drillKPI`, `sessionModal`)

**Mode transition:**

- If most recent session is `in_progress` → live mode (gauge prominent, charts secondary)
- If most recent session is completed → analytics mode (charts prominent, gauge shows last session)
- User can toggle between modes via tab control

**Staleness detection:** Sessions `in_progress` for more than 2 hours are displayed as "Abandoned" in the dashboard (configurable via `RTFI_STALE_SESSION_HOURS`, default 2).

**2.3 SRI Hashes**

- Chart.js 4.5.1: `integrity="sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ"` (from POC)
- No HTMX CDN dependency (removed)

**Deliverables:**
- Rebuilt `scripts/rtfi_dashboard.py` — JSON API server + static file serving
- New `scripts/dashboard.html` — single-page dashboard with Chart.js
- Updated `commands/dashboard.md` — points to new server

**Success criteria:**
- All 7 API endpoints return valid JSON
- `/api/config` returns actual configured threshold (not hardcoded 70)
- Dashboard loads in browser with all 5 charts rendering
- Live gauge updates every 2s during active session
- Session drill-down shows full event timeline

---

#### Phase 3: Test Suite Rewrite

Clean-slate test suite matching the new 2-file structure.

**3.1 `tests/test_core.py`** (replaces `test_storage.py` + `test_scoring.py`)

```python
# Models
test_risk_score_calculate_all_zero()
test_risk_score_calculate_all_max()
test_risk_score_threshold_boundary()
test_risk_score_custom_ceilings()

# Database
test_save_and_get_session()
test_save_session_preserves_session_state()          # AC-1: regression for H1
test_save_session_state_roundtrip()
test_get_recent_sessions_ordering()
test_get_high_risk_sessions_uses_threshold_param()   # AC-6
test_get_stats_uses_threshold_param()
test_purge_old_sessions()
test_schema_includes_all_columns()                    # Gap 25

# Engine
test_process_tool_call_increments_state()
test_process_agent_spawn_adds_timestamp()
test_process_checkpoint_resets_autonomy()              # AC-8
test_session_state_to_dict_from_dict_roundtrip()
test_active_agents_decay_window()                      # AC-3
test_active_agents_custom_decay_seconds()
test_prune_old_timestamps()
test_peak_risk_score_tracking()

# Config
test_load_settings_defaults()
test_load_settings_env_override()
test_load_settings_config_file()
test_load_settings_agent_decay_seconds()               # AC-3
```

**3.2 `tests/test_hook_handler.py`** (rewritten)

```python
test_validate_hook_data_valid()
test_validate_hook_data_missing_fields()
test_validate_hook_data_oversized_input()
test_handle_session_start_creates_session()
test_handle_pre_tool_use_scores_event()
test_handle_pre_tool_use_agent_spawn()
test_handle_pre_tool_use_checkpoint_detection()        # H3
test_handle_post_tool_use_updates_tokens()
test_handle_stop_preserves_session_state()             # AC-2
test_handle_stop_finalizes_session()
test_action_mode_block_response_format()               # Gap 22
test_action_mode_confirm_response_format()             # Gap 22
test_env_file_path_validation()
test_env_file_dotenv_format()                          # H2: no export prefix
```

Eliminate `importlib.reload()` pattern. Use subprocess invocation for env-var-dependent tests (like `test_integration.py`).

**3.3 `tests/test_integration.py`** (updated)

- Use absolute paths: `Path(__file__).parent.parent / "scripts" / "hook_handler.py"`
- Add: `test_session_state_survives_across_hooks()` — 3 sequential subprocess calls, verify session_state is non-NULL after each
- Add: `test_checkpoint_resets_autonomy_depth()` — spawn checkpoint tool, verify score drops
- Add: `test_hook_completes_within_budget()` — timing assertion < 500ms (AC-10)

**3.4 `tests/test_dashboard.py`** (new)

```python
test_api_config_returns_configured_threshold()         # AC-6
test_api_live_active_session()
test_api_live_no_session()
test_api_sessions_pagination()
test_api_session_detail_includes_events()
test_api_stats_uses_threshold()
test_api_chart_data_structure()
test_static_html_served()
```

**Deliverables:**
- Rewritten `tests/test_core.py`
- Rewritten `tests/test_hook_handler.py`
- Updated `tests/test_integration.py`
- New `tests/test_dashboard.py`
- Updated `tests/conftest.py`

**Success criteria:**
- All tests pass on Python 3.10, 3.11, 3.12
- Coverage of all 10 acceptance criteria (AC-1 through AC-10)
- No `importlib.reload()` usage
- Integration tests use absolute paths

## Acceptance Criteria

### Functional Requirements

- [x] **AC-1**: `save_session()` on an existing session preserves the `session_state` column value
- [x] **AC-2**: `session_state` is non-NULL after `handle_stop()` for a session with prior tool calls
- [x] **AC-3**: `RTFI_AGENT_DECAY_SECONDS` config value changes the decay window used during scoring
- [x] **AC-4**: Statusline live score matches hook handler score under the same configuration
- [x] **AC-5**: Risk level labels are identical across statusline, dashboard, and CLI (NORMAL/ELEVATED/HIGH RISK)
- [x] **AC-6**: Dashboard `/api/config` returns the actual configured threshold (not hardcoded 70)
- [x] **AC-7**: An `in_progress` session older than 2 hours is displayed as "Abandoned" in the dashboard
- [x] **AC-8**: `/rtfi:checkpoint` resets `steps_since_confirm` to 0 and is reflected in the next hook call's score
- [x] **AC-9**: HMAC audit signatures can be verified for all rotated log files (not just current file)
- [ ] **AC-10**: Hook handler completes within 500ms on a cold start (no prior DB connection)

### Non-Functional Requirements

- [x] All enterprise features preserved: StatsD metrics, HMAC audit trail, layered config
- [x] No new runtime dependencies (Chart.js is CDN-loaded, not bundled)
- [x] Backward compatible with existing `~/.rtfi/rtfi.db` data
- [x] SRI hash on all CDN script tags
- [x] Audit key created with 0o600 permissions atomically (no race window)

### Quality Gates

- [x] All tests pass on Python 3.14 (63/63)
- [ ] `ruff check` and `ruff format --check` pass
- [ ] `mypy` passes with `disallow_untyped_defs = true`
- [x] No direct access to `engine._sessions` (use public API)
- [x] Single version source of truth in `rtfi_core.py`

## Dependencies & Prerequisites

- **No external dependencies**: All work is local to the RTFI plugin
- **Phase ordering**: Phase 1 (core + bugs) must complete before Phase 2 (dashboard) because the dashboard imports from `rtfi_core.py`. Phase 3 (tests) can begin in parallel with Phase 2 for `test_core.py` and `test_hook_handler.py`, but `test_dashboard.py` requires Phase 2.
- **Backward compat**: Existing `~/.rtfi/rtfi.db` must continue to work. The consolidated SCHEMA includes migration logic for DBs that lack `session_state` or `project_dir` columns.

## Risk Analysis & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Module consolidation breaks imports | Medium | High | Run tests after every merge step; integration tests catch subprocess failures |
| `save_session()` fix introduces new bug | Low | Critical | Regression test (AC-1) runs before any other work; test INSERT OR IGNORE + UPDATE edge cases |
| Dashboard rebuild takes longer than expected | Medium | Medium | Phase 2 is independent — existing HTMX dashboard continues to work until replaced |
| Checkpoint auto-detection fires on wrong tools | Medium | Low | Configurable tool allowlist; conservative default; manual `/rtfi:checkpoint` as fallback |
| `CLAUDE_ENV_FILE` format change breaks session continuity | Low | High | Test both `export VAR=val` and `VAR=val` formats; default to more universal format |
| Chart.js CDN unavailable | Low | Low | Dashboard degrades gracefully (gauge + table still work, charts show empty) |

## References & Research

### Internal References

- Brainstorm: `docs/brainstorms/2026-03-22-architecture-audit-brainstorm.md`
- Architecture: `docs/ARCHITECTURE.md` (C4 diagrams, sequence diagrams)
- ADR-0001: Deterministic risk scoring (`docs/adr/0001-deterministic-risk-scoring.md`)
- ADR-0002: Fresh process per hook (`docs/adr/0002-fresh-process-per-hook.md`)
- ADR-0003: Agent decay (`docs/adr/0003-agent-decay.md`)
- ADR-0004: Layered configuration (`docs/adr/0004-layered-configuration.md`)
- ADR-0005: HMAC audit trail (`docs/adr/0005-hmac-audit-trail.md`)
- POC Dashboard: `rtfi_analytics_dashboard.html` (design system source of truth)

### Research Files

- Implementation research: `/tmp/tasks/rtfi-implementation-research.md`
- Learnings: `/tmp/tasks/rtfi-learnings.md`
- SpecFlow gap analysis: `/tmp/tasks/rtfi-specflow.md`
