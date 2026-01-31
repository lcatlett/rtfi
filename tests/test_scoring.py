"""Tests for the risk scoring engine."""

import pytest

from rtfi.models.events import EventType, RiskEvent, RiskScore, Session
from rtfi.scoring.engine import RiskEngine


class TestRiskScore:
    """Tests for RiskScore calculation."""

    def test_zero_state_gives_zero_score(self):
        """Empty session should have zero risk."""
        score = RiskScore.calculate(
            tokens=0,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
        )
        assert score.total == 0.0
        assert not score.threshold_exceeded

    def test_max_values_give_100_score(self):
        """Maxed out factors should give 100 risk."""
        score = RiskScore.calculate(
            tokens=200000,  # > 128k
            active_agents=10,  # > 5
            steps_since_confirm=20,  # > 10
            tools_per_minute=40.0,  # > 20
        )
        assert score.total == 100.0
        assert score.threshold_exceeded

    def test_agent_fanout_has_highest_weight(self):
        """Agent fanout should contribute most to score."""
        # Only agent fanout maxed
        score_agents = RiskScore.calculate(
            tokens=0,
            active_agents=10,
            steps_since_confirm=0,
            tools_per_minute=0.0,
        )

        # Only context length maxed
        score_context = RiskScore.calculate(
            tokens=200000,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
        )

        # Agent fanout (0.30 weight) > context (0.25 weight)
        assert score_agents.total > score_context.total

    def test_threshold_customization(self):
        """Custom threshold should work correctly."""
        score = RiskScore.calculate(
            tokens=64000,
            active_agents=2,
            steps_since_confirm=5,
            tools_per_minute=10.0,
            threshold=30.0,  # Lower threshold
        )
        assert score.threshold_exceeded


class TestRiskEngine:
    """Tests for the RiskEngine."""

    def test_session_lifecycle(self):
        """Test starting and ending sessions."""
        engine = RiskEngine()
        session = Session(id="test-session")

        engine.start_session(session)
        assert engine.get_session("test-session") is not None

        ended = engine.end_session("test-session")
        assert ended is not None
        assert ended.ended_at is not None
        assert engine.get_session("test-session") is None

    def test_tool_call_increases_risk(self):
        """Tool calls should increase risk score."""
        engine = RiskEngine()
        session = Session(id="test-session")
        engine.start_session(session)

        initial_score = engine.get_current_score("test-session")
        assert initial_score.total == 0.0

        # Process tool calls
        for i in range(5):
            event = RiskEvent(
                session_id="test-session",
                event_type=EventType.TOOL_CALL,
                tool_name=f"Tool{i}",
            )
            engine.process_event(event)

        final_score = engine.get_current_score("test-session")
        assert final_score.total > initial_score.total

    def test_agent_spawn_increases_risk(self):
        """Agent spawns should significantly increase risk."""
        engine = RiskEngine()
        session = Session(id="test-session")
        engine.start_session(session)

        # Spawn multiple agents
        for i in range(3):
            event = RiskEvent(
                session_id="test-session",
                event_type=EventType.AGENT_SPAWN,
                tool_name="Task",
            )
            engine.process_event(event)

        score = engine.get_current_score("test-session")
        # 3 agents / 5 max * 0.30 weight * 100 = 18
        assert score.agent_fanout == 0.6
        assert score.total >= 18.0

    def test_checkpoint_resets_autonomy(self):
        """Checkpoints should reset autonomy depth."""
        engine = RiskEngine()
        session = Session(id="test-session")
        engine.start_session(session)

        # Build up autonomy
        for i in range(5):
            event = RiskEvent(
                session_id="test-session",
                event_type=EventType.TOOL_CALL,
                tool_name=f"Tool{i}",
            )
            engine.process_event(event)

        mid_score = engine.get_current_score("test-session")
        assert mid_score.autonomy_depth > 0

        # Checkpoint
        checkpoint = RiskEvent(
            session_id="test-session",
            event_type=EventType.CHECKPOINT,
        )
        engine.process_event(checkpoint)

        after_checkpoint = engine.get_current_score("test-session")
        assert after_checkpoint.autonomy_depth == 0.0

    def test_threshold_callback(self):
        """Callback should fire when threshold exceeded."""
        callback_fired = []

        def on_exceeded(session, score):
            callback_fired.append((session.id, score.total))

        engine = RiskEngine(threshold=20.0, on_threshold_exceeded=on_exceeded)
        session = Session(id="test-session")
        engine.start_session(session)

        # Trigger high risk
        for i in range(5):
            event = RiskEvent(
                session_id="test-session",
                event_type=EventType.AGENT_SPAWN,
                tool_name="Task",
            )
            engine.process_event(event)

        assert len(callback_fired) > 0
        assert callback_fired[0][0] == "test-session"

    def test_peak_score_tracking(self):
        """Peak score should be tracked correctly."""
        engine = RiskEngine()
        session = Session(id="test-session")
        engine.start_session(session)

        # Build up risk with tool calls (increases autonomy_depth)
        for i in range(8):
            event = RiskEvent(
                session_id="test-session",
                event_type=EventType.TOOL_CALL,
                tool_name=f"Tool{i}",
            )
            engine.process_event(event)

        peak = engine.get_session("test-session").peak_risk_score
        assert peak > 0  # Should have some risk

        # Checkpoint resets autonomy_depth, reducing current score
        checkpoint = RiskEvent(
            session_id="test-session",
            event_type=EventType.CHECKPOINT,
        )
        engine.process_event(checkpoint)

        current = engine.get_current_score("test-session")
        session = engine.get_session("test-session")

        # Peak should be preserved
        assert session.peak_risk_score == peak
        # Current should be lower (autonomy reset to 0)
        assert current.autonomy_depth == 0.0


class TestRiskEvent:
    """Tests for RiskEvent model."""

    def test_event_creation(self):
        """Test basic event creation."""
        event = RiskEvent(
            session_id="test",
            event_type=EventType.TOOL_CALL,
            tool_name="Read",
            context_tokens=5000,
        )
        assert event.session_id == "test"
        assert event.event_type == EventType.TOOL_CALL
        assert event.timestamp is not None
