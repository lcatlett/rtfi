#!/usr/bin/env python3
"""RTFI Web Dashboard — JSON API server + static HTML (Phase 2 rebuild)."""

import argparse
import json
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingTCPServer
from urllib.parse import parse_qs, urlparse

# Add rtfi package to path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from rtfi_core import (
    Database,
    RiskScore,
    SessionOutcome,
    SessionState,
    __version__,
    load_settings,
    risk_color,
    risk_level,
)

# ---------------------------------------------------------------------------
# Settings (loaded once at startup, used by all handlers)
# ---------------------------------------------------------------------------

_settings: dict = {}
STALE_SESSION_HOURS = 2


def _load_config() -> dict:
    """Load settings and cache them."""
    global _settings
    _settings = load_settings()
    return _settings


def _threshold() -> float:
    return float(_settings.get("threshold", 70.0))


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _json_serial(obj):
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _session_to_dict(session, stale_hours: int = 2) -> dict:
    """Convert a Session model to a JSON-safe dict."""
    outcome = session.outcome.value
    # Mark stale in-progress sessions as abandoned
    if outcome == "in_progress" and session.started_at:
        elapsed = datetime.now(timezone.utc) - session.started_at
        if elapsed > timedelta(hours=stale_hours):
            outcome = "abandoned"

    return {
        "id": session.id,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "peak_risk_score": session.peak_risk_score,
        "total_tool_calls": session.total_tool_calls,
        "total_agent_spawns": session.total_agent_spawns,
        "outcome": outcome,
        "project_dir": session.project_dir,
    }


def _event_to_dict(event) -> dict:
    """Convert a RiskEvent model to a JSON-safe dict."""
    return {
        "id": event.id,
        "session_id": event.session_id,
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type.value,
        "tool_name": event.tool_name,
        "context_tokens": event.context_tokens,
        "risk_score": event.risk_score.model_dump() if event.risk_score else None,
        "metadata": event.metadata,
    }


# ---------------------------------------------------------------------------
# API route handlers
# ---------------------------------------------------------------------------

def api_config() -> dict:
    """GET /api/config"""
    return {
        "threshold": _settings.get("threshold", 70.0),
        "max_tokens": _settings.get("max_tokens", 128000),
        "max_agents": _settings.get("max_agents", 5),
        "max_steps": _settings.get("max_steps", 10),
        "max_tools_per_min": _settings.get("max_tools_per_min", 20.0),
        "agent_decay_seconds": _settings.get("agent_decay_seconds", 300),
        "action_mode": _settings.get("action_mode", "alert"),
        "version": __version__,
    }


def api_live(db: Database) -> dict:
    """GET /api/live — live gauge data for current/last session."""
    sessions = db.get_recent_sessions(limit=1)
    if not sessions:
        return {"score": None, "session": None, "is_live": False}

    session = sessions[0]
    is_live = session.outcome == SessionOutcome.IN_PROGRESS
    stale_hours = int(_settings.get("stale_session_hours", STALE_SESSION_HOURS))

    # Check if session is stale
    if is_live:
        elapsed = datetime.now(timezone.utc) - session.started_at
        if elapsed > timedelta(hours=stale_hours):
            is_live = False

    # Try to reconstruct live score from session_state
    state_dict = db.load_session_state(session.id)
    if state_dict:
        state = SessionState.from_dict(
            state_dict,
            session,
            agent_decay_seconds=int(_settings.get("agent_decay_seconds", 300)),
        )
        score = RiskScore.calculate(
            tokens=state.tokens,
            active_agents=state.active_agents,
            steps_since_confirm=state.steps_since_confirm,
            tools_per_minute=state.tools_per_minute,
            threshold=_threshold(),
            max_tokens=int(_settings.get("max_tokens", 128000)),
            max_agents=int(_settings.get("max_agents", 5)),
            max_steps=int(_settings.get("max_steps", 10)),
            max_tools_per_min=float(_settings.get("max_tools_per_min", 20.0)),
        )
    else:
        # Fallback to peak score
        score = RiskScore(
            total=session.peak_risk_score,
            context_length=0.0,
            agent_fanout=0.0,
            autonomy_depth=0.0,
            decision_velocity=0.0,
            threshold_exceeded=session.peak_risk_score >= _threshold(),
        )

    return {
        "score": {
            "total": score.total,
            "context_length": score.context_length,
            "agent_fanout": score.agent_fanout,
            "autonomy_depth": score.autonomy_depth,
            "decision_velocity": score.decision_velocity,
            "threshold_exceeded": score.threshold_exceeded,
        },
        "session": _session_to_dict(session, stale_hours),
        "is_live": is_live,
    }


def api_sessions(db: Database, params: dict) -> dict:
    """GET /api/sessions?limit=50&offset=0"""
    limit = int(params.get("limit", ["50"])[0])
    offset = int(params.get("offset", ["0"])[0])

    # Clamp
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    # Get total count
    conn = db._connect()
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    # Get paginated sessions
    conn.row_factory = __import__("sqlite3").Row
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.row_factory = None

    stale_hours = int(_settings.get("stale_session_hours", STALE_SESSION_HOURS))
    sessions = [_session_to_dict(db._row_to_session(row), stale_hours) for row in rows]

    return {"sessions": sessions, "total": total}


