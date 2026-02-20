#!/usr/bin/env python3
"""RTFI Web Dashboard — live risk gauge and session history (ACT-049)."""

import argparse
import html
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingTCPServer

# Add rtfi package to path (same pattern as hook_handler.py and rtfi_cli.py)
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from rtfi.models.events import RiskScore, SessionOutcome
from rtfi.scoring.engine import SessionState
from rtfi.storage.database import Database

VERSION = "1.0.0"
THRESHOLD = 70.0

# ---------------------------------------------------------------------------
# Design tokens — mirrors rtfi_analytics_dashboard.html
# ---------------------------------------------------------------------------

_CSS = """
/* ── DESIGN TOKENS ──────────────────────────────────────────────────── */
:root {
  --bg:           #F4F5F7;
  --card:         #FFFFFF;
  --navy:         #1B2A4A;
  --navy-light:   #2D4066;
  --slate:        #64748B;
  --slate-light:  #94A3B8;
  --text:         #1E293B;
  --text-2:       #475569;
  --red:          #DC2626;
  --red-bg:       #FEF2F2;
  --red-border:   #FECACA;
  --amber:        #D97706;
  --amber-bg:     #FFFBEB;
  --amber-border: #FDE68A;
  --green:        #059669;
  --green-bg:     #ECFDF5;
  --green-border: #A7F3D0;
  --blue:         #2563EB;
  --blue-bg:      #EFF6FF;
  --gap:          16px;
  --r:            20px;
  --shadow:       0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
  --shadow-lg:    0 4px 12px rgba(0,0,0,.08), 0 2px 4px rgba(0,0,0,.04);
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  line-height: 1.6;
}

/* ── HEADER ─────────────────────────────────────────────────────────── */
#hdr {
  background: var(--navy);
  padding: 0.875rem 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
#hdr h1 { font-size: 1.1rem; font-weight: 700; color: #f1f5f9; letter-spacing: -.2px; }
#hdr h1 .brand { color: #60a5fa; }
#hdr p  { font-size: 0.72em; color: #94a3b8; margin-top: 2px; }

.live-dot {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.8em; color: #22c55e; font-weight: 600;
}
.live-dot::before {
  content: '●'; font-size: 0.7em;
  animation: blink 2s infinite;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }

/* ── LAYOUT ─────────────────────────────────────────────────────────── */
.wrap { max-width: 1400px; margin: 0 auto; padding: 24px; }

.stats-row {
  display: flex; gap: var(--gap); margin-bottom: var(--gap); flex-wrap: wrap;
}

.main-grid {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: var(--gap);
  min-height: calc(100vh - 212px);
}
.panel-live { overflow-y: auto; }
.panel-main { overflow-y: auto; }

/* ── CARDS ──────────────────────────────────────────────────────────── */
.card {
  background: var(--card);
  border-radius: var(--r);
  padding: 24px;
  box-shadow: var(--shadow);
  transition: box-shadow .2s;
}
.card:hover { box-shadow: var(--shadow-lg); }

.card-title {
  font-size: 12px; font-weight: 600; color: var(--slate);
  text-transform: uppercase; letter-spacing: .8px; margin-bottom: 14px;
}

/* ── HERO KPIs ──────────────────────────────────────────────────────── */
.hero-kpi { flex: 1; min-width: 160px; }
.kpi-icon {
  width: 44px; height: 44px; border-radius: 12px; display: flex;
  align-items: center; justify-content: center; font-size: 20px; margin-bottom: 14px;
}
.kpi-val   { font-size: 32px; font-weight: 800; letter-spacing: -.5px; line-height: 1.1; }
.kpi-label { font-size: 13px; font-weight: 600; color: var(--text-2); margin-top: 4px; }
.kpi-sub   { font-size: 12px; color: var(--slate-light); margin-top: 6px; }

.kpi-critical .kpi-icon { background: var(--red-bg);   color: var(--red); }
.kpi-critical .kpi-val  { color: var(--red); }
.kpi-warning .kpi-icon  { background: var(--amber-bg); color: var(--amber); }
.kpi-warning .kpi-val   { color: var(--amber); }
.kpi-ok .kpi-icon       { background: var(--green-bg); color: var(--green); }
.kpi-ok .kpi-val        { color: var(--green); }
.kpi-neutral .kpi-icon  { background: var(--blue-bg);  color: var(--blue); }
.kpi-neutral .kpi-val   { color: var(--navy); }

/* ── BADGES ─────────────────────────────────────────────────────────── */
.badge {
  display: inline-flex; align-items: center;
  padding: 3px 12px; border-radius: 20px; font-size: 12px; font-weight: 600;
}
.badge.hi { background: var(--red-bg);   color: var(--red);   border: 1px solid var(--red-border); }
.badge.md { background: var(--amber-bg); color: var(--amber); border: 1px solid var(--amber-border); }
.badge.lo { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-border); }

.obadge { display: inline-block; padding: 3px 12px; border-radius: 20px; font-size: 12px; font-weight: 500; }
.obadge.completed   { background: var(--green-bg); color: var(--green); }
.obadge.in_progress { background: var(--blue-bg);  color: var(--blue); }

/* ── GAUGE RING ─────────────────────────────────────────────────────── */
.gauge-ring {
  width: 140px; height: 140px; border-radius: 50%; border-width: 6px; border-style: solid;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  margin: 0.5rem auto 1rem; transition: border-color .5s, box-shadow .5s;
}
.gauge-val   { font-size: 2.8rem; font-weight: 800; line-height: 1; }
.gauge-label { font-size: 0.6rem; letter-spacing: 0.1em; font-weight: 700; margin-top: 3px; }

/* ── FACTOR BARS ────────────────────────────────────────────────────── */
.factor-bar { margin: 8px 0; }
.factor-bar-hd {
  display: flex; justify-content: space-between;
  font-size: 0.75em; margin-bottom: 4px;
}
.factor-bar-hd .name { color: var(--text-2); }
.factor-bar-track { background: #E2E8F0; border-radius: 4px; height: 6px; }
.factor-bar-fill  { height: 6px; border-radius: 4px; transition: width .5s; }

/* ── TABLE ──────────────────────────────────────────────────────────── */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; padding: 10px 14px; border-bottom: 2px solid #E2E8F0;
  color: var(--slate); font-weight: 600; font-size: 11px;
  text-transform: uppercase; letter-spacing: .6px; white-space: nowrap;
}
tbody td { padding: 10px 14px; border-bottom: 1px solid #F1F5F9; vertical-align: middle; }
tbody tr { cursor: pointer; transition: background .15s; }
tbody tr:hover { background: #F8FAFC; }

/* ── SESSION DETAIL ─────────────────────────────────────────────────── */
.back-link {
  display: inline-flex; align-items: center; gap: 6px; color: var(--blue);
  font-size: 0.85em; cursor: pointer; text-decoration: none; margin-bottom: 1rem;
}
.back-link:hover { text-decoration: underline; }

.stat-chips {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem; margin-bottom: 1.5rem;
}
.stat-chip {
  background: var(--bg); border: 1px solid #E2E8F0; border-radius: 12px; padding: 12px 16px;
}
.stat-chip .chip-val { font-size: 1.1rem; font-weight: 700; color: var(--navy); }
.stat-chip .chip-lbl {
  font-size: 0.7em; color: var(--slate); text-transform: uppercase; letter-spacing: .05em; margin-top: 2px;
}

/* ── EMPTY STATE ────────────────────────────────────────────────────── */
.empty { color: var(--slate); text-align: center; padding: 2rem; }

/* ── FOOTER ─────────────────────────────────────────────────────────── */
.ft { text-align: center; padding: 24px 0 12px; color: var(--slate-light); font-size: 12px; }

/* ── RESPONSIVE ─────────────────────────────────────────────────────── */
@media (max-width: 1024px) {
  .main-grid { grid-template-columns: 1fr; }
  .stat-chips { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 640px) {
  .wrap { padding: 12px; }
  .stats-row { gap: 10px; }
  .stat-chips { grid-template-columns: repeat(2, 1fr); }
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _risk_level(score: float) -> str:
    """Return badge level class: 'hi', 'md', or 'lo'."""
    if score >= THRESHOLD:
        return "hi"
    if score >= 30:
        return "md"
    return "lo"


def _color(score: float) -> str:
    """Return hex color for a risk score (matches HTML design tokens)."""
    if score >= THRESHOLD:
        return "#DC2626"  # --red
    if score >= 30:
        return "#D97706"  # --amber
    return "#059669"      # --green


def _risk_cell(score_obj) -> str:
    """Render an HTML risk score cell, avoiding backslashes in f-string expressions."""
    if score_obj is None:
        return "&mdash;"
    c = _color(score_obj.total)
    return f'<span style="color:{c};font-weight:600">{score_obj.total:.1f}</span>'


def _label(score: float) -> str:
    if score >= THRESHOLD:
        return "HIGH RISK"
    if score >= 30:
        return "ELEVATED"
    return "NORMAL"


def _badge(score: float) -> str:
    lvl = _risk_level(score)
    text = f"{score:.1f}{'!' if score >= THRESHOLD else ''}"
    return f'<span class="badge {html.escape(lvl)}">{html.escape(text)}</span>'


def _obadge(outcome: str) -> str:
    label = "Completed" if outcome == "completed" else "In Progress"
    return f'<span class="obadge {html.escape(outcome)}">{html.escape(label)}</span>'


def _get_live(db: Database):
    """Return (RiskScore | None, Session | None, is_live: bool)."""
    sessions = db.get_recent_sessions(limit=1)
    if not sessions:
        return None, None, False
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
            threshold=THRESHOLD,
        )
        return score, session, is_live
    # No state persisted yet — fall back to peak score, don't crash
    fallback = RiskScore(
        total=session.peak_risk_score,
        context_length=0.0,
        agent_fanout=0.0,
        autonomy_depth=0.0,
        decision_velocity=0.0,
        threshold_exceeded=session.peak_risk_score >= THRESHOLD,
    )
    return fallback, session, is_live


# ---------------------------------------------------------------------------
# HTML fragments
# ---------------------------------------------------------------------------

def frag_live(db: Database) -> str:
    score, session, is_live = _get_live(db)

    if score is None:
        return '<div class="empty">No sessions yet</div>'

    color = _color(score.total)
    label = _label(score.total)
    sid_short = (session.id[:12] + "\u2026") if session else ""
    live_indicator = (
        '<span style="color:var(--green)">●</span> LIVE'
        if is_live
        else '<span style="color:var(--slate-light)">◉</span> LAST SESSION'
    )

    factors = [
        ("Context", score.context_length, "\u00d70.25"),
        ("Fanout",  score.agent_fanout,   "\u00d70.30"),
        ("Autonomy", score.autonomy_depth, "\u00d70.25"),
        ("Velocity", score.decision_velocity, "\u00d70.20"),
    ]
    bars = ""
    for name, val, weight in factors:
        pct = int(val * 100)
        fc = _color(pct)
        bars += (
            f'<div class="factor-bar">'
            f'<div class="factor-bar-hd">'
            f'<span class="name">{html.escape(name)} '
            f'<span style="color:var(--slate-light)">{weight}</span></span>'
            f'<span style="color:{fc};font-weight:600">{pct}%</span>'
            f'</div>'
            f'<div class="factor-bar-track">'
            f'<div class="factor-bar-fill" style="background:{fc};width:{pct}%"></div>'
            f'</div></div>'
        )

    return (
        f'<div style="text-align:center;margin-bottom:1.25rem">'
        f'<div style="font-size:0.75em;color:var(--slate);margin-bottom:0.75rem">{live_indicator}</div>'
        f'<div class="gauge-ring" style="border-color:{color};box-shadow:0 0 24px {color}33">'
        f'<div class="gauge-val" style="color:{color}">{score.total:.0f}</div>'
        f'<div class="gauge-label" style="color:{color}">{html.escape(label)}</div>'
        f'</div>'
        f'<div style="font-size:0.75em;color:var(--slate-light);font-family:monospace">'
        f'{html.escape(sid_short)}</div>'
        f'</div>'
        f'{bars}'
    )


def frag_stats(db: Database) -> str:
    stats = db.get_stats()
    total = stats["total_sessions"]
    hi    = stats["high_risk_sessions"]
    events = stats["total_events"]

    hi_state = "kpi-critical" if hi > 0 else "kpi-ok"
    hi_icon  = "&#9888;" if hi > 0 else "&#9745;"

    cards = [
        ("kpi-neutral", "&#128202;", str(total),  "Total Sessions",      "All time"),
        (hi_state,      hi_icon,     str(hi),      "High-Risk Sessions",  f"&ge;&thinsp;{THRESHOLD:.0f} threshold"),
        ("kpi-ok",      "&#9889;",   str(events),  "Total Events",        "Tool calls + agent spawns"),
        ("kpi-neutral", "&#128274;", f"v{VERSION}", "Dashboard Version",  "RTFI live monitor"),
    ]
    parts = []
    for cls, icon, val, label, sub in cards:
        parts.append(
            f'<div class="card hero-kpi {cls}">'
            f'<div class="kpi-icon">{icon}</div>'
            f'<div class="kpi-val">{html.escape(val)}</div>'
            f'<div class="kpi-label">{html.escape(label)}</div>'
            f'<div class="kpi-sub">{sub}</div>'
            f'</div>'
        )
    return "".join(parts)


def frag_sessions(db: Database) -> str:
    sessions = db.get_recent_sessions(limit=25)
    if not sessions:
        return (
            '<tr><td colspan="5" class="empty">No sessions recorded yet</td></tr>'
        )
    rows = []
    for s in sessions:
        time_str = s.started_at.strftime("%m/%d %H:%M")
        sid_disp = s.id[:8] + ".."
        badge = _badge(s.peak_risk_score)
        status_dot = (
            '<span style="color:var(--green)">&#9679;</span>'
            if s.outcome == SessionOutcome.IN_PROGRESS
            else '<span style="color:var(--slate-light)">&#9679;</span>'
        )
        rows.append(
            f'<tr hx-get="/frag/session/{html.escape(s.id)}" '
            f'hx-target="#main-content" hx-push-url="/session/{html.escape(s.id)}">'
            f'<td style="font-family:\'SF Mono\',monospace;font-size:0.85em">'
            f'{status_dot} {html.escape(sid_disp)}</td>'
            f'<td style="color:var(--slate)">{html.escape(time_str)}</td>'
            f'<td>{badge}</td>'
            f'<td style="text-align:right;color:var(--text-2)">{s.total_tool_calls}</td>'
            f'<td style="text-align:right;color:var(--text-2)">{s.total_agent_spawns}</td>'
            f'</tr>'
        )
    return "".join(rows)


def frag_sessions_panel() -> str:
    return (
        '<div>'
        '<div class="card-title">Session History '
        '<span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--slate-light)">'
        '&mdash; click a row to drill down</span></div>'
        '<div style="overflow-x:auto">'
        '<table>'
        '<thead><tr>'
        '<th>Session</th>'
        '<th>Time</th>'
        '<th>Peak Risk</th>'
        '<th style="text-align:right">Tools</th>'
        '<th style="text-align:right">Agents</th>'
        '</tr></thead>'
        '<tbody id="sessions-body" hx-get="/frag/sessions" '
        'hx-trigger="load, every 10s" hx-swap="innerHTML">'
        '<tr><td colspan="5" style="color:var(--slate);padding:1.5rem 0;text-align:center">'
        'Loading sessions&hellip;</td></tr>'
        '</tbody></table></div></div>'
    )


def frag_session(db: Database, session_id: str) -> str:
    session = db.get_session(session_id)
    if not session:
        session = db.find_session_by_prefix(session_id)
    if not session:
        return '<div style="color:var(--red);padding:2rem">Session not found</div>'

    color = _color(session.peak_risk_score)
    outcome_label = session.outcome.value.replace("_", " ").title()
    started = session.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    ended = session.ended_at.strftime("%Y-%m-%d %H:%M:%S UTC") if session.ended_at else "&mdash;"

    stat_chips = [
        (_badge(session.peak_risk_score),         "Peak Risk"),
        (str(session.total_tool_calls),            "Tool Calls"),
        (str(session.total_agent_spawns),          "Agent Spawns"),
        (_obadge(session.outcome.value),           "Outcome"),
    ]
    chips_html = "".join(
        f'<div class="stat-chip">'
        f'<div class="chip-val">{val}</div>'
        f'<div class="chip-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for val, lbl in stat_chips
    )

    events = list(db.get_session_events(session_id))[-20:]
    event_rows = "".join(
        f'<tr>'
        f'<td style="font-family:\'SF Mono\',monospace;color:var(--slate)">'
        f'{html.escape(ev.timestamp.strftime("%H:%M:%S"))}</td>'
        f'<td style="color:var(--text-2)">{html.escape(ev.event_type.value)}</td>'
        f'<td style="font-family:\'SF Mono\',monospace;color:var(--text)">'
        f'{html.escape(ev.tool_name or "—")}</td>'
        f'<td style="text-align:right">'
        f'{_risk_cell(ev.risk_score)}'
        f'</td>'
        f'</tr>'
        for ev in reversed(events)
    ) or '<tr><td colspan="4" class="empty">No events recorded</td></tr>'

    project_line = (
        f'<br>Project: <span style="font-family:\'SF Mono\',monospace;color:var(--text)">'
        f'{html.escape(session.project_dir)}</span>'
        if session.project_dir else ""
    )
    return (
        f'<div>'
        f'<a hx-get="/frag/sessions-panel" hx-target="#main-content" hx-push-url="/" '
        f'class="back-link">&larr; All Sessions</a>'
        f'<div style="font-family:\'SF Mono\',monospace;font-size:0.8em;color:var(--slate);margin-bottom:4px">'
        f'{html.escape(session.id)}</div>'
        f'<div style="font-size:0.75em;color:var(--slate);margin-bottom:1.25rem">'
        f'Started: {html.escape(started)}<br>Ended: {ended}{project_line}</div>'
        f'<div class="stat-chips">{chips_html}</div>'
        f'<div class="card-title">Recent Events</div>'
        f'<div style="overflow-x:auto">'
        f'<table>'
        f'<thead><tr>'
        f'<th>Time</th><th>Type</th><th>Tool</th>'
        f'<th style="text-align:right">Score</th>'
        f'</tr></thead>'
        f'<tbody>{event_rows}</tbody>'
        f'</table></div></div>'
    )


def page(right_content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RTFI Analytics</title>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <style>{_CSS}</style>
</head>
<body>
  <!-- Header -->
  <div id="hdr">
    <div>
      <h1><span class="brand">RTFI</span> Analytics</h1>
      <p>Real-Time Instruction Compliance Risk Scoring &mdash; Live data from ~/.rtfi/rtfi.db</p>
    </div>
    <div class="live-dot">LIVE</div>
  </div>

  <div class="wrap">
    <!-- KPI hero row — polled every 10s -->
    <div class="stats-row"
         hx-get="/frag/stats"
         hx-trigger="load, every 10s"
         hx-swap="innerHTML">
      <div class="card hero-kpi kpi-neutral" style="flex:1">
        <div class="kpi-icon">&#128202;</div>
        <div class="kpi-val" style="color:var(--slate-light)">&hellip;</div>
        <div class="kpi-label">Loading</div>
      </div>
    </div>

    <!-- Two-panel grid: live gauge + main content -->
    <div class="main-grid">
      <!-- Live risk gauge -->
      <div class="card panel-live">
        <div class="card-title">Live Risk Monitor</div>
        <div hx-get="/frag/live" hx-trigger="load, every 2s" hx-swap="innerHTML">
          <div class="empty">Loading&hellip;</div>
        </div>
      </div>

      <!-- Main content (sessions list or session detail) -->
      <div class="card panel-main" id="main-content">
        {right_content}
      </div>
    </div>

    <footer class="ft">RTFI Analytics &mdash; AI Compliance Risk Monitoring</footer>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    """Routes GET requests to HTML fragment generators."""

    def log_message(self, format, *args):  # noqa: A002
        pass  # Suppress per-request stdout noise during demos

    def do_GET(self):
        try:
            self._handle()
        except Exception as exc:
            self.send_error(500, str(exc))

    def _handle(self):
        path = self.path.split("?")[0]
        db = Database()
        try:
            if path == "/":
                body = page(frag_sessions_panel()).encode()
                self._ok("text/html", body)
            elif path == "/frag/live":
                self._ok("text/html", frag_live(db).encode())
            elif path == "/frag/stats":
                self._ok("text/html", frag_stats(db).encode())
            elif path == "/frag/sessions":
                self._ok("text/html", frag_sessions(db).encode())
            elif path == "/frag/sessions-panel":
                self._ok("text/html", frag_sessions_panel().encode())
            elif path.startswith("/frag/session/"):
                sid = path[len("/frag/session/"):]
                self._ok("text/html", frag_session(db, sid).encode())
            elif path.startswith("/session/"):
                sid = path[len("/session/"):]
                self._ok("text/html", page(frag_session(db, sid)).encode())
            else:
                self.send_error(404, "Not found")
        finally:
            db.close()

    def _ok(self, content_type: str, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def serve(port: int, open_browser: bool) -> None:
    ThreadingTCPServer.allow_reuse_address = True
    with ThreadingTCPServer(("", port), DashboardHandler) as server:
        url = f"http://localhost:{port}"
        print(f"RTFI Dashboard → {url}")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="RTFI Web Dashboard")
    parser.add_argument("--port", type=int, default=7430, help="Port to listen on (default: 7430)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser on start")
    args = parser.parse_args()
    serve(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
