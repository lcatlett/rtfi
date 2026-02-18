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
# Helpers
# ---------------------------------------------------------------------------

def _color(score: float) -> str:
    """Return hex color for a risk score."""
    if score < THRESHOLD * 0.7:
        return "#22c55e"  # green
    if score < THRESHOLD:
        return "#f59e0b"  # amber
    return "#ef4444"    # red


def _label(score: float) -> str:
    if score < THRESHOLD * 0.7:
        return "NORMAL"
    if score < THRESHOLD:
        return "ELEVATED"
    return "HIGH RISK"


def _badge(score: float) -> str:
    color = _color(score)
    text = f"{score:.1f}{'!' if score >= THRESHOLD else ''}"
    return (
        f'<span style="background:{color};color:#0f172a;font-weight:700;'
        f'padding:2px 8px;border-radius:4px;font-size:0.85em">{html.escape(text)}</span>'
    )


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
        return '<div style="color:#64748b;padding:2rem;text-align:center">No sessions yet</div>'

    color = _color(score.total)
    label = _label(score.total)
    sid_short = (session.id[:12] + "...") if session else ""
    live_dot = (
        '<span style="color:#22c55e">●</span> LIVE'
        if is_live
        else '<span style="color:#64748b">◉</span> LAST'
    )

    factors = [
        ("Context", score.context_length, "×0.25"),
        ("Fanout", score.agent_fanout, "×0.30"),
        ("Autonomy", score.autonomy_depth, "×0.25"),
        ("Velocity", score.decision_velocity, "×0.20"),
    ]
    bars_html = ""
    for name, val, weight in factors:
        pct = int(val * 100)
        bar_color = _color(val * 100)
        bars_html += (
            f'<div style="margin:6px 0">'
            f'<div style="display:flex;justify-content:space-between;font-size:0.8em;color:#94a3b8;margin-bottom:3px">'
            f'<span>{html.escape(name)} <span style="color:#64748b">{weight}</span></span>'
            f'<span style="color:{bar_color}">{pct}%</span></div>'
            f'<div style="background:#334155;border-radius:3px;height:6px">'
            f'<div style="background:{bar_color};width:{pct}%;height:6px;border-radius:3px;transition:width 0.5s"></div>'
            f'</div></div>'
        )

    return (
        f'<div style="text-align:center;padding:0.5rem 0">'
        f'<div style="font-size:0.8em;color:#64748b;margin-bottom:1rem">{live_dot}</div>'
        f'<div style="width:140px;height:140px;border-radius:50%;border:6px solid {color};'
        f'box-shadow:0 0 30px {color}88;display:flex;flex-direction:column;'
        f'align-items:center;justify-content:center;transition:border-color 0.5s,box-shadow 0.5s;margin:0 auto 1rem">'
        f'<div style="font-size:2.8rem;font-weight:700;color:{color};line-height:1">{score.total:.0f}</div>'
        f'<div style="font-size:0.7rem;color:{color};letter-spacing:0.05em;margin-top:2px">{html.escape(label)}</div>'
        f'</div>'
        f'<div style="font-size:0.75em;color:#64748b;margin-bottom:1.5rem;font-family:monospace">'
        f'{html.escape(sid_short)}</div>'
        f'{bars_html}'
        f'</div>'
    )


def frag_stats(db: Database) -> str:
    stats = db.get_stats()
    cards = [
        (str(stats["total_sessions"]), "Sessions"),
        (str(stats["high_risk_sessions"]), "High Risk"),
        (str(stats["total_events"]), "Events"),
        (VERSION, "Version"),
    ]
    parts = [
        f'<div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:0.5rem 1rem;text-align:center">'
        f'<div style="font-size:1.1rem;font-weight:700;color:#e2e8f0">{html.escape(val)}</div>'
        f'<div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;letter-spacing:0.05em">'
        f'{html.escape(label)}</div></div>'
        for val, label in cards
    ]
    return '<div style="display:flex;gap:0.75rem;flex-wrap:wrap">' + "".join(parts) + "</div>"


