# RTFI Architecture Audit & Overhaul

**Date**: 2026-03-22
**Status**: Brainstorm complete

## What We're Building

A comprehensive overhaul of the RTFI plugin that:
1. Fixes 15 identified correctness and quality bugs
2. Consolidates the Python codebase from 7+ modules into 2 core files
3. Rebuilds the dashboard from the POC forward with Chart.js and a JSON API
4. Preserves all enterprise features (StatsD, HMAC audit trails, layered config)

## Why This Approach

**Approach B: Module Consolidation + Dashboard Rebuild** was chosen over surgical fixes (too conservative) and phased overhaul (too slow). The plugin is over-abstracted for its size, and the dashboard needs a fresh start from the POC design rather than incremental HTMX evolution.

Key constraint: enterprise features (StatsD, HMAC, layered config) must stay. The target audience is enterprise teams using Claude Code. Simplification means fewer files and abstractions, not fewer capabilities.

## Key Decisions

### Python Architecture
- **Flatten to 2 files**: `hook_handler.py` (entry point, hook routing, I/O) and `rtfi_core.py` (scoring engine, models, database, state management, config). Delete `scripts/rtfi/` subpackage.
- **Delete dead code**: Remove Click CLI (`scripts/rtfi/cli/main.py`), stale `HANDOFF.md`
- **Fix version mismatch**: Sync `pyproject.toml` to 1.1.0
- **Add public API to engine**: Replace direct `engine._sessions` access with a proper method

### Correctness Bugs
- **Checkpoint detection (critical)**: Auto-detect human confirmation from hook data (AskUserQuestion tool calls, user-interactive tools) AND add `/rtfi:checkpoint` manual command as fallback
- **Session ID fragmentation**: Harden `CLAUDE_ENV_FILE` mechanism; add fallback session lookup by project_dir + recent timestamp when `RTFI_SESSION_ID` is missing
- **save_session() clears state**: Replace `INSERT OR REPLACE` with `INSERT OR IGNORE` + column `UPDATE`s, or create `save_session_with_state()` that wraps both in a transaction
- **Statusline shows peak not live**: Compute live score from `session_state` JSON column
- **Hardcoded thresholds**: Dashboard and `get_stats()` must read from config, not hardcode 70

### Dashboard
- **Architecture**: Single HTML file served by Python server, with JSON API endpoints for data
- **Design**: Start from the POC (`rtfi_analytics_dashboard.html`) bento grid layout
- **Charts**: Chart.js (already in POC with SRI hash) for risk score trends over time
- **Live data**: JSON API replaces HTMX fragments; client-side polling via fetch()
- **Both modes**: Live gauge monitoring during sessions + post-session analytics with session comparison
- **Fix SRI**: Add integrity hash for all CDN resources
- **Fix threshold**: Read from config, not hardcode

### Security
- Add SRI hash to all CDN script tags
- Keep HMAC audit trail, StatsD, file permissions

## Scope

### In Scope
- All 15 identified bugs
- Python module consolidation (7+ files -> 2 core files)
- Dashboard rebuild from POC
- Checkpoint auto-detection + manual command
- CLI consolidation (keep argparse, delete Click)
- Version sync

### Out of Scope
- New features beyond checkpoint detection
- Migration to a different database
- Plugin manifest changes beyond version
- CI/CD pipeline changes
- New scoring factors or formula changes

## Resolved Questions

1. **CLI refactor scope**: Keep `rtfi_cli.py` separate from `rtfi_core.py`. CLI is user-facing I/O (argument parsing, formatting); core is domain logic. Separation enables independent testing and follows single responsibility.
2. **Dashboard server**: Rebuild from scratch. The existing 632-line server is tightly coupled to HTMX fragment generation. A clean JSON API server + static file serving is simpler and matches the new frontend approach.
3. **Test strategy**: Rewrite tests to match the new 2-file structure. Clean-slate test suite rather than incremental import rewiring.
4. **Chart scope**: Full analytics suite — risk score timeline, factor contribution breakdown, cross-session comparison, and aggregate trends. Chart.js is already in the POC with SRI hash, so complexity is manageable.

## Target File Structure (Post-Overhaul)

```
scripts/
  hook_handler.py     # Hook entry point: routing, I/O, validation
  rtfi_core.py        # Domain: scoring, models, database, state, config
  rtfi_cli.py         # CLI: argument parsing, output formatting (imports rtfi_core)
  rtfi_dashboard.py   # JSON API server + static file serving (rebuilt)
  rtfi_statusline.py  # Statusline helper (fixed to show live score)
  run_hook.sh         # Bash shim (unchanged)
  setup.sh            # Setup script (unchanged)
  demo_scenario.py    # Demo driver (updated imports)
  demo_compliance_check.py  # Compliance auditor (updated imports)
tests/
  test_core.py        # Unit tests for rtfi_core.py
  test_hook_handler.py # Unit tests for hook routing
  test_integration.py  # Integration tests (subprocess)
  test_dashboard.py    # Dashboard API tests
```
