"""Tests for rtfi_core.py - models, scoring engine, database, and config."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from rtfi_core import (
    Database,
    EventType,
    RiskEngine,
    RiskEvent,
    RiskScore,
    Session,
    SessionOutcome,
    SessionState,
    estimate_tokens,
    get_expected_artifacts,
    load_settings,
    risk_level,
)

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield Database(db_path=db_path)


# ── RiskScore Tests ──────────────────────────────────────────────────────


class TestRiskScore:
    def test_calculate_all_zero(self):
        score = RiskScore.calculate(
            tokens=0, active_agents=0, steps_since_confirm=0, tools_per_minute=0.0
        )
        assert score.total == 0.0
        assert not score.threshold_exceeded

    def test_calculate_all_max(self):
        score = RiskScore.calculate(
            tokens=200000,
            active_agents=10,
            steps_since_confirm=20,
            tools_per_minute=40.0,
            skill_tokens_injected=5000,
            instruction_tokens=2500,
        )
        assert score.total == 100.0
        assert score.threshold_exceeded

    def test_calculate_all_max_without_displacement(self):
        """Without displacement params, max is 90 (displacement weight=0.10 contributes 0)."""
        score = RiskScore.calculate(
            tokens=200000,
            active_agents=10,
            steps_since_confirm=20,
            tools_per_minute=40.0,
        )
        assert score.total == 90.0
        assert score.threshold_exceeded

    def test_threshold_boundary(self):
        # Just below default threshold
        score_below = RiskScore.calculate(
            tokens=0,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
            threshold=70.0,
        )
        assert not score_below.threshold_exceeded

        # At threshold — all 5 factors maxed
        score_at = RiskScore.calculate(
            tokens=128000,
            active_agents=5,
            steps_since_confirm=10,
            tools_per_minute=20.0,
            skill_tokens_injected=5000,
            instruction_tokens=2500,
            threshold=100.0,
        )
        assert score_at.threshold_exceeded

    def test_custom_ceilings(self):
        score = RiskScore.calculate(
            tokens=32000,
            active_agents=1,
            steps_since_confirm=2,
            tools_per_minute=5.0,
            max_tokens=32000,
            max_agents=1,
            max_steps=2,
            max_tools_per_min=5.0,
            skill_tokens_injected=5000,
            instruction_tokens=2500,
        )
        assert score.total == 100.0

    def test_agent_fanout_highest_weight(self):
        score_agents = RiskScore.calculate(
            tokens=0, active_agents=10, steps_since_confirm=0, tools_per_minute=0.0
        )
        score_context = RiskScore.calculate(
            tokens=200000, active_agents=0, steps_since_confirm=0, tools_per_minute=0.0
        )
        assert score_agents.total > score_context.total

    def test_displacement_zero_when_no_instruction_tokens(self):
        score = RiskScore.calculate(
            tokens=50000,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
            skill_tokens_injected=10000,
            instruction_tokens=0,
        )
        assert score.instruction_displacement == 0.0

    def test_displacement_zero_when_no_skill_tokens(self):
        score = RiskScore.calculate(
            tokens=50000,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
            skill_tokens_injected=0,
            instruction_tokens=2500,
        )
        assert score.instruction_displacement == 0.0

    def test_displacement_scales_with_skill_tokens(self):
        score_low = RiskScore.calculate(
            tokens=50000,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
            skill_tokens_injected=500,
            instruction_tokens=2500,
        )
        score_high = RiskScore.calculate(
            tokens=50000,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
            skill_tokens_injected=2500,
            instruction_tokens=2500,
        )
        assert score_low.instruction_displacement == 0.2
        assert score_high.instruction_displacement == 1.0
        assert score_high.total > score_low.total

    def test_displacement_caps_at_one(self):
        score = RiskScore.calculate(
            tokens=50000,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
            skill_tokens_injected=50000,
            instruction_tokens=2500,
        )
        assert score.instruction_displacement == 1.0

    def test_displacement_weight_is_010(self):
        """Displacement at 1.0 with all other factors at 0 should contribute exactly 10 points."""
        score = RiskScore.calculate(
            tokens=0,
            active_agents=0,
            steps_since_confirm=0,
            tools_per_minute=0.0,
            skill_tokens_injected=5000,
            instruction_tokens=2500,
        )
        assert score.total == 10.0

    def test_all_five_weights_sum_to_one(self):
        """Weight distribution must sum to 1.0."""
        score = RiskScore.calculate(
            tokens=128000,
            active_agents=5,
            steps_since_confirm=10,
            tools_per_minute=20.0,
            skill_tokens_injected=5000,
            instruction_tokens=2500,
        )
        assert score.total == 100.0

    def test_backward_compat_default_displacement(self):
        """Calling calculate() without displacement params should still work."""
        score = RiskScore.calculate(
            tokens=64000,
            active_agents=2,
            steps_since_confirm=5,
            tools_per_minute=10.0,
        )
        assert score.instruction_displacement == 0.0
        assert score.total > 0


# ── Database Tests ───────────────────────────────────────────────────────


class TestDatabase:
    def test_save_and_get_session(self, temp_db):
        session = Session(
            id="test-123",
            peak_risk_score=45.5,
            total_tool_calls=10,
            outcome=SessionOutcome.COMPLETED,
        )
        temp_db.save_session(session)
        retrieved = temp_db.get_session("test-123")
        assert retrieved is not None
        assert retrieved.id == "test-123"
        assert retrieved.peak_risk_score == 45.5
        assert retrieved.outcome == SessionOutcome.COMPLETED

    def test_save_session_preserves_session_state(self, temp_db):
        """AC-1: save_session() must not clear session_state on updates."""
        session = Session(id="test-ac1")
        state = {
            "tokens": 5000,
            "steps_since_confirm": 3,
            "tool_timestamps": [],
            "agent_spawn_timestamps": [],
        }
        temp_db.save_session(session, session_state=state)

        # Update session without providing session_state
        session.total_tool_calls = 5
        session.peak_risk_score = 42.0
        temp_db.save_session(session)  # No session_state param

        # session_state should still be there
        loaded = temp_db.load_session_state("test-ac1")
        assert loaded is not None
        assert loaded["tokens"] == 5000
        assert loaded["steps_since_confirm"] == 3

    def test_save_session_state_roundtrip(self, temp_db):
        session = Session(id="test-rt")
        temp_db.save_session(session)
        state = {
            "tokens": 10000,
            "steps_since_confirm": 5,
            "tool_timestamps": ["2026-01-01T00:00:00+00:00"],
            "agent_spawn_timestamps": [],
        }
        temp_db.save_session_state("test-rt", state)
        loaded = temp_db.load_session_state("test-rt")
        assert loaded == state

    def test_get_recent_sessions_ordering(self, temp_db):
        for i in range(5):
            session = Session(id=f"session-{i}")
            temp_db.save_session(session)
        recent = temp_db.get_recent_sessions(limit=3)
        assert len(recent) == 3

    def test_get_high_risk_sessions_uses_threshold_param(self, temp_db):
        """AC-6 (partial): get_high_risk_sessions uses the provided threshold."""
        for i, risk in enumerate([30.0, 50.0, 75.0, 90.0]):
            session = Session(id=f"session-{i}", peak_risk_score=risk)
            temp_db.save_session(session)

        # Default threshold 70
        high_risk = temp_db.get_high_risk_sessions(threshold=70.0)
        assert len(high_risk) == 2

        # Custom threshold 40
        high_risk = temp_db.get_high_risk_sessions(threshold=40.0)
        assert len(high_risk) == 3

    def test_get_stats_uses_threshold_param(self, temp_db):
        for i, risk in enumerate([30.0, 50.0, 75.0, 90.0]):
            session = Session(id=f"session-{i}", peak_risk_score=risk)
            temp_db.save_session(session)

        stats = temp_db.get_stats(threshold=70.0)
        assert stats["high_risk_sessions"] == 2

        stats = temp_db.get_stats(threshold=40.0)
        assert stats["high_risk_sessions"] == 3

    def test_purge_old_sessions(self, temp_db):
        old_session = Session(
            id="old-session", started_at=datetime.now(timezone.utc) - timedelta(days=100)
        )
        new_session = Session(id="new-session")
        temp_db.save_session(old_session)
        temp_db.save_session(new_session)

        deleted = temp_db.purge_old_sessions(days=90)
        assert deleted == 1
        assert temp_db.get_session("old-session") is None
        assert temp_db.get_session("new-session") is not None

    def test_schema_includes_all_columns(self, temp_db):
        """Gap 25: SCHEMA should include session_state and project_dir."""
        import sqlite3

        conn = sqlite3.connect(temp_db.db_path)
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "session_state" in columns
        assert "project_dir" in columns
        assert "compliance_violated" in columns

    def test_compliance_violated_column_persists(self, temp_db):
        """Session.compliance_violated must round-trip through save/get."""
        session = Session(id="test-comp", compliance_violated=True)
        temp_db.save_session(session)
        loaded = temp_db.get_session("test-comp")
        assert loaded is not None
        assert loaded.compliance_violated is True

        clean = Session(id="test-clean", compliance_violated=False)
        temp_db.save_session(clean)
        loaded_clean = temp_db.get_session("test-clean")
        assert loaded_clean.compliance_violated is False

    def test_migration_adds_compliance_column_to_legacy_db(self, tmp_path):
        """Opening a DB that predates the compliance column should upgrade it."""
        import sqlite3

        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                instruction_source TEXT,
                instruction_hash TEXT,
                final_risk_score REAL,
                peak_risk_score REAL DEFAULT 0,
                total_tool_calls INTEGER DEFAULT 0,
                total_agent_spawns INTEGER DEFAULT 0,
                outcome TEXT DEFAULT 'in_progress'
            );
            """
        )
        conn.execute(
            "INSERT INTO sessions (id, started_at, outcome) VALUES (?, ?, ?)",
            ("legacy-1", "2026-01-01T00:00:00+00:00", "completed"),
        )
        conn.commit()
        conn.close()

        db = Database(db_path=db_path)
        loaded = db.get_session("legacy-1")
        assert loaded is not None
        assert loaded.compliance_violated is False  # Default for pre-existing row
        db.close()

    def test_find_active_session(self, temp_db):
        """H2: fallback session lookup by project_dir."""
        session = Session(
            id="active-1", project_dir="/test/project", outcome=SessionOutcome.IN_PROGRESS
        )
        temp_db.save_session(session)
        found = temp_db.find_active_session("/test/project")
        assert found is not None
        assert found.id == "active-1"

    def test_save_and_get_events(self, temp_db):
        session = Session(id="test-events")
        temp_db.save_session(session)
        score = RiskScore.calculate(
            tokens=5000, active_agents=2, steps_since_confirm=3, tools_per_minute=5.0
        )
        event = RiskEvent(
            session_id="test-events",
            event_type=EventType.TOOL_CALL,
            tool_name="Read",
            context_tokens=5000,
            risk_score=score,
            metadata={"file": "test.py"},
        )
        event_id = temp_db.save_event(event)
        assert event_id > 0
        events = list(temp_db.get_session_events("test-events"))
        assert len(events) == 1
        assert events[0].tool_name == "Read"
        assert events[0].risk_score.total == score.total


