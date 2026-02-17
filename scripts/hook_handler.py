#!/usr/bin/env python3
"""RTFI Hook Handler - processes Claude Code hook events for risk scoring."""

import hashlib
import hmac
import json
import json as json_module
import logging
import logging.handlers
import os
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Configure logging with rotation (H8) and structured JSON (M4)
LOG_DIR = Path.home() / ".rtfi"
LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)  # M8: restrict permissions

MAX_INPUT_SIZE = 1_000_000  # 1MB stdin limit (M9)


class JsonFormatter(logging.Formatter):
    """JSON log formatter for structured logging (M4)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "session_id"):
            log_entry["session_id"] = record.session_id
        if hasattr(record, "hook_type"):
            log_entry["hook_type"] = record.hook_type
        return json_module.dumps(log_entry)


_json_formatter = JsonFormatter()

_rtfi_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "rtfi.log", maxBytes=5_000_000, backupCount=3
)
_rtfi_handler.setFormatter(_json_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_rtfi_handler],
)
logger = logging.getLogger("rtfi")

# Audit logger for compliance with rotation (H8) and JSON format (M4)
audit_logger = logging.getLogger("rtfi.audit")
audit_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "audit.log", maxBytes=5_000_000, backupCount=3
)
audit_handler.setFormatter(_json_formatter)
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)

# Add rtfi package to path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

# Check dependencies BEFORE importing rtfi modules (no auto-install — H5)
try:
    import pydantic  # noqa: F401
except ImportError:
    logger.error("RTFI: Missing dependency 'pydantic'. Run: uv pip install pydantic>=2.0.0 (or pip3 install pydantic>=2.0.0)")
    print(json.dumps({
        "continue": True,
        "systemMessage": "RTFI: Missing dependency 'pydantic'. Run: uv pip install pydantic>=2.0.0 (or pip3 install pydantic>=2.0.0)"
    }))
    sys.exit(0)

from rtfi.metrics import get_statsd
from rtfi.models.events import EventType, RiskEvent, Session, SessionOutcome
from rtfi.scoring.engine import RiskEngine
from rtfi.storage.database import Database


def _get_audit_key() -> bytes:
    """Get or create a machine-specific audit signing key (M5)."""
    key_path = LOG_DIR / ".audit_key"
    if key_path.exists():
        return key_path.read_bytes()
    key = os.urandom(32)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


def sign_audit_entry(entry: str) -> str:
    """Add HMAC-SHA256 signature to an audit log entry (M5)."""
    key = _get_audit_key()
    sig = hmac.new(key, entry.encode(), hashlib.sha256).hexdigest()
    return f"{entry} [sig:{sig}]"


def verify_audit_log(log_path: Path | None = None) -> list[dict]:
    """Verify integrity of audit log entries. Returns list of results per line."""
    log_path = log_path or (LOG_DIR / "audit.log")
    key = _get_audit_key()
    results = []
    for i, line in enumerate(log_path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        sig_marker = " [sig:"
        if sig_marker not in line:
            results.append({"line": i, "valid": False, "reason": "no signature"})
            continue
        content, _, sig_part = line.rpartition(sig_marker)
        sig = sig_part.rstrip("]")
        expected = hmac.new(key, content.encode(), hashlib.sha256).hexdigest()
        results.append({"line": i, "valid": hmac.compare_digest(sig, expected)})
    return results


def log_audit(event_type: str, session_id: str, details: dict) -> None:
    """Log audit event with HMAC signature for integrity verification (M5)."""
    entry = f"{event_type}|session={session_id}|{json.dumps(details, default=str)}"
    signed = sign_audit_entry(entry)
    audit_logger.info(signed)


def validate_hook_data(hook_data: Any) -> dict:
    """Validate and sanitize hook data input."""
    if not isinstance(hook_data, dict):
        logger.warning(f"Invalid hook_data type: {type(hook_data)}")
        return {}

    validated = {}

    # Validate tool_name
    tool_name = hook_data.get("tool_name")
    if isinstance(tool_name, str) and len(tool_name) < 256:
        validated["tool_name"] = tool_name
    else:
        validated["tool_name"] = "unknown"

    # Validate context_tokens
    context_tokens = hook_data.get("context_tokens")
    if isinstance(context_tokens, int) and 0 <= context_tokens < 10_000_000:
        validated["context_tokens"] = context_tokens
    else:
        validated["context_tokens"] = 0

    # Validate session_id if present
    session_id = hook_data.get("session_id")
    if isinstance(session_id, str) and len(session_id) < 128:
        validated["session_id"] = session_id

    return validated


def validate_env_file_path(env_file: str | None) -> str | None:
    """Validate that env_file path is safe to write to."""
    if not env_file:
        return None

    try:
        path = Path(env_file).resolve()
        # Only allow writing to temp directories or .claude directories
        allowed_prefixes = [
            Path("/tmp"),
            Path("/var/tmp"),
            Path.home() / ".claude",
            Path(os.environ.get("TMPDIR", "/tmp")),
        ]
        for prefix in allowed_prefixes:
            try:
                path.relative_to(prefix.resolve())
                return str(path)
            except ValueError:
                continue
        logger.warning(f"Rejected env_file path outside allowed directories: {env_file}")
        return None
    except Exception as e:
        logger.warning(f"Invalid env_file path: {e}")
        return None


def load_settings() -> dict:
    """Load settings from config file and environment variables (M6).

    Priority (highest wins):
    1. Environment variables (RTFI_THRESHOLD, RTFI_ACTION_MODE, etc.)
    2. Config file (~/.rtfi/config.env)
    3. Legacy settings file (.claude/rtfi.local.md)
    4. Built-in defaults
    """
    config: dict[str, Any] = {
        "threshold": 70.0,
        "retention_days": 90,
        "action_mode": "alert",
        "log_level": "INFO",
        # Normalization thresholds (L6)
        "max_tokens": 128000,
        "max_agents": 5,
        "max_steps": 10,
        "max_tools_per_min": 20.0,
    }

    # Layer 1: Read config.env file (M6)
    config_path = LOG_DIR / "config.env"
    if config_path.exists():
        try:
            for line in config_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    config[key.strip().lower()] = value.strip()
        except Exception as e:
            logger.error(f"Error reading config file {config_path}: {e}")

    # Layer 2: Legacy settings file (backward compat)
    settings_paths = [
        Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")) / ".claude" / "rtfi.local.md",
        Path.home() / ".claude" / "rtfi.local.md",
    ]
    for settings_path in settings_paths:
        if settings_path.exists():
            try:
                content = settings_path.read_text()
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("Risk score threshold"):
                        try:
                            config["threshold"] = float(line.split(":")[-1].strip())
                        except ValueError:
                            pass
                    elif line.startswith("What happens when threshold exceeded"):
                        mode = line.split(":")[-1].strip().lower()
                        if mode in ("alert", "block", "confirm"):
                            config["action_mode"] = mode
                    elif line.startswith("Data retention days"):
                        try:
                            config["retention_days"] = int(line.split(":")[-1].strip())
                        except ValueError:
                            pass
                break
            except Exception as e:
                logger.error(f"Error reading settings file {settings_path}: {e}")

    # Layer 3: Environment variables override everything (C4 validation)
    _env_overrides: list[tuple[str, str, type, Any, tuple[float, float] | None]] = [
        ("threshold", "RTFI_THRESHOLD", float, 70.0, (0, 100)),
        ("retention_days", "RTFI_RETENTION_DAYS", int, 90, (1, 3650)),
        ("action_mode", "RTFI_ACTION_MODE", str, "alert", None),
        ("max_tokens", "RTFI_MAX_TOKENS", int, 128000, (1000, 10_000_000)),
        ("max_agents", "RTFI_MAX_AGENTS", int, 5, (1, 1000)),
        ("max_steps", "RTFI_MAX_STEPS", int, 10, (1, 1000)),
        ("max_tools_per_min", "RTFI_MAX_TOOLS_PER_MIN", float, 20.0, (1, 1000)),
    ]

    for key, env_var, parser, default, bounds in _env_overrides:
        env_val = os.environ.get(env_var)
        if env_val is not None:
            try:
                parsed = parser(env_val)
                if bounds and not (bounds[0] <= parsed <= bounds[1]):
                    logger.warning(
                        f"{env_var}={parsed} out of range {bounds[0]}-{bounds[1]}, "
                        f"using default {default}"
                    )
                    config[key] = default
                else:
                    config[key] = parsed
            except (ValueError, TypeError):
                logger.warning(f"Invalid {env_var}={env_val!r}, using default {default}")
                config[key] = default
        else:
            # Ensure file-loaded string values are cast to proper types
            try:
                config[key] = parser(config[key])
            except (ValueError, TypeError):
                config[key] = default

    # Validate action_mode
    if config["action_mode"] not in ("alert", "block", "confirm"):
        config["action_mode"] = "alert"

    logger.info(f"Loaded settings: threshold={config['threshold']}, mode={config['action_mode']}")
    return config


# Global state for session tracking
SESSION_ID_ENV = "RTFI_SESSION_ID"
db = Database()
settings = load_settings()
engine = RiskEngine(
    threshold=settings["threshold"],
    max_tokens=settings["max_tokens"],
    max_agents=settings["max_agents"],
    max_steps=settings["max_steps"],
    max_tools_per_min=settings["max_tools_per_min"],
)
statsd = get_statsd()  # L3: optional metrics export


def _hydrate_session(session_id: str) -> bool:
    """Load session and its persisted state into the engine. Returns True if successful."""
    if engine.get_session(session_id):
        return True  # Already hydrated
    session = db.get_session(session_id)
    if not session:
        return False
    state_dict = db.load_session_state(session_id)
    if state_dict:
        engine.restore_session(session, state_dict)
    else:
        engine.start_session(session)
    return True


def _persist_state(session_id: str) -> None:
    """Save the engine's current session state to the database."""
    state = engine._sessions.get(session_id)
    if state:
        # save_session first (INSERT OR REPLACE clears session_state),
        # then save_session_state to persist the mutable state
        db.save_session(state.session)
        db.save_session_state(session_id, state.to_dict())


