# RTFI

Real-Time Instruction Compliance Risk Scoring — a Claude Code plugin that scores sessions for the risk of ignoring standing instructions, so operators can intervene before failures compound.

## Environment

- **Python 3.14** pinned via `.mise.toml`. Run `mise install` to provision. Python stdlib only — no third-party runtime dependencies.
- **Dev tools** (pytest, mypy, ruff) installed via `make dev`.
- **Data home**: `~/.rtfi/` (SQLite at `~/.rtfi/rtfi.db`, config at `~/.rtfi/config.env`, logs at `~/.rtfi/rtfi.log` + `~/.rtfi/audit.log`).
- **Plugin manifest**: `.claude-plugin/plugin.json` (v1.2.0).

## Key Files

Read these first for any non-trivial task — they are the source of truth for current architecture:

- `scripts/rtfi_core.py` — consolidated core: `SessionState` dataclass, `RiskScore.calculate()` (5-factor formula), SQLite schema and persistence.
- `scripts/hook_handler.py` — PreToolUse / PostToolUse / SessionStart / Stop hook dispatch; Skill-tool displacement tracking lives here.
- `scripts/rtfi_dashboard.py` + `scripts/dashboard.html` — stdlib HTTP server + Chart.js single-page app.
- `scripts/rtfi_cli.py` — CLI entrypoint (setup, health, status, etc.).
- `scripts/demo_scenario.py` / `scripts/demo_compliance_check.py` — live demo driver and per-constraint validator.
- `tests/` — `test_core.py`, `test_hook_handler.py`, `test_dashboard.py`, `test_integration.py`. Follow existing patterns for new tests (see `test_backward_compat_*` for dataclass-field additions).
- `docs/ARCHITECTURE.md`, `docs/CASE_STUDY.md`, `docs/PRODUCT-BRIEF.md` — design context. `CHANGELOG.md` for version history.
- `CONTEXT.md` — short living session-handoff doc (see Session End below).
- `commands/`, `skills/`, `hooks/`, `agents/` — plugin surface area users invoke.

## Rules

1. **Stdlib only at runtime.** Do not add third-party runtime dependencies. Dev-only tools (pytest/mypy/ruff) go under `[project.optional-dependencies]` in `pyproject.toml`.
2. **Prefer extending `rtfi_core.py` and existing hook handlers** over creating new modules. v1.2.0 deliberately consolidated 7+ modules into one — don't re-fragment it.
3. **Backward-compat fields.** New `SessionState` fields must use `field(default_factory=...)` so older DB rows deserialize cleanly. Cover with a `test_backward_compat_*` test.
4. **Do not modify the 5-factor scoring weights or formula** without an explicit change request. New signals become derived metrics or new fields, not new scoring factors.
5. **Run tests, lint, and typecheck before declaring done:** `make test && make lint && make typecheck`.
6. **Config precedence** (highest wins): env vars → `~/.rtfi/config.env` → `.claude/rtfi.local.md` → built-in defaults. Respect this order when adding new settings.
7. **Never edit** `.claude/rtfi.local.md` or files under `~/.rtfi/` from code as part of a feature — those are user state.
8. **Commits**: no Co-Authored-By trailers; follow existing Conventional Commit style in `git log`.

## Active State

- **v1.2.0 shipped** (PR #1 merged). `docs/ARCHITECTURE.md` still references pre-consolidation module paths and needs a sweep.
- **In progress**: instruction-displacement enforcement — extending the v1.2.0 displacement risk factor with a behavioral compliance layer (expected-vs-observed artifact tracking at session end). See `docs/CASE_STUDY.md`.
- **Known nit**: `Makefile` `typecheck` target points at `scripts/rtfi/` (legacy path); actual code is `scripts/rtfi_core.py`. Fix when touching the Makefile.

## Session End Protocol

When the user signals they are done (e.g. "bye", "done", "wrap up", "end session"), update `CONTEXT.md` in the project root with:

- **Current Task** — one sentence on what was being worked on
- **Key Decisions** — bullet list, max 3 items
- **Next Steps** — bullet list, max 3 items

Keep `CONTEXT.md` under 20 lines total. Do not summarize the full conversation — only what's needed to resume next session.
