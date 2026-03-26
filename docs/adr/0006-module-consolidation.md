# ADR-0006: Module Consolidation into rtfi_core.py

**Status:** Accepted
**Date:** 2026-03-22
**Context:** The RTFI plugin had grown to 7+ Python modules across a nested package structure (`scripts/rtfi/` with `models/`, `scoring/`, `storage/` subpackages). Total domain logic was ~1,500 lines.

## Decision

Consolidate all domain logic (models, scoring engine, database, configuration, metrics) into a single `scripts/rtfi_core.py` module. Keep `hook_handler.py` (entry point), `rtfi_cli.py` (CLI), and `rtfi_dashboard.py` (dashboard server) as separate files that import from `rtfi_core`.

## Rationale

- **Over-abstraction for scale:** 7+ modules with `__init__.py` re-exports for ~1,500 lines of domain logic created more navigational overhead than organizational benefit. The abstractions existed for structure, not for managing actual complexity.
- **Import fragility:** Circular import risks and `sys.path` manipulation in tests (`importlib.reload()` patterns) created brittle test infrastructure.
- **Bug surface area:** The scattered structure hid 15 bugs including a critical session state persistence issue (`INSERT OR REPLACE` silently dropping `session_state`) that was harder to diagnose across module boundaries.
- **Single responsibility preserved:** `rtfi_core.py` contains cohesive domain logic (all classes collaborate on the same data flow). Entry points (`hook_handler`, `cli`, `dashboard`) remain separate, preserving the separation between "what RTFI does" and "how it's invoked."

## Consequences

- Single file to navigate for all domain logic
- `from rtfi_core import Database, RiskEngine, RiskScore, load_settings` replaces deep package imports
- Tests no longer need `importlib.reload()` — direct imports work cleanly
- Future extraction into separate modules remains straightforward if the codebase grows significantly

## Alternatives Considered

- **Keep package structure, fix imports:** Would fix test fragility but not the navigational overhead or bug discoverability issues.
- **Fewer, larger submodules:** (e.g., `models.py`, `engine.py`, `database.py` as flat files) — reasonable middle ground, but at current scale the single-file approach is simpler with no meaningful drawback.