# ── Engine Tests ─────────────────────────────────────────────────────────


class TestRiskEngine:
    def test_session_lifecycle(self):
        engine = RiskEngine()
        session = Session(id="test-session")
        engine.start_session(session)
        assert engine.get_session("test-session") is not None
        ended = engine.end_session("test-session")
        assert ended is not None
        assert ended.ended_at is not None
        assert engine.get_session("test-session") is None

    def test_process_tool_call_increments_state(self):
        engine = RiskEngine()
        session = Session(id="test-session")
        engine.start_session(session)
        for i in range(5):
            event = RiskEvent(
                session_id="test-session", event_type=EventType.TOOL_CALL, tool_name=f"Tool{i}"
            )
            engine.process_event(event)
        state = engine.get_session_state("test-session")
        assert state.steps_since_confirm == 5
        assert state.session.total_tool_calls == 5

    def test_process_agent_spawn_adds_timestamp(self):
        engine = RiskEngine()
        session = Session(id="test-session")
        engine.start_session(session)
        for i in range(3):
            event = RiskEvent(
                session_id="test-session", event_type=EventType.AGENT_SPAWN, tool_name="Task"
            )
            engine.process_event(event)
        score = engine.get_current_score("test-session")
        assert score.agent_fanout == 0.6
        assert score.total >= 18.0

    def test_process_checkpoint_resets_autonomy(self):
        """AC-8: Checkpoint resets steps_since_confirm to 0."""
        engine = RiskEngine()
        session = Session(id="test-session")
        engine.start_session(session)
        for i in range(5):
            event = RiskEvent(
                session_id="test-session", event_type=EventType.TOOL_CALL, tool_name=f"Tool{i}"
            )
            engine.process_event(event)
        mid_score = engine.get_current_score("test-session")
        assert mid_score.autonomy_depth > 0

        checkpoint = RiskEvent(session_id="test-session", event_type=EventType.CHECKPOINT)
        engine.process_event(checkpoint)
        after = engine.get_current_score("test-session")
        assert after.autonomy_depth == 0.0

    def test_session_state_to_dict_from_dict_roundtrip(self):
        session = Session(id="test-rt")
        state = SessionState(session=session, tokens=5000, steps_since_confirm=3)
        state.tool_calls_timestamps.append(datetime.now(timezone.utc))
        state.agent_spawn_timestamps.append(datetime.now(timezone.utc))

        d = state.to_dict()
        restored = SessionState.from_dict(d, session)
        assert restored.tokens == 5000
        assert restored.steps_since_confirm == 3
        assert len(restored.tool_calls_timestamps) == 1
        assert len(restored.agent_spawn_timestamps) == 1

    def test_session_state_displacement_fields_roundtrip(self):
        session = Session(id="test-disp-rt")
        state = SessionState(
            session=session,
            instruction_tokens=2500,
            skill_tokens_injected=8000,
            last_context_tokens=50000,
        )
        d = state.to_dict()
        assert d["instruction_tokens"] == 2500
        assert d["skill_tokens_injected"] == 8000
        assert d["last_context_tokens"] == 50000

        restored = SessionState.from_dict(d, session)
        assert restored.instruction_tokens == 2500
        assert restored.skill_tokens_injected == 8000
        assert restored.last_context_tokens == 50000

    def test_session_state_displacement_defaults_backward_compat(self):
        """Legacy state dicts without displacement fields should deserialize safely."""
        session = Session(id="test-legacy")
        legacy_dict = {
            "tokens": 3000,
            "steps_since_confirm": 2,
            "tool_timestamps": [],
            "agent_spawn_timestamps": [],
        }
        restored = SessionState.from_dict(legacy_dict, session)
        assert restored.instruction_tokens == 0
        assert restored.skill_tokens_injected == 0
        assert restored.last_context_tokens == 0

    def test_backward_compat_missing_artifact_fields(self):
        """Legacy state dicts without artifact fields should deserialize to empty lists."""
        session = Session(id="test-artifact-legacy")
        legacy_dict = {
            "tokens": 3000,
            "steps_since_confirm": 2,
            "tool_timestamps": [],
            "agent_spawn_timestamps": [],
        }
        restored = SessionState.from_dict(legacy_dict, session)
        assert restored.expected_artifacts == []
        assert restored.observed_artifacts == []
        assert restored.compliance_failures == []

    def test_session_state_artifact_fields_roundtrip(self):
        """Round-trip non-empty artifact lists through to_dict/from_dict."""
        session = Session(id="test-artifact-rt")
        state = SessionState(
            session=session,
            expected_artifacts=["/proj/CONTEXT.md", "/proj/CHANGELOG.md"],
            observed_artifacts=["/proj/CONTEXT.md"],
            compliance_failures=["/proj/CHANGELOG.md"],
        )
        d = state.to_dict()
        assert d["expected_artifacts"] == ["/proj/CONTEXT.md", "/proj/CHANGELOG.md"]
        assert d["observed_artifacts"] == ["/proj/CONTEXT.md"]
        assert d["compliance_failures"] == ["/proj/CHANGELOG.md"]

        restored = SessionState.from_dict(d, session)
        assert restored.expected_artifacts == ["/proj/CONTEXT.md", "/proj/CHANGELOG.md"]
        assert restored.observed_artifacts == ["/proj/CONTEXT.md"]
        assert restored.compliance_failures == ["/proj/CHANGELOG.md"]

    def test_active_agents_decay_window(self):
        """AC-3 (partial): Agents older than decay window don't count."""
        session = Session(id="test-decay")
        state = SessionState(session=session, agent_decay_seconds=300)
        # Add an old timestamp (10 minutes ago)
        old_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
        state.agent_spawn_timestamps.append(old_ts)
        # Add a recent timestamp
        state.agent_spawn_timestamps.append(datetime.now(timezone.utc))
        assert state.active_agents == 1  # Only the recent one

    def test_active_agents_custom_decay_seconds(self):
        """AC-3: Custom decay window is respected."""
        session = Session(id="test-custom-decay")
        state = SessionState(session=session, agent_decay_seconds=60)  # 1 minute
        # Add a 2-minute-old timestamp
        old_ts = datetime.now(timezone.utc) - timedelta(minutes=2)
        state.agent_spawn_timestamps.append(old_ts)
        assert state.active_agents == 0  # Decayed with 60s window

        state2 = SessionState(session=session, agent_decay_seconds=300)  # 5 minutes
        state2.agent_spawn_timestamps.append(old_ts)
        assert state2.active_agents == 1  # Still active with 300s window

    def test_prune_old_timestamps(self):
        session = Session(id="test-prune")
        state = SessionState(session=session)
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        for _ in range(110):
            state.tool_calls_timestamps.append(old)
        state.prune_old_timestamps()
        assert len(state.tool_calls_timestamps) < 110

    def test_peak_risk_score_tracking(self):
        engine = RiskEngine()
        session = Session(id="test-peak")
        engine.start_session(session)
        for i in range(8):
            event = RiskEvent(
                session_id="test-peak", event_type=EventType.TOOL_CALL, tool_name=f"Tool{i}"
            )
            engine.process_event(event)
        peak = engine.get_session("test-peak").peak_risk_score
        assert peak > 0

        checkpoint = RiskEvent(session_id="test-peak", event_type=EventType.CHECKPOINT)
        engine.process_event(checkpoint)
        assert engine.get_session("test-peak").peak_risk_score == peak

    def test_get_session_state_public_api(self):
        engine = RiskEngine()
        session = Session(id="test-api")
        engine.start_session(session)
        state = engine.get_session_state("test-api")
        assert state is not None
        assert state.session.id == "test-api"
        assert engine.get_session_state("nonexistent") is None

    def test_threshold_callback(self):
        callback_fired = []

        def on_exceeded(session, score):
            callback_fired.append((session.id, score.total))

        engine = RiskEngine(threshold=20.0, on_threshold_exceeded=on_exceeded)
        session = Session(id="test-cb")
        engine.start_session(session)
        for i in range(5):
            event = RiskEvent(
                session_id="test-cb", event_type=EventType.AGENT_SPAWN, tool_name="Task"
            )
            engine.process_event(event)
        assert len(callback_fired) > 0