def handle_session_start(hook_data: dict) -> dict:
    """Handle SessionStart - initialize new session."""
    validated = validate_hook_data(hook_data)
    session_id = str(uuid.uuid4())
    os.environ[SESSION_ID_ENV] = session_id

    session = Session(id=session_id, project_dir=os.environ.get("CLAUDE_PROJECT_DIR"))
    engine.start_session(session)
    db.save_session(session)

    # Purge old sessions based on retention policy
    try:
        db.purge_old_sessions(days=settings["retention_days"])
    except Exception as e:
        logger.warning(f"Failed to purge old sessions: {e}")

    # Write session ID to env file if available and valid
    env_file = validate_env_file_path(os.environ.get("CLAUDE_ENV_FILE"))
    if env_file:
        try:
            with open(env_file, "a") as f:
                f.write(f'export {SESSION_ID_ENV}="{session_id}"\n')
        except Exception as e:
            logger.warning(f"Failed to write to env file: {e}")

    log_audit("SESSION_START", session_id, {"threshold": settings["threshold"]})

    return {
        "continue": True,
        "systemMessage": f"RTFI: Session tracking started (threshold: {settings['threshold']})",
    }


def handle_pre_tool_use(hook_data: dict) -> dict:
    """Handle PreToolUse - track tool calls and calculate risk."""
    validated = validate_hook_data(hook_data)
    session_id = os.environ.get(SESSION_ID_ENV)

    if not session_id:
        # Session not initialized, start one now
        session_id = str(uuid.uuid4())
        os.environ[SESSION_ID_ENV] = session_id
        session = Session(id=session_id)
        engine.start_session(session)
        db.save_session(session)
        log_audit("SESSION_AUTO_START", session_id, {})
    else:
        # Hydrate session state from DB (C1 fix)
        if not _hydrate_session(session_id):
            # Session ID set but not in DB — create fresh
            session = Session(id=session_id)
            engine.start_session(session)
            db.save_session(session)
            log_audit("SESSION_AUTO_START", session_id, {})

    tool_name = validated.get("tool_name", "unknown")

    # Detect agent spawns (Task tool)
    event_type = EventType.TOOL_CALL
    if tool_name == "Task":
        event_type = EventType.AGENT_SPAWN

    event = RiskEvent(
        session_id=session_id,
        event_type=event_type,
        tool_name=tool_name,
        context_tokens=validated.get("context_tokens", 0),
        metadata={"hook": "pre_tool_use"},
    )

    score = engine.process_event(event)
    db.save_event(event)

    # Persist updated state back to DB (C1 fix)
    _persist_state(session_id)

    # Emit metrics (L3)
    if statsd:
        statsd.gauge("risk_score", score.total)
        statsd.incr("tool_calls")
        if event_type == EventType.AGENT_SPAWN:
            statsd.incr("agent_spawns")

    result = {"continue": True}

    if score.threshold_exceeded:
        warning = (
            f"RTFI WARNING: Risk score {score.total:.1f} exceeds threshold {settings['threshold']}. "
            f"Factors: context={score.context_length:.2f}, agents={score.agent_fanout:.2f}, "
            f"autonomy={score.autonomy_depth:.2f}, velocity={score.decision_velocity:.2f}. "
            f"High probability of instruction non-compliance."
        )

        if statsd:
            statsd.incr("threshold_exceeded")

        log_audit(
            "THRESHOLD_EXCEEDED",
            session_id,
            {
                "score": score.total,
                "threshold": settings["threshold"],
                "tool": tool_name,
                "action": settings["action_mode"],
            },
        )

        if settings["action_mode"] == "block":
            result["hookSpecificOutput"] = {
                "permissionDecision": "deny",
            }
            result["systemMessage"] = warning + " Action blocked by RTFI."
        elif settings["action_mode"] == "confirm":
            result["hookSpecificOutput"] = {
                "permissionDecision": "ask",
            }
            result["systemMessage"] = warning + " Confirm to proceed."
        else:
            # alert mode (default)
            result["systemMessage"] = warning

    return result