def frag_sessions(db: Database) -> str:
    sessions = db.get_recent_sessions(limit=25)
    if not sessions:
        return (
            '<tr><td colspan="5" style="color:#64748b;text-align:center;padding:2rem">'
            "No sessions recorded yet</td></tr>"
        )
    rows = []
    for s in sessions:
        time_str = s.started_at.strftime("%m/%d %H:%M")
        sid_disp = s.id[:8] + ".."
        badge = _badge(s.peak_risk_score)
        status_color = "#22c55e" if s.outcome == SessionOutcome.IN_PROGRESS else "#475569"
        rows.append(
            f'<tr hx-get="/frag/session/{html.escape(s.id)}" '
            f'hx-target="#main-content" hx-push-url="/session/{html.escape(s.id)}" '
            f'style="cursor:pointer;border-bottom:1px solid #1e293b" '
            f'onmouseover="this.style.background=\'#1e293b\'" '
            f'onmouseout="this.style.background=\'transparent\'">'
            f'<td style="font-family:monospace;font-size:0.85em">'
            f'<span style="color:{status_color}">●</span> {html.escape(sid_disp)}</td>'
            f'<td style="color:#94a3b8;font-size:0.85em">{html.escape(time_str)}</td>'
            f'<td>{badge}</td>'
            f'<td style="text-align:right;color:#94a3b8;font-size:0.85em">{s.total_tool_calls}</td>'
            f'<td style="text-align:right;color:#94a3b8;font-size:0.85em">{s.total_agent_spawns}</td>'
            f'</tr>'
        )
    return "".join(rows)


def frag_sessions_panel() -> str:
    return (
        '<div style="height:100%">'
        '<div style="color:#94a3b8;font-size:0.8em;font-weight:600;letter-spacing:0.1em;'
        'text-transform:uppercase;margin-bottom:1rem">Recent Sessions</div>'
        '<table style="width:100%;border-collapse:collapse;font-size:0.9em">'
        '<thead><tr style="color:#64748b;font-size:0.75em;text-transform:uppercase;letter-spacing:0.05em">'
        '<th style="text-align:left;padding:0 0 8px 0;border-bottom:1px solid #334155">ID</th>'
        '<th style="text-align:left;padding:0 0 8px 8px;border-bottom:1px solid #334155">Time</th>'
        '<th style="text-align:left;padding:0 0 8px 8px;border-bottom:1px solid #334155">Peak</th>'
        '<th style="text-align:right;padding:0 0 8px 8px;border-bottom:1px solid #334155">Tools</th>'
        '<th style="text-align:right;padding:0 0 8px 8px;border-bottom:1px solid #334155">Agents</th>'
        '</tr></thead>'
        '<tbody id="sessions-body" hx-get="/frag/sessions" hx-trigger="load, every 10s" hx-swap="innerHTML">'
        '<tr><td colspan="5" style="color:#64748b;padding:1rem 0">Loading...</td></tr>'
        '</tbody></table></div>'
    )


