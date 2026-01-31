"""Tests for the storage layer."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from rtfi.models.events import (
    EventType,
    RiskEvent,
    RiskScore,
    Session,
    SessionOutcome,
)
from rtfi.storage.database import Database


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield Database(db_path=db_path)


class TestDatabase:
    """Tests for the Database class."""

    def test_save_and_get_session(self, temp_db):
        """Test saving and retrieving a session."""
        session = Session(
            id="test-123",
            started_at=datetime.now(),
            peak_risk_score=45.5,
            total_tool_calls=10,
            total_agent_spawns=2,
            outcome=SessionOutcome.COMPLETED,
        )

        temp_db.save_session(session)
        retrieved = temp_db.get_session("test-123")

        assert retrieved is not None
        assert retrieved.id == "test-123"
        assert retrieved.peak_risk_score == 45.5
        assert retrieved.total_tool_calls == 10
        assert retrieved.outcome == SessionOutcome.COMPLETED

    def test_get_recent_sessions(self, temp_db):
        """Test getting recent sessions."""
        for i in range(5):
            session = Session(id=f"session-{i}")
            temp_db.save_session(session)

        recent = temp_db.get_recent_sessions(limit=3)
        assert len(recent) == 3

    def test_save_and_get_events(self, temp_db):
        """Test saving and retrieving events."""
        session = Session(id="test-session")
        temp_db.save_session(session)

        score = RiskScore.calculate(
            tokens=5000,
            active_agents=2,
            steps_since_confirm=3,
            tools_per_minute=5.0,
        )

        event = RiskEvent(
            session_id="test-session",
            event_type=EventType.TOOL_CALL,
            tool_name="Read",
            context_tokens=5000,
            risk_score=score,
            metadata={"file": "test.py"},
        )

        event_id = temp_db.save_event(event)
        assert event_id > 0

        events = list(temp_db.get_session_events("test-session"))
        assert len(events) == 1
        assert events[0].tool_name == "Read"
        assert events[0].risk_score.total == score.total
        assert events[0].metadata["file"] == "test.py"

    def test_get_high_risk_sessions(self, temp_db):
        """Test filtering high-risk sessions."""
        # Create sessions with varying risk
        for i, risk in enumerate([30.0, 50.0, 75.0, 90.0]):
            session = Session(id=f"session-{i}", peak_risk_score=risk)
            temp_db.save_session(session)

        high_risk = temp_db.get_high_risk_sessions(threshold=70.0)
        assert len(high_risk) == 2
        assert all(s.peak_risk_score >= 70.0 for s in high_risk)

    def test_update_session(self, temp_db):
        """Test updating an existing session."""
        session = Session(id="test-session", outcome=SessionOutcome.IN_PROGRESS)
        temp_db.save_session(session)

        # Update the session
        session.ended_at = datetime.now()
        session.outcome = SessionOutcome.COMPLETED
        session.final_risk_score = 55.0
        temp_db.save_session(session)

        retrieved = temp_db.get_session("test-session")
        assert retrieved.outcome == SessionOutcome.COMPLETED
        assert retrieved.final_risk_score == 55.0
        assert retrieved.ended_at is not None