def handle_post_tool_use(hook_data: dict) -> dict:
    """Handle PostToolUse - update context after tool execution."""
    validated = validate_hook_data(hook_data)
    session_id = os.environ.get(SESSION_ID_ENV)

    if not session_id:
        return {"continue": True}

    # Hydrate session state from DB
    if not _hydrate_session(session_id):
        return {"continue": True}

    event = RiskEvent(
        session_id=session_id,
        event_type=EventType.RESPONSE,
        tool_name=validated.get("tool_name"),
        context_tokens=validated.get("context_tokens", 0),
        metadata={"hook": "post_tool_use"},
    )

    engine.process_event(event)
    db.save_event(event)
    _persist_state(session_id)

    return {"continue": True}


def handle_stop(hook_data: dict) -> dict:
    """Handle Stop - finalize session."""
    session_id = os.environ.get(SESSION_ID_ENV)

    if not session_id:
        return {"decision": "approve"}

    # Hydrate session state from DB before ending (C2 fix)
    _hydrate_session(session_id)

    session = engine.end_session(session_id)

    # Fallback: load directly from DB if engine didn't have it
    if not session:
        session = db.get_session(session_id)

    if session:
        # Calculate final score from persisted state
        state_dict = db.load_session_state(session_id)
        if state_dict:
            from rtfi.scoring.engine import SessionState

            temp_state = SessionState.from_dict(state_dict, session)
            from rtfi.models.events import RiskScore

            final_score = RiskScore.calculate(
                tokens=temp_state.tokens,
                active_agents=temp_state.active_agents,
                steps_since_confirm=temp_state.steps_since_confirm,
                tools_per_minute=temp_state.tools_per_minute,
                threshold=engine.threshold,
                max_tokens=engine.max_tokens,
                max_agents=engine.max_agents,
                max_steps=engine.max_steps,
                max_tools_per_min=engine.max_tools_per_min,
            )
            session.final_risk_score = final_score.total
        else:
            session.final_risk_score = session.peak_risk_score

        session.outcome = SessionOutcome.COMPLETED
        session.ended_at = datetime.now(timezone.utc)
        db.save_session(session)

        log_audit(
            "SESSION_END",
            session_id,
            {
                "peak_risk": session.peak_risk_score,
                "final_risk": session.final_risk_score,
                "tool_calls": session.total_tool_calls,
                "agent_spawns": session.total_agent_spawns,
            },
        )

        # Clean up
        if SESSION_ID_ENV in os.environ:
            del os.environ[SESSION_ID_ENV]

        summary = (
            f"RTFI: Session complete. Peak risk: {session.peak_risk_score:.1f}, "
            f"Tool calls: {session.total_tool_calls}, Agent spawns: {session.total_agent_spawns}"
        )

        return {
            "decision": "approve",
            "systemMessage": summary,
        }

    return {"decision": "approve"}