# ── Config Tests ─────────────────────────────────────────────────────────


class TestConfig:
    def test_load_settings_defaults(self):
        import os

        # Clear relevant env vars
        with pytest.MonkeyPatch.context() as mp:
            for k in list(os.environ):
                if k.startswith("RTFI_"):
                    mp.delenv(k, raising=False)
            settings = load_settings(log_dir=Path(tempfile.mkdtemp()))
        assert settings["threshold"] == 70.0
        assert settings["action_mode"] == "alert"
        assert settings["retention_days"] == 90

    def test_load_settings_env_override(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RTFI_THRESHOLD", "50.0")
            settings = load_settings(log_dir=Path(tempfile.mkdtemp()))
        assert settings["threshold"] == 50.0

    def test_load_settings_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.env"
            config_path.write_text("threshold=42.0\naction_mode=block\n")
            with pytest.MonkeyPatch.context() as mp:
                for k in list(os.environ):
                    if k.startswith("RTFI_"):
                        mp.delenv(k, raising=False)
                settings = load_settings(log_dir=Path(tmpdir))
            assert settings["threshold"] == 42.0
            assert settings["action_mode"] == "block"

    def test_load_settings_agent_decay_seconds(self):
        """AC-3: RTFI_AGENT_DECAY_SECONDS config is loaded."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RTFI_AGENT_DECAY_SECONDS", "120")
            settings = load_settings(log_dir=Path(tempfile.mkdtemp()))
        assert settings["agent_decay_seconds"] == 120

    def test_get_expected_artifacts_unset_is_empty(self, tmp_path):
        """Unset RTFI_EXPECTED_ARTIFACTS disables enforcement (empty list)."""
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("RTFI_EXPECTED_ARTIFACTS", raising=False)
            assert get_expected_artifacts(tmp_path) == []

    def test_get_expected_artifacts_resolves_relative_paths(self, tmp_path):
        """Relative entries resolve against project_dir; multiple entries split on ':'."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RTFI_EXPECTED_ARTIFACTS", "CONTEXT.md:docs/CHANGELOG.md")
            paths = get_expected_artifacts(tmp_path)
        assert len(paths) == 2
        assert paths[0] == str((tmp_path / "CONTEXT.md").resolve())
        assert paths[1] == str((tmp_path / "docs" / "CHANGELOG.md").resolve())

    def test_get_expected_artifacts_absolute_path_preserved(self, tmp_path):
        """Absolute entries stay absolute."""
        abs_path = str(tmp_path / "CONTEXT.md")
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RTFI_EXPECTED_ARTIFACTS", abs_path)
            paths = get_expected_artifacts(tmp_path)
        assert paths == [abs_path]

    def test_get_expected_artifacts_empty_string_disables(self, tmp_path):
        """Empty env var string → empty list (explicit opt-out)."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RTFI_EXPECTED_ARTIFACTS", "")
            assert get_expected_artifacts(tmp_path) == []


# ── Risk Level Tests ─────────────────────────────────────────────────────


class TestEstimateTokens:
    def test_basic_estimation(self):
        assert estimate_tokens("a" * 400) == 100

    def test_empty_string_returns_one(self):
        assert estimate_tokens("") == 1

    def test_short_string(self):
        assert estimate_tokens("hi") == 1

    def test_realistic_claude_md(self):
        text = "x" * 10000  # ~10KB CLAUDE.md
        tokens = estimate_tokens(text)
        assert 2000 <= tokens <= 3000


class TestRiskLevel:
    def test_risk_level_normal(self):
        assert risk_level(0) == "NORMAL"
        assert risk_level(29) == "NORMAL"

    def test_risk_level_elevated(self):
        assert risk_level(30) == "ELEVATED"
        assert risk_level(69) == "ELEVATED"

    def test_risk_level_high(self):
        assert risk_level(70) == "HIGH RISK"
        assert risk_level(100) == "HIGH RISK"
