"""Tests for the hook handler — no importlib.reload, uses direct function calls."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from hook_handler import (
    handle_post_tool_use,
    handle_pre_tool_use,
    handle_session_start,
    handle_stop,
    validate_env_file_path,
    validate_hook_data,
)


# ── Input Validation Tests ───────────────────────────────────────────────


class TestValidateHookData:
    def test_valid_hook_data(self):
        data = {"tool_name": "Read", "context_tokens": 5000, "session_id": "abc123"}
        result = validate_hook_data(data)
        assert result["tool_name"] == "Read"
        assert result["context_tokens"] == 5000
        assert result["session_id"] == "abc123"

    def test_missing_fields_use_defaults(self):
        result = validate_hook_data({})
        assert result["tool_name"] == "unknown"
        assert result["context_tokens"] == 0

    def test_invalid_tool_name_type(self):
        result = validate_hook_data({"tool_name": 12345})
        assert result["tool_name"] == "unknown"

    def test_tool_name_too_long(self):
        result = validate_hook_data({"tool_name": "x" * 300})
        assert result["tool_name"] == "unknown"

    def test_invalid_context_tokens(self):
        result = validate_hook_data({"context_tokens": -100})
        assert result["context_tokens"] == 0

    def test_oversized_context_tokens(self):
        result = validate_hook_data({"context_tokens": 10_000_001})
        assert result["context_tokens"] == 0

    def test_non_dict_input(self):
        assert validate_hook_data("not a dict") == {}
        assert validate_hook_data(None) == {}


class TestValidateEnvFilePath:
    def test_none_input(self):
        assert validate_env_file_path(None) is None

    def test_valid_tmp_path(self):
        result = validate_env_file_path("/tmp/test.env")
        assert result is not None

    def test_valid_claude_path(self):
        home = str(Path.home())
        result = validate_env_file_path(f"{home}/.claude/env")
        assert result is not None

    def test_rejected_unsafe_path(self):
        assert validate_env_file_path("/etc/passwd") is None
        assert validate_env_file_path("/usr/bin/test") is None


# ── Handler Tests ────────────────────────────────────────────────────────


class TestHandlers:
    @pytest.fixture(autouse=True)
    def clean_env(self):
        if "RTFI_SESSION_ID" in os.environ:
            del os.environ["RTFI_SESSION_ID"]
        yield
        if "RTFI_SESSION_ID" in os.environ:
            del os.environ["RTFI_SESSION_ID"]

    def test_handle_session_start_creates_session(self):
        result = handle_session_start({})
        assert result["continue"] is True
        assert "RTFI: Session tracking started" in result["systemMessage"]
        assert "RTFI_SESSION_ID" in os.environ

    def test_handle_pre_tool_use_scores_event(self):
        handle_session_start({})
        result = handle_pre_tool_use({"tool_name": "Read"})
        assert result["continue"] is True

    def test_handle_pre_tool_use_agent_spawn(self):
        handle_session_start({})
        result = handle_pre_tool_use({"tool_name": "Task"})
        assert result["continue"] is True

    def test_handle_pre_tool_use_checkpoint_detection(self):
        """H3: Tools in checkpoint_tools should emit CHECKPOINT events."""
        handle_session_start({})
        # AskUserQuestion should trigger checkpoint (in default allowlist)
        handle_pre_tool_use({"tool_name": "Read"})  # builds autonomy
        handle_pre_tool_use({"tool_name": "Read"})
        handle_pre_tool_use({"tool_name": "Read"})

        from hook_handler import engine
        session_id = os.environ.get("RTFI_SESSION_ID")
        state = engine.get_session_state(session_id)
        assert state.steps_since_confirm == 3

        # Checkpoint tool should reset
        handle_pre_tool_use({"tool_name": "AskUserQuestion"})
        state = engine.get_session_state(session_id)
        assert state.steps_since_confirm == 0

    def test_handle_post_tool_use_updates_tokens(self):
        handle_session_start({})
        result = handle_post_tool_use({"tool_name": "Read", "context_tokens": 50000})
        assert result["continue"] is True

    def test_handle_stop_preserves_session_state(self):
        """AC-2: session_state should be non-NULL after handle_stop."""
        handle_session_start({})
        handle_pre_tool_use({"tool_name": "Read", "context_tokens": 5000})

        from hook_handler import db
        session_id = os.environ.get("RTFI_SESSION_ID")

        handle_stop({})

        state = db.load_session_state(session_id)
        assert state is not None, "session_state was NULL after handle_stop"

    def test_handle_stop_finalizes_session(self):
        handle_session_start({})
        handle_pre_tool_use({"tool_name": "Read"})
        result = handle_stop({})
        assert result["decision"] == "approve"
        assert "RTFI: Session complete" in result.get("systemMessage", "")
        assert "RTFI_SESSION_ID" not in os.environ

    def test_handle_stop_no_session(self):
        result = handle_stop({})
        assert result["decision"] == "approve"

    def test_handle_post_tool_use_no_session(self):
        result = handle_post_tool_use({})
        assert result["continue"] is True

    def test_action_mode_block_response_format(self):
        """Gap 22: Verify block mode produces correct permissionDecision."""
        from hook_handler import engine, settings

        handle_session_start({})
        original_threshold = engine.threshold
        original_mode = settings["action_mode"]
        engine.threshold = 1.0
        settings["action_mode"] = "block"

        try:
            for i in range(5):
                result = handle_pre_tool_use({"tool_name": "Task"})
            assert "hookSpecificOutput" in result
            assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        finally:
            engine.threshold = original_threshold
            settings["action_mode"] = original_mode

    def test_action_mode_confirm_response_format(self):
        """Gap 22: Verify confirm mode produces correct permissionDecision."""
        from hook_handler import engine, settings

        handle_session_start({})
        original_threshold = engine.threshold
        original_mode = settings["action_mode"]
        engine.threshold = 1.0
        settings["action_mode"] = "confirm"

        try:
            for i in range(5):
                result = handle_pre_tool_use({"tool_name": "Task"})
            assert "hookSpecificOutput" in result
            assert result["hookSpecificOutput"]["permissionDecision"] == "ask"
        finally:
            engine.threshold = original_threshold
            settings["action_mode"] = original_mode

    def test_env_file_dotenv_format(self):
        """H2: env file should use dotenv format (no 'export' prefix)."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", dir="/tmp", delete=False) as f:
            env_file_path = f.name

        try:
            with patch.dict(os.environ, {"CLAUDE_ENV_FILE": env_file_path}):
                handle_session_start({})

            content = Path(env_file_path).read_text()
            assert "export" not in content, f"env file should not contain 'export': {content}"
            assert "RTFI_SESSION_ID=" in content
        finally:
            Path(env_file_path).unlink(missing_ok=True)

    def test_skill_tool_tracks_displacement_delta(self):
        """Skill tool invocations should track token deltas for displacement."""
        from hook_handler import engine

        handle_session_start({})
        session_id = os.environ.get("RTFI_SESSION_ID")

        # Set instruction_tokens baseline
        state = engine.get_session_state(session_id)
        state.instruction_tokens = 2500

        # Pre-Skill: record context_tokens
        handle_pre_tool_use({"tool_name": "Skill", "context_tokens": 50000})
        assert state.pre_skill_tokens == 50000

        # Post-Skill: context grew by 15000 tokens (skill content loaded)
        handle_post_tool_use({"tool_name": "Skill", "context_tokens": 65000})
        assert state.pre_skill_tokens is None
        assert state.skill_tokens_injected == 15000

    def test_compaction_resets_skill_tokens(self):
        """When context_tokens drops >50%, skill_tokens_injected resets."""
        from hook_handler import engine

        handle_session_start({})
        session_id = os.environ.get("RTFI_SESSION_ID")

        state = engine.get_session_state(session_id)
        state.skill_tokens_injected = 20000
        state.last_context_tokens = 180000

        # Simulate compaction: tokens drop from 180K to 40K
        handle_post_tool_use({"tool_name": "Read", "context_tokens": 40000})
        assert state.skill_tokens_injected == 0

    def test_session_start_measures_instruction_tokens(self):
        """SessionStart should measure CLAUDE.md for displacement baseline."""
        import tempfile

        from hook_handler import engine

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a CLAUDE.md file
            claude_md = Path(tmpdir) / "CLAUDE.md"
            claude_md.write_text("x" * 10000)  # ~2500 tokens

            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": tmpdir}):
                handle_session_start({})
                session_id = os.environ.get("RTFI_SESSION_ID")
                state = engine.get_session_state(session_id)
                # Should be CLAUDE.md tokens (~2500) + system_prompt_tokens (2000)
                assert state.instruction_tokens > 2000

    def test_instruction_tokens_env_override(self):
        """RTFI_INSTRUCTION_TOKENS env var should override auto-detection."""
        from hook_handler import engine

        with patch.dict(os.environ, {"RTFI_INSTRUCTION_TOKENS": "5000"}):
            handle_session_start({})
            session_id = os.environ.get("RTFI_SESSION_ID")
            state = engine.get_session_state(session_id)
            assert state.instruction_tokens == 5000

    def test_displacement_factor_appears_in_score(self):
        """After Skill injection, displacement should appear in risk score."""
        from hook_handler import engine

        handle_session_start({})
        session_id = os.environ.get("RTFI_SESSION_ID")

        state = engine.get_session_state(session_id)
        state.instruction_tokens = 2500
        state.skill_tokens_injected = 2500

        score = engine.get_current_score(session_id)
        assert score.instruction_displacement == 1.0

    def test_verify_audit_log_all_rotated(self):
        """AC-9: verify_audit_log with verify_all=True checks rotated files."""
        import tempfile
        from hook_handler import sign_audit_entry, verify_audit_log

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.log"
            # Write a signed entry to the main log
            entry1 = sign_audit_entry("TEST|session=abc|{}")
            log_path.write_text(entry1 + "\n")
            # Write a signed entry to a rotated file
            rotated = log_path.with_suffix(".log.1")
            entry2 = sign_audit_entry("TEST2|session=def|{}")
            rotated.write_text(entry2 + "\n")

            # verify_all=False only checks main file
            results_main = verify_audit_log(log_path=log_path, verify_all=False)
            assert len(results_main) == 1
            assert results_main[0]["valid"]

            # verify_all=True checks both
            results_all = verify_audit_log(log_path=log_path, verify_all=True)
            assert len(results_all) == 2
            assert all(r["valid"] for r in results_all)
