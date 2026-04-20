"""Tests for the dashboard JSON API server."""

import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def dashboard_server():
    """Start a dashboard server on a random port for testing."""
    tmp_dir = tempfile.mkdtemp()
    db_path = str(Path(tmp_dir) / "test.db")
    # Find a free port
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    # Pre-populate the DB with a session
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from rtfi_core import Database, EventType, RiskEvent, RiskScore, Session, SessionOutcome

    db = Database(db_path=Path(db_path))
    session = Session(
        id="test-dash-001",
        peak_risk_score=45.5,
        total_tool_calls=10,
        total_agent_spawns=2,
        outcome=SessionOutcome.COMPLETED,
    )
    db.save_session(session)
    score = RiskScore.calculate(
        tokens=5000, active_agents=1, steps_since_confirm=3, tools_per_minute=5.0
    )
    event = RiskEvent(
        session_id="test-dash-001",
        event_type=EventType.TOOL_CALL,
        tool_name="Read",
        context_tokens=5000,
        risk_score=score,
    )
    db.save_event(event)
    db.close()

    hook_script = str(Path(__file__).parent.parent / "scripts" / "rtfi_dashboard.py")
    import os

    env = {**os.environ, "RTFI_DB_PATH": db_path}

    proc = subprocess.Popen(
        ["python3", hook_script, "--no-browser", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1.5)  # Wait for server to start

    yield f"http://localhost:{port}", db_path

    proc.terminate()
    proc.wait(timeout=5)
    import shutil

    shutil.rmtree(tmp_dir, ignore_errors=True)


def _get(url: str) -> dict:
    """Fetch a JSON API endpoint."""
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


class TestDashboardAPI:
    def test_api_config_returns_configured_threshold(self, dashboard_server):
        """AC-6: /api/config returns actual configured threshold."""
        base_url, _ = dashboard_server
        data = _get(f"{base_url}/api/config")
        assert "threshold" in data
        assert isinstance(data["threshold"], (int, float))
        assert "version" in data
        assert data["version"] == "1.2.0"

    def test_api_live_returns_valid_structure(self, dashboard_server):
        base_url, _ = dashboard_server
        data = _get(f"{base_url}/api/live")
        assert "is_live" in data
        # Session is completed, so either score is null or has total
        if data["score"] is not None:
            assert "total" in data["score"]

    def test_api_sessions_pagination(self, dashboard_server):
        base_url, _ = dashboard_server
        data = _get(f"{base_url}/api/sessions?limit=10&offset=0")
        assert "sessions" in data
        assert "total" in data
        assert isinstance(data["sessions"], list)

    def test_api_session_detail_includes_events(self, dashboard_server):
        base_url, _ = dashboard_server
        data = _get(f"{base_url}/api/session/test-dash-001")
        assert "session" in data
        assert "events" in data
        assert data["session"]["id"] == "test-dash-001"
        assert len(data["events"]) >= 1

    def test_api_stats_uses_threshold(self, dashboard_server):
        base_url, _ = dashboard_server
        data = _get(f"{base_url}/api/stats")
        assert "total_sessions" in data
        assert "high_risk_sessions" in data
        assert data["total_sessions"] >= 1

    def test_api_chart_data_structure(self, dashboard_server):
        base_url, _ = dashboard_server
        data = _get(f"{base_url}/api/chart-data?days=30")
        assert "daily" in data
        assert "tool_usage" in data
        assert "risk_distribution" in data
        assert isinstance(data["risk_distribution"], list)
        assert len(data["risk_distribution"]) == 10  # 10 bins

    def test_static_html_served(self, dashboard_server):
        base_url, _ = dashboard_server
        with urllib.request.urlopen(f"{base_url}/", timeout=5) as resp:
            content = resp.read().decode()
            assert "<!DOCTYPE html>" in content
            assert "chart.js" in content.lower() or "Chart" in content

    def test_api_sessions_includes_compliance_fields(self, dashboard_server):
        """/api/sessions must expose compliance_violated + compliance_failures."""
        base_url, _ = dashboard_server
        data = _get(f"{base_url}/api/sessions?limit=10")
        assert data["sessions"], "expected at least one session"
        row = data["sessions"][0]
        assert "compliance_violated" in row
        assert "expected_artifacts" in row
        assert "compliance_failures" in row
        assert isinstance(row["compliance_violated"], bool)

    def test_api_compliance_endpoint(self, dashboard_server):
        """/api/compliance returns displacement×compliance correlation metadata."""
        base_url, _ = dashboard_server
        data = _get(f"{base_url}/api/compliance?threshold=0.7")
        assert "displacement_threshold" in data
        assert "high_displacement_sessions" in data
        assert "compliance_failures_among_high_displacement" in data
        assert "ratio" in data  # May be None if denominator is 0
        assert data["displacement_threshold"] == 0.7
