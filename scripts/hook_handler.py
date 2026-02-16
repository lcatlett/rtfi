#!/usr/bin/env python3
"""RTFI Hook Handler - processes Claude Code hook events for risk scoring."""

import json
import logging
import logging.handlers
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Configure logging with rotation (H8)
LOG_DIR = Path.home() / ".rtfi"
LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)  # M8: restrict permissions

MAX_INPUT_SIZE = 1_000_000  # 1MB stdin limit (M9)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "rtfi.log", maxBytes=5_000_000, backupCount=3
        ),
    ],
)
logger = logging.getLogger("rtfi")

# Audit logger for compliance with rotation (H8)
audit_logger = logging.getLogger("rtfi.audit")
audit_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "audit.log", maxBytes=5_000_000, backupCount=3
)
audit_handler.setFormatter(
    logging.Formatter("%(asctime)s|%(message)s")
)
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)

# Add rtfi package to path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

# Check dependencies BEFORE importing rtfi modules (no auto-install — H5)
try:
    import pydantic  # noqa: F401
except ImportError:
    logger.error("RTFI: Missing dependency 'pydantic'. Run: pip3 install pydantic>=2.0.0")
    print(json.dumps({
        "continue": True,
        "systemMessage": "RTFI: Missing dependency 'pydantic'. Run: pip3 install pydantic>=2.0.0"
    }))
    sys.exit(0)

from rtfi.models.events import EventType, RiskEvent, Session, SessionOutcome
from rtfi.scoring.engine import RiskEngine
from rtfi.storage.database import Database


def log_audit(event_type: str, session_id: str, details: dict) -> None:
    """Log audit event for compliance tracking."""
    audit_logger.info(
        f"{event_type}|session={session_id}|{json.dumps(details, default=str)}"
    )


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
    """Load user settings from .claude/rtfi.local.md or defaults."""
    # Parse threshold with validation (C4)
    try:
        threshold = float(os.environ.get("RTFI_THRESHOLD", 70.0))
        if not (0 <= threshold <= 100):
            logger.warning(f"RTFI_THRESHOLD={threshold} out of range 0-100, using default 70.0")
            threshold = 70.0
    except (ValueError, TypeError):
        logger.warning(
            f"Invalid RTFI_THRESHOLD={os.environ.get('RTFI_THRESHOLD')!r}, using default 70.0"
        )
        threshold = 70.0

    # Parse retention_days with validation (C4)
    try:
        retention_days = int(os.environ.get("RTFI_RETENTION_DAYS", 90))
        if not (1 <= retention_days <= 3650):
            logger.warning(
                f"RTFI_RETENTION_DAYS={retention_days} out of range 1-3650, using default 90"
            )
            retention_days = 90
    except (ValueError, TypeError):
        logger.warning(
            f"Invalid RTFI_RETENTION_DAYS={os.environ.get('RTFI_RETENTION_DAYS')!r}, "
            "using default 90"
        )
        retention_days = 90

    settings = {
        "threshold": threshold,
        "action_mode": os.environ.get("RTFI_ACTION_MODE", "alert"),
        "retention_days": retention_days,
    }

    # Validate action_mode from env
    if settings["action_mode"] not in ("alert", "block", "confirm"):
        settings["action_mode"] = "alert"

    # Check for settings file in project or home
    settings_paths = [
        Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")) / ".claude" / "rtfi.local.md",
        Path.home() / ".claude" / "rtfi.local.md",
    ]

    for settings_path in settings_paths:
        if settings_path.exists():
            try:
                content = settings_path.read_text()
                # Parse simple key-value from markdown
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("Risk score threshold"):
                        try:
                            settings["threshold"] = float(line.split(":")[-1].strip())
                        except ValueError:
                            logger.warning(f"Invalid threshold in settings: {line}")
                    elif line.startswith("What happens when threshold exceeded"):
                        mode = line.split(":")[-1].strip().lower()
                        if mode in ("alert", "block", "confirm"):
                            settings["action_mode"] = mode
                        else:
                            logger.warning(f"Invalid action_mode in settings: {mode}")
                    elif line.startswith("Data retention days"):
                        try:
                            settings["retention_days"] = int(line.split(":")[-1].strip())
                        except ValueError:
                            logger.warning(f"Invalid retention_days in settings: {line}")
                break
            except Exception as e:
                logger.error(f"Error reading settings file {settings_path}: {e}")

    logger.info(f"Loaded settings: threshold={settings['threshold']}, mode={settings['action_mode']}")
    return settings


# Global state for session tracking
SESSION_ID_ENV = "RTFI_SESSION_ID"
db = Database()
settings = load_settings()
engine = RiskEngine(threshold=settings["threshold"])


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

    session = Session(id=session_id)
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

    result = {"continue": True}

    if score.threshold_exceeded:
        warning = (
            f"RTFI WARNING: Risk score {score.total:.1f} exceeds threshold {settings['threshold']}. "
            f"Factors: context={score.context_length:.2f}, agents={score.agent_fanout:.2f}, "
            f"autonomy={score.autonomy_depth:.2f}, velocity={score.decision_velocity:.2f}. "
            f"High probability of instruction non-compliance."
        )

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
            )
            session.final_risk_score = final_score.total
        else:
            session.final_risk_score = session.peak_risk_score

        session.outcome = SessionOutcome.COMPLETED
        session.ended_at = datetime.now()
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

    except Exception as e:
        # Critical: Never let exceptions crash the hook
        logger.exception(f"Unhandled exception in hook handler: {e}")
        # Always return continue: True to avoid breaking Claude Code
        print(json.dumps({"continue": True, "decision": "approve"}))


if __name__ == "__main__":
    main()
