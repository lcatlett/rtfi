# RTFI Session Context

## Current Task
v1.2.0 architecture overhaul shipped (PR #1). Post-ship diagnosis of instruction displacement problem.

## Key Decisions
- Consolidated 7+ Python modules into single `rtfi_core.py` (aggressive simplification, enterprise features preserved)
- Dashboard rebuilt from HTMX to JSON API + Chart.js single-page app (from POC design)
- Checkpoint auto-detection via configurable tool allowlist + manual `/rtfi:checkpoint` command

## Next Steps
- Merge PR #1 after CI passes
- Update `docs/ARCHITECTURE.md` — still references old module paths
- **New investigation**: Skill prompt displacement of CLAUDE.md instructions (177KB skills vs 10KB CLAUDE.md). How to detect and enforce standing instruction compliance when skills dominate context. Potential new RTFI feature or separate tool.
