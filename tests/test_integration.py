"""Integration tests that invoke hook_handler.py as subprocess (matching production).

Each test invokes the hook handler as a separate process, exactly like Claude Code does
in production. This validates cross-process state persistence (C1) and session
finalization (C2).
"""

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest


class TestCrossProcessStatePersistence:
    """Integration tests that invoke hook_handler.py as subprocess (matching production)."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        self.session_id = f"test-{uuid.uuid4().hex[:8]}"
        self.env = {
            **os.environ,
            "RTFI_DB_PATH": self.db_path,
            "RTFI_SESSION_ID": self.session_id,
        }

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _invoke_hook(self, hook_type: str, input_data: dict) -> dict:
        result = subprocess.run(
            ["python3", "scripts/hook_handler.py", hook_type],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            env=self.env,
            timeout=10,
        )
        if result.returncode != 0 and result.stderr:
            # Log stderr for debugging but don't fail — hook logs go to stderr
            pass
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def test_risk_score_accumulates_across_processes(self):
        """Risk score should increase with each tool call (separate process)."""
        scores = []
        for i in range(5):
            result = self._invoke_hook(
                "pre_tool_use", {"tool_name": "Read", "context_tokens": 50000}
            )
            assert result.get("continue") is True
            # Read score from database
            import sqlite3

            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT risk_score_total FROM risk_events WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            conn.close()
            if row and row[0] is not None:
                scores.append(row[0])

        # Scores should be monotonically increasing (autonomy_depth grows)
        assert len(scores) == 5, f"Expected 5 scores, got {len(scores)}"
        assert scores == sorted(scores), f"Scores not monotonically increasing: {scores}"
        assert scores[-1] > scores[0], f"Last score {scores[-1]} not > first {scores[0]}"

    def test_agent_fanout_accumulates(self):
        """Agent spawns should accumulate across processes."""
        for i in range(3):
            self._invoke_hook("pre_tool_use", {"tool_name": "Task"})

        import sqlite3

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT session_state FROM sessions WHERE id=?", (self.session_id,)
        ).fetchone()
        conn.close()

        assert row and row[0], "Session state not persisted"
        state = json.loads(row[0])
        agent_count = len(state.get("agent_spawn_timestamps", []))
        assert agent_count == 3, f"Expected 3 agent timestamps, got {agent_count}"

    def test_stop_produces_session_summary(self):
        """handle_stop should return a summary with non-zero stats."""
        # Make some tool calls first
        for i in range(3):
            self._invoke_hook("pre_tool_use", {"tool_name": "Read", "context_tokens": 50000})

        result = self._invoke_hook("stop", {})

        assert "systemMessage" in result, f"No systemMessage in stop result: {result}"
        assert "Peak risk" in result["systemMessage"]
        assert "Tool calls: 3" in result["systemMessage"]

    def test_stop_sets_session_completed(self):
        """handle_stop should set session outcome to completed."""
        self._invoke_hook("pre_tool_use", {"tool_name": "Read"})
        self._invoke_hook("stop", {})

        import sqlite3

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT outcome, ended_at FROM sessions WHERE id=?", (self.session_id,)
        ).fetchone()
        conn.close()

        assert row is not None, "Session not found in DB"
        assert row[0] == "completed", f"Expected 'completed', got {row[0]}"
        assert row[1] is not None, "ended_at not set"

    def test_session_auto_start_on_pre_tool_use(self):
        """pre_tool_use should auto-create session if ID not in DB."""
        # Use a new session ID that has no DB entry
        fresh_id = f"fresh-{uuid.uuid4().hex[:8]}"
        self.env["RTFI_SESSION_ID"] = fresh_id

        result = self._invoke_hook("pre_tool_use", {"tool_name": "Read"})

        assert result.get("continue") is True

        import sqlite3

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT id FROM sessions WHERE id=?", (fresh_id,)).fetchone()
        conn.close()
        assert row is not None, "Session not auto-created"

    def test_malformed_env_vars_dont_crash(self):
        """Malformed environment variables should not crash the hook."""
        self.env["RTFI_THRESHOLD"] = "abc"
        self.env["RTFI_RETENTION_DAYS"] = "xyz"

        result = self._invoke_hook("pre_tool_use", {"tool_name": "Read"})

        assert result.get("continue") is True

    def test_tools_per_minute_reflects_actual_rate(self):
        """tools_per_minute should reflect tool calls within the last 60 seconds."""
        # Make 5 rapid tool calls
        for i in range(5):
            self._invoke_hook("pre_tool_use", {"tool_name": "Read"})

        import sqlite3

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT session_state FROM sessions WHERE id=?", (self.session_id,)
        ).fetchone()
        conn.close()

        state = json.loads(row[0])
        assert len(state["tool_timestamps"]) == 5
