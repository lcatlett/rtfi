# Handoff: ACT-049 — RTFI Customer Demo Dashboard (HTMX)

**Generated**: 2026-02-18
**Branch**: main
**Status**: In Progress — plan approved, implementation not started

---

## Goal

Build a minimal web dashboard to make RTFI customer-demonstrable. The "wow" moment: a live risk gauge that updates every 2 seconds as Claude tool calls happen, showing the score climb when agents spawn. Uses HTMX + Python stdlib (no new dependencies). Referenced as ACT-049 in `/Users/lindsey/projects/kd/outputs/master-action-checklist.md`.

---

## Completed This Session

- [x] ACT-011: Confirmed all 44 tests passing (`test_risk_score_accumulates_across_processes` is green — was already fixed in v0.2.0)
- [x] Committed architecture docs + ADRs + plugin schema fixes → `6c35b3e docs(arch): add ARCHITECTURE.md, ADRs, fix plugin schema [1.0.0]`
- [x] Fully designed the dashboard — plan written at `/Users/lindsey/.claude/plans/joyful-juggling-brooks.md`
- [x] Confirmed working tree clean, 44/44 tests green before starting dashboard work

---

## Not Yet Done

- [ ] Create `scripts/rtfi_dashboard.py` (~380 lines) — the entire dashboard implementation
- [ ] Create `commands/dashboard.md` — slash command to launch the dashboard
- [ ] Verify curl tests pass and browser demo works
- [ ] Mark ACT-049 as `[x]` in `/Users/lindsey/projects/kd/outputs/master-action-checklist.md`

---

## Failed Approaches

None — implementation not started yet. Plan was designed but not executed.

---

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| Python stdlib `http.server` + `socketserver.ThreadingTCPServer` | Zero new runtime dependencies (project has only pydantic) |
| HTMX from CDN (`unpkg.com/htmx.org@2.0.4`) | No build step, works offline once cached |
| All HTML/CSS inline in single Python file | Easy to run, no template files to manage, single deployment artifact |
| 2s polling for live gauge, 10s for session list | Fast enough to feel live during demo, not wasteful |
| Dark professional theme with glowing CSS circle gauge | Visually striking for customer demo — "wow" factor |
| Reuse `SessionState.from_dict()` + `RiskScore.calculate()` for live score | Correct, no duplication; computes live score from persisted state dict |
| Port 7430 default | Unlikely to conflict with common services |

---

## Current State

**Working**: Everything in RTFI v1.0.0 — 44 tests green, hooks functional, all 6 CLI commands working, plugin schema valid.

**Not Yet Built**: The web dashboard (the only thing left for ACT-049).

**Uncommitted Changes**: None (clean working tree).

---

## Files to Know

| File | Why It Matters |
|------|----------------|
| `scripts/rtfi_dashboard.py` | **CREATE THIS** — the entire dashboard |
| `commands/dashboard.md` | **CREATE THIS** — `/rtfi:dashboard` slash command |
| `scripts/rtfi/storage/database.py` | All DB queries to reuse |
| `scripts/rtfi/scoring/engine.py` | `SessionState.from_dict()` for live score hydration |
| `scripts/rtfi/models/events.py` | `RiskScore.calculate()`, `SessionOutcome` enum |
| `/Users/lindsey/.claude/plans/joyful-juggling-brooks.md` | Complete implementation plan — READ THIS FIRST |

---

## Code Context

### Live score computation (the key piece)

```python
# How to get the current risk score for the most recent session:
from rtfi.storage.database import Database
from rtfi.scoring.engine import SessionState
from rtfi.models.events import RiskScore, SessionOutcome

db = Database()
sessions = db.get_recent_sessions(limit=1)
session = sessions[0]
is_live = session.outcome == SessionOutcome.IN_PROGRESS
state_dict = db.load_session_state(session.id)
if state_dict:
    state = SessionState.from_dict(state_dict, session)
    score = RiskScore.calculate(
        tokens=state.tokens,
        active_agents=state.active_agents,
        steps_since_confirm=state.steps_since_confirm,
        tools_per_minute=state.tools_per_minute,
        threshold=70.0,
    )
# score.total, score.context_length, score.agent_fanout,
# score.autonomy_depth, score.decision_velocity are all floats 0-1 (except total: 0-100)
```

### Session model fields (what's queryable)

```python
session.id                  # UUID string
session.started_at          # datetime (UTC)
session.ended_at            # datetime | None
session.peak_risk_score     # float (0-100)
session.final_risk_score    # float | None
session.total_tool_calls    # int
session.total_agent_spawns  # int
session.outcome             # SessionOutcome enum: "in_progress" | "completed" | ...
session.project_dir         # str | None
```

### Database methods needed for dashboard

```python
db.get_recent_sessions(limit=25) -> list[Session]
db.get_session(session_id: str) -> Session | None
db.find_session_by_prefix(prefix: str) -> Session | None
db.get_session_events(session_id: str) -> Iterator[RiskEvent]
db.get_stats() -> {"total_sessions": int, "high_risk_sessions": int, "total_events": int, "database_path": str}
db.load_session_state(session_id: str) -> dict | None
```

