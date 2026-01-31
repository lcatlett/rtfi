"""Tests for the hook handler."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))


class TestValidateHookData:
    """Tests for validate_hook_data function."""

    def test_valid_hook_data(self):
        from hook_handler import validate_hook_data

        data = {
            "tool_name": "Read",
            "context_tokens": 5000,
            "session_id": "abc123",
        }
        result = validate_hook_data(data)

        assert result["tool_name"] == "Read"
        assert result["context_tokens"] == 5000
        assert result["session_id"] == "abc123"

    def test_invalid_tool_name_type(self):
        from hook_handler import validate_hook_data

        data = {"tool_name": 12345}
        result = validate_hook_data(data)

        assert result["tool_name"] == "unknown"

    def test_tool_name_too_long(self):
        from hook_handler import validate_hook_data

        data = {"tool_name": "x" * 300}
        result = validate_hook_data(data)

        assert result["tool_name"] == "unknown"

    def test_invalid_context_tokens(self):
        from hook_handler import validate_hook_data

        data = {"context_tokens": -100}
        result = validate_hook_data(data)

        assert result["context_tokens"] == 0

    def test_non_dict_input(self):
        from hook_handler import validate_hook_data

        result = validate_hook_data("not a dict")
        assert result == {}

        result = validate_hook_data(None)
        assert result == {}

    def test_missing_fields_use_defaults(self):
        from hook_handler import validate_hook_data

        result = validate_hook_data({})
        assert result["tool_name"] == "unknown"
        assert result["context_tokens"] == 0


class TestValidateEnvFilePath:
    """Tests for validate_env_file_path function."""

    def test_none_input(self):
        from hook_handler import validate_env_file_path

        assert validate_env_file_path(None) is None

    def test_valid_tmp_path(self):
        from hook_handler import validate_env_file_path

        result = validate_env_file_path("/tmp/test.env")
        assert result is not None
        assert "test.env" in result

    def test_valid_claude_path(self):
        from hook_handler import validate_env_file_path

        home = str(Path.home())
        result = validate_env_file_path(f"{home}/.claude/env")
        assert result is not None

    def test_rejected_unsafe_path(self):
        from hook_handler import validate_env_file_path

        result = validate_env_file_path("/etc/passwd")
        assert result is None

        result = validate_env_file_path("/usr/bin/test")
        assert result is None


class TestLoadSettings:
    """Tests for load_settings function."""

    def test_default_settings(self):
        from hook_handler import load_settings

        # Clear any env overrides
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings()

        assert settings["threshold"] == 70.0
        assert settings["action_mode"] == "alert"
        assert settings["retention_days"] == 90

    def test_env_override_threshold(self):
        from hook_handler import load_settings

        with patch.dict(os.environ, {"RTFI_THRESHOLD": "50.0"}):
            # Need to reimport to pick up new env
            import importlib
            import hook_handler
            importlib.reload(hook_handler)
            settings = hook_handler.load_settings()

        assert settings["threshold"] == 50.0

    def test_env_override_action_mode(self):
        from hook_handler import load_settings

        with patch.dict(os.environ, {"RTFI_ACTION_MODE": "block"}):
            import importlib
            import hook_handler
            importlib.reload(hook_handler)
            settings = hook_handler.load_settings()

        assert settings["action_mode"] == "block"

    def test_invalid_action_mode_falls_back(self):
        from hook_handler import load_settings

        with patch.dict(os.environ, {"RTFI_ACTION_MODE": "invalid"}):
            import importlib
            import hook_handler
            importlib.reload(hook_handler)
            settings = hook_handler.load_settings()

        assert settings["action_mode"] == "alert"


class TestHandlers:
    """Tests for handler functions."""

    @pytest.fixture
    def clean_env(self):
        """Clean up RTFI env vars before and after tests."""
        if "RTFI_SESSION_ID" in os.environ:
            del os.environ["RTFI_SESSION_ID"]
        yield
        if "RTFI_SESSION_ID" in os.environ:
            del os.environ["RTFI_SESSION_ID"]

    def test_handle_session_start(self, clean_env):
        from hook_handler import handle_session_start

        result = handle_session_start({})

        assert result["continue"] is True
        assert "RTFI: Session tracking started" in result["systemMessage"]
        assert "RTFI_SESSION_ID" in os.environ

    def test_handle_pre_tool_use_creates_session_if_missing(self, clean_env):
        from hook_handler import handle_pre_tool_use

        result = handle_pre_tool_use({"tool_name": "Read"})

        assert result["continue"] is True
        assert "RTFI_SESSION_ID" in os.environ

    def test_handle_pre_tool_use_tracks_tool_calls(self, clean_env):
        from hook_handler import handle_session_start, handle_pre_tool_use

        handle_session_start({})

        result = handle_pre_tool_use({"tool_name": "Read"})
        assert result["continue"] is True

        result = handle_pre_tool_use({"tool_name": "Write"})
        assert result["continue"] is True

    def test_handle_post_tool_use_no_session(self, clean_env):
        from hook_handler import handle_post_tool_use

        result = handle_post_tool_use({})

        assert result["continue"] is True

    def test_handle_stop_no_session(self, clean_env):
        from hook_handler import handle_stop

        result = handle_stop({})

        assert result["decision"] == "approve"

    def test_handle_stop_with_session(self, clean_env):
        from hook_handler import handle_session_start, handle_pre_tool_use, handle_stop

        handle_session_start({})
        handle_pre_tool_use({"tool_name": "Read"})

        result = handle_stop({})

        assert result["decision"] == "approve"
        assert "RTFI: Session complete" in result.get("systemMessage", "")
        assert "RTFI_SESSION_ID" not in os.environ

    def test_threshold_exceeded_alert_mode(self, clean_env):
        from hook_handler import handle_session_start, handle_pre_tool_use, engine, settings

        # Temporarily lower threshold
        original_threshold = engine.threshold
        engine.threshold = 1.0  # Very low threshold

        try:
            handle_session_start({})

            # Spawn multiple agents to exceed threshold
            for i in range(5):
                result = handle_pre_tool_use({"tool_name": "Task"})

            # Should have warning in result
            assert "systemMessage" in result
            assert "RTFI WARNING" in result["systemMessage"]
        finally:
            engine.threshold = original_threshold