def api_session_detail(db: Database, session_id: str) -> dict:
    """GET /api/session/{id}"""
    session = db.get_session(session_id)
    if not session:
        session = db.find_session_by_prefix(session_id)
    if not session:
        return {"error": "Session not found"}

    events = list(db.get_session_events(session.id))
    stale_hours = int(_settings.get("stale_session_hours", STALE_SESSION_HOURS))

    # Load session state for factor data
    state_dict = db.load_session_state(session.id)

    return {
        "session": _session_to_dict(session, stale_hours),
        "events": [_event_to_dict(e) for e in events],
        "state": state_dict,
    }


def api_stats(db: Database) -> dict:
    """GET /api/stats"""
    threshold = _threshold()
    stats = db.get_stats(threshold=threshold)
    return {
        "total_sessions": stats["total_sessions"],
        "high_risk_sessions": stats["high_risk_sessions"],
        "total_tool_calls": stats["total_tool_calls"],
        "total_agent_spawns": stats["total_agent_spawns"],
        "avg_risk_score": stats["avg_risk_score"],
    }


def api_chart_data(db: Database, params: dict) -> dict:
    """GET /api/chart-data?days=30"""
    days = int(params.get("days", ["30"])[0])
    if days <= 0:
        days = 30

    conn = db._connect()
    threshold = _threshold()

    # Cutoff date
    if days >= 9999:
        cutoff = "1970-01-01"
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Daily aggregation
    rows = conn.execute(
        """SELECT
            DATE(started_at) as day,
            COUNT(*) as sessions,
            SUM(CASE WHEN peak_risk_score >= ? THEN 1 ELSE 0 END) as high_risk,
            ROUND(AVG(peak_risk_score), 1) as avg_risk,
            SUM(total_tool_calls) as tool_calls,
            SUM(total_agent_spawns) as agent_spawns
        FROM sessions
        WHERE started_at >= ?
        GROUP BY DATE(started_at)
        ORDER BY day""",
        (threshold, cutoff),
    ).fetchall()

    daily = [
        {
            "day": row[0],
            "sessions": row[1],
            "high_risk": row[2],
            "avg_risk": row[3] or 0,
            "tool_calls": row[4] or 0,
            "agent_spawns": row[5] or 0,
        }
        for row in rows
    ]

    # Tool usage — aggregate from events
    tool_rows = conn.execute(
        """SELECT
            e.tool_name,
            COUNT(*) as count,
            ROUND(AVG(e.risk_score_total), 1) as avg_risk
        FROM risk_events e
        JOIN sessions s ON e.session_id = s.id
        WHERE e.tool_name IS NOT NULL
          AND s.started_at >= ?
        GROUP BY e.tool_name
        ORDER BY count DESC
        LIMIT 15""",
        (cutoff,),
    ).fetchall()

    tool_usage = [
        {"tool": row[0], "count": row[1], "avg_risk": row[2] or 0}
        for row in tool_rows
    ]

    # Risk distribution — 10 bins (0-9, 10-19, ..., 90-100)
    distribution = [0] * 10
    score_rows = conn.execute(
        "SELECT peak_risk_score FROM sessions WHERE started_at >= ?",
        (cutoff,),
    ).fetchall()
    for (score,) in score_rows:
        if score is not None:
            bin_idx = min(9, int(score / 10))
            distribution[bin_idx] += 1

    return {
        "daily": daily,
        "tool_usage": tool_usage,
        "risk_distribution": distribution,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    """Routes GET requests to JSON API handlers or serves static HTML."""

    def log_message(self, format, *args):  # noqa: A002
        pass  # Suppress per-request stdout noise

    def do_GET(self):
        try:
            self._route()
        except Exception as exc:
            self._json_response(500, {"error": str(exc)})

    def _route(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # Serve dashboard.html at /
        if path == "/":
            html_path = script_dir / "dashboard.html"
            if not html_path.exists():
                self._json_response(404, {"error": "dashboard.html not found"})
                return
            body = html_path.read_bytes()
            self._raw_response(200, "text/html", body)
            return

        # API routes
        if not path.startswith("/api/"):
            self._json_response(404, {"error": "Not found"})
            return

        db = Database()
        try:
            if path == "/api/config":
                data = api_config()
            elif path == "/api/live":
                data = api_live(db)
            elif path == "/api/sessions":
                data = api_sessions(db, params)
            elif path.startswith("/api/session/"):
                session_id = path[len("/api/session/"):]
                if not session_id:
                    data = {"error": "Missing session ID"}
                else:
                    data = api_session_detail(db, session_id)
                    if "error" in data:
                        self._json_response(404, data)
                        return
            elif path == "/api/stats":
                data = api_stats(db)
            elif path == "/api/chart-data":
                data = api_chart_data(db, params)
            else:
                self._json_response(404, {"error": "Not found"})
                return

            self._json_response(200, data)
        finally:
            db.close()

    def _json_response(self, status: int, data: dict) -> None:
        body = json.dumps(data, default=_json_serial).encode()
        self._raw_response(status, "application/json", body)

    def _raw_response(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def serve(port: int, open_browser: bool) -> None:
    _load_config()
    ThreadingTCPServer.allow_reuse_address = True
    with ThreadingTCPServer(("", port), DashboardHandler) as server:
        url = f"http://localhost:{port}"
        print(f"RTFI Dashboard v{__version__} -> {url}")
        print(f"  Threshold: {_threshold():.0f}  |  Mode: {_settings.get('action_mode', 'alert')}")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="RTFI Web Dashboard — JSON API Server")
    parser.add_argument("--port", type=int, default=7430, help="Port to listen on (default: 7430)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser on start")
    args = parser.parse_args()
    serve(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