def main():
    """Entry point for hook execution."""
    import time

    _start_time = time.monotonic()
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"continue": True, "error": "No hook type specified"}))
            return

        hook_type = sys.argv[1]

        # Read hook data from stdin
        try:
            stdin_data = sys.stdin.read(MAX_INPUT_SIZE)
            hook_data = json.loads(stdin_data) if stdin_data else {}
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in hook input: {e}")
            hook_data = {}

        handlers = {
            "session_start": handle_session_start,
            "pre_tool_use": handle_pre_tool_use,
            "post_tool_use": handle_post_tool_use,
            "stop": handle_stop,
        }

        handler = handlers.get(hook_type)
        if handler:
            result = handler(hook_data)
        else:
            logger.warning(f"Unknown hook type: {hook_type}")
            result = {"continue": True}

        print(json.dumps(result))

        # Emit hook latency metric (L3)
        if statsd:
            elapsed_ms = (time.monotonic() - _start_time) * 1000
            statsd.timing("hook_latency_ms", elapsed_ms)

    except Exception as e:
        # Critical: Never let exceptions crash the hook
        logger.exception(f"Unhandled exception in hook handler: {e}")
        # Always return continue: True to avoid breaking Claude Code
        print(json.dumps({"continue": True, "decision": "approve"}))
    finally:
        db.close()  # L1: close cached connection


if __name__ == "__main__":
    main()