def frag_session(db: Database, session_id: str) -> str:
    session = db.get_session(session_id)
    if not session:
        session = db.find_session_by_prefix(session_id)
    if not session:
        return '<div style="color:#ef4444;padding:2rem">Session not found</div>'

    color = _color(session.peak_risk_score)
    outcome_label = session.outcome.value.replace("_", " ").title()
    started = session.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    ended = session.ended_at.strftime("%Y-%m-%d %H:%M:%S UTC") if session.ended_at else "—"

    stat_cards = [
        ("Peak Risk", f"{session.peak_risk_score:.1f}", color),
        ("Tool Calls", str(session.total_tool_calls), "#e2e8f0"),
        ("Agents", str(session.total_agent_spawns), "#e2e8f0"),
        ("Outcome", outcome_label, "#e2e8f0"),
    ]
    stats_html = "".join(
        f'<div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:0.75rem 1rem">'
        f'<div style="font-size:1.1rem;font-weight:700;color:{c}">{html.escape(v)}</div>'
        f'<div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;letter-spacing:0.05em">'
        f'{html.escape(lbl)}</div></div>'
        for lbl, v, c in stat_cards
    )

    events = list(db.get_session_events(session_id))[-20:]
    event_rows = "".join(
        f'<tr style="border-bottom:1px solid #1e293b">'
        f'<td style="padding:4px 0;font-size:0.8em;color:#64748b;font-family:monospace">'
        f'{html.escape(ev.timestamp.strftime("%H:%M:%S"))}</td>'
        f'<td style="padding:4px 8px;font-size:0.8em;color:#94a3b8">{html.escape(ev.event_type.value)}</td>'
        f'<td style="padding:4px 8px;font-size:0.8em;font-family:monospace;color:#cbd5e1">'
        f'{html.escape(ev.tool_name or "—")}</td>'
        f'<td style="padding:4px 0;text-align:right;font-size:0.8em;color:#94a3b8">'
        f'{f"{ev.risk_score.total:.1f}" if ev.risk_score else "—"}</td>'
        f'</tr>'
        for ev in reversed(events)
    ) or '<tr><td colspan="4" style="color:#64748b;padding:1rem 0;font-size:0.85em">No events recorded</td></tr>'

    project_line = (
        f'<br>Project: {html.escape(session.project_dir)}' if session.project_dir else ""
    )
    return (
        f'<div>'
        f'<a hx-get="/frag/sessions-panel" hx-target="#main-content" hx-push-url="/" '
        f'style="color:#60a5fa;font-size:0.85em;cursor:pointer;text-decoration:none;'
        f'display:inline-block;margin-bottom:1rem">← All Sessions</a>'
        f'<div style="font-family:monospace;font-size:0.8em;color:#64748b;margin-bottom:0.5rem">'
        f'{html.escape(session.id)}</div>'
        f'<div style="font-size:0.75em;color:#64748b;margin-bottom:1.5rem">'
        f'Started: {html.escape(started)}<br>Ended: {html.escape(ended)}{project_line}</div>'
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.75rem;margin-bottom:1.5rem">'
        f'{stats_html}</div>'
        f'<div style="color:#94a3b8;font-size:0.8em;font-weight:600;letter-spacing:0.1em;'
        f'text-transform:uppercase;margin-bottom:0.75rem">Recent Events</div>'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="color:#64748b;font-size:0.7em;text-transform:uppercase">'
        f'<th style="text-align:left;padding-bottom:6px;border-bottom:1px solid #334155">Time</th>'
        f'<th style="text-align:left;padding:0 8px 6px;border-bottom:1px solid #334155">Type</th>'
        f'<th style="text-align:left;padding:0 8px 6px;border-bottom:1px solid #334155">Tool</th>'
        f'<th style="text-align:right;padding-bottom:6px;border-bottom:1px solid #334155">Score</th>'
        f'</tr></thead>'
        f'<tbody>{event_rows}</tbody></table></div>'
    )


def page(right_content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RTFI — AI Compliance Risk Dashboard</title>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0f172a;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      min-height: 100vh;
    }}
    #header {{
      background: #1e293b;
      border-bottom: 1px solid #334155;
      padding: 0.75rem 1.5rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    #header h1 {{ font-size: 1.1rem; font-weight: 700; color: #f1f5f9; }}
    #header h1 span {{ color: #60a5fa; }}
    #stats-bar {{
      padding: 0.75rem 1.5rem;
      background: #1e293b;
      border-bottom: 1px solid #334155;
    }}
    #content {{
      display: grid;
      grid-template-columns: 300px 1fr;
      height: calc(100vh - 112px);
    }}
    #live-panel {{
      background: #1e293b;
      border-right: 1px solid #334155;
      padding: 1.5rem;
      overflow-y: auto;
    }}
    #live-panel h2 {{
      font-size: 0.8em;
      font-weight: 600;
      color: #94a3b8;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin-bottom: 1rem;
    }}
    #main-content {{ padding: 1.5rem; overflow-y: auto; }}
    td {{ padding: 6px 8px; }}
    td:first-child {{ padding-left: 0; }}
    td:last-child {{ padding-right: 0; }}
  </style>
</head>
<body>
  <div id="header">
    <h1><span>RTFI</span> · AI Compliance Risk Dashboard</h1>
    <span style="font-size:0.8em;color:#22c55e">● Live</span>
  </div>
  <div id="stats-bar">
    <div hx-get="/frag/stats" hx-trigger="load, every 10s" hx-swap="innerHTML">Loading stats…</div>
  </div>
  <div id="content">
    <div id="live-panel">
      <h2>Live Risk Monitor</h2>
      <div hx-get="/frag/live" hx-trigger="load, every 2s" hx-swap="innerHTML">Loading…</div>
    </div>
    <div id="main-content">
      {right_content}
    </div>
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