### HTMX patterns to use

```html
<!-- Live gauge — polls every 2s, fires immediately on load -->
<div id="live-panel"
     hx-get="/frag/live"
     hx-trigger="load, every 2s"
     hx-swap="innerHTML">Loading...</div>

<!-- Session table body — polls every 10s -->
<tbody id="sessions-body"
       hx-get="/frag/sessions"
       hx-trigger="load, every 10s"
       hx-swap="innerHTML"></tbody>

<!-- Session row — click to navigate, update URL, replace right panel -->
<tr hx-get="/frag/session/{session.id}"
    hx-target="#main-content"
    hx-push-url="/session/{session.id}">...</tr>

<!-- Back link in session detail -->
<a hx-get="/frag/sessions-panel"
   hx-target="#main-content"
   hx-push-url="/">← All Sessions</a>
```

### Route map

```
GET /                      → page() with sessions-panel shell in right column
GET /frag/live             → frag_live(db) — gauge + 4 factor bars
GET /frag/stats            → frag_stats(db) — 4 stat cards
GET /frag/sessions         → frag_sessions(db) — <tr> rows only (for tbody)
GET /frag/sessions-panel   → frag_sessions_panel() — full table shell w/ HTMX triggers
GET /frag/session/{id}     → frag_session(db, id) — detail fragment
GET /session/{id}          → page() with frag_session pre-rendered in right column
```

### Visual wireframe

```
┌─ RTFI  AI Compliance Risk Dashboard ──────────────────● Live ─┐
├─ [47 Sessions]──[3 High Risk]──[892 Events]──[v1.0.0] ────────┤
│                                                               │
│  ┌─ Live Risk Monitor ─────┐  ┌─ Recent Sessions ───────────┐ │
│  │  ● LIVE                 │  │ ID       Time   Peak  Tools  │ │
│  │   ┌────────────────┐    │  │ abc123.. 14:32  73.2! 47    │ │
│  │   │      73        │    │  │ def456.. 13:15  45.1  23    │ │
│  │   │   HIGH RISK    │    │  │ ghi789.. 12:01  12.0  8     │ │
│  │   └────────────────┘    │  └─────────────────────────────┘ │
│  │   abc12345...           │                                  │
│  │                         │  [click row → session detail     │
│  │  Context  ×0.25 ████░░  │   replaces right panel via HTMX] │
│  │  Fanout   ×0.30 ████████│                                  │
│  │  Autonomy ×0.25 ████░░░ │                                  │
│  │  Velocity ×0.20 ███░░░░ │                                  │
│  └─────────────────────────┘                                  │
└───────────────────────────────────────────────────────────────┘
```

Gauge: CSS circle with colored glow. Green <threshold×0.7, amber <threshold, red ≥threshold.

---

## Resume Instructions

1. Read `/Users/lindsey/.claude/plans/joyful-juggling-brooks.md` — the full implementation plan
2. Create `scripts/rtfi_dashboard.py` using the plan — key parts:
   - `ThreadingTCPServer` on port 7430, handler suppresses request logs
   - Inline CSS (dark theme: `--bg:#0f172a`, `--surf:#1e293b`)
   - HTMX from `https://unpkg.com/htmx.org@2.0.4`
   - All 7 routes as described above
3. Create `commands/dashboard.md`:
   ```markdown
   ---
   name: dashboard
   description: Launch the RTFI web dashboard for live risk monitoring and session history
   ---

   Launch the RTFI web dashboard:

   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/rtfi_dashboard.py" --port 7430
   ```

   Open http://localhost:7430. Stop with Ctrl+C.
   ```
4. Verify:
   - `python3 scripts/rtfi_dashboard.py --no-browser` — should print "RTFI Dashboard → http://localhost:7430"
   - `curl -s http://localhost:7430/frag/live` — should return HTML with `gauge` class
   - `curl -s http://localhost:7430/frag/stats` — should return HTML with session counts
   - Browser: gauge auto-refreshes every 2s, session rows load, clicking navigates
   - `python -m pytest tests/ -v` — all 44 must still pass (no regressions)
5. Commit: `feat(dashboard): add HTMX web dashboard for customer demos [1.0.0]`
6. Mark ACT-049 `[x]` in `/Users/lindsey/projects/kd/outputs/master-action-checklist.md`

---

## Warnings

- The `sys.path.insert(0, str(script_dir))` trick used in `hook_handler.py` and `rtfi_cli.py` is needed in the dashboard too — without it, `from rtfi.storage.database import Database` will fail
- `SessionState.from_dict()` needs a `Session` object as second arg, not just the session_id
- If `load_session_state()` returns `None` (no state persisted yet), fall back to `session.peak_risk_score` for the gauge display — don't crash
- The `DashboardHandler.log_message()` should be overridden to pass/no-op to suppress per-request stdout noise during demos
- `ThreadingTCPServer.allow_reuse_address = True` must be set before `serve_forever()` to avoid "address already in use" on restart
- `db.close()` must be called in a `finally` block in the handler — connections are cached and must be cleaned up
