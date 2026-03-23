#!/usr/bin/env python3
"""RTFI Hook Handler - processes Claude Code hook events for risk scoring."""

import hashlib
import hmac
import json
import json as json_module
import logging
import logging.handlers
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Configure logging with rotation and structured JSON
LOG_DIR = Path.home() / ".rtfi"
LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

MAX_INPUT_SIZE = 1_000_000  # 1MB stdin limit


class JsonFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

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

# Audit logger for compliance with rotation and JSON format
audit_logger = logging.getLogger("rtfi.audit")
audit_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "audit.log", maxBytes=5_000_000, backupCount=3
)
audit_handler.setFormatter(_json_formatter)
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)

# Add scripts/ to path for rtfi_core import
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

# Check dependencies BEFORE importing rtfi_core
try:
    import pydantic  # noqa: F401
except ImportError:
    logger.error("RTFI: Missing dependency 'pydantic'. Run: uv pip install pydantic>=2.0.0")
    print(json.dumps({
        "continue": True,
        "systemMessage": "RTFI: Missing dependency 'pydantic'. Run: uv pip install pydantic>=2.0.0"
    }))
    sys.exit(0)

from rtfi_core import (
    Database,
    EventType,
    RiskEngine,
    RiskEvent,
    RiskScore,
    Session,
    SessionOutcome,
    SessionState,
    get_statsd,
    load_settings,
)


# ── HMAC Audit Trail ────────────────────────────────────────────────────


def _get_audit_key() -> bytes:
    """Get or create a machine-specific audit signing key.

    Creates key file with 0o600 permissions atomically to prevent race condition.
    """
    key_path = LOG_DIR / ".audit_key"
    if key_path.exists():
        return key_path.read_bytes()
    key = os.urandom(32)
    # Atomic creation with restricted permissions (no race window)
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def sign_audit_entry(entry: str) -> str:
    """Add HMAC-SHA256 signature to an audit log entry."""
    key = _get_audit_key()
    sig = hmac.new(key, entry.encode(), hashlib.sha256).hexdigest()
    return f"{entry} [sig:{sig}]"


def verify_audit_log(log_path: Path | None = None, verify_all: bool = False) -> list[dict[str, Any]]:
    """Verify integrity of audit log entries.

    Args:
        log_path: Path to audit log file. Defaults to ~/.rtfi/audit.log.
        verify_all: If True, also verify rotated log files (.1, .2, .3).

    Returns list of results per line.
    """
    log_path = log_path or (LOG_DIR / "audit.log")
    key = _get_audit_key()

    files_to_check = [log_path]
    if verify_all:
        for i in range(1, 4):
            rotated = log_path.with_suffix(f".log.{i}")
            if rotated.exists():
                files_to_check.append(rotated)

    results: list[dict[str, Any]] = []
    for check_path in files_to_check:
        if not check_path.exists():
            continue
        for i, line in enumerate(check_path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            sig_marker = " [sig:"
            if sig_marker not in line:
                results.append({"file": str(check_path), "line": i, "valid": False, "reason": "no signature"})
                continue
            content, _, sig_part = line.rpartition(sig_marker)
            sig = sig_part.rstrip("]")
            expected = hmac.new(key, content.encode(), hashlib.sha256).hexdigest()
            results.append({"file": str(check_path), "line": i, "valid": hmac.compare_digest(sig, expected)})
    return results


def log_audit(event_type: str, session_id: str, details: dict[str, Any]) -> None:
    """Log audit event with HMAC signature for integrity verification."""
    entry = f"{event_type}|session={session_id}|{json.dumps(details, default=str)}"
    signed = sign_audit_entry(entry)
    audit_logger.info(signed)


# ── Input Validation ─────────────────────────────────────────────────────


def validate_hook_data(hook_data: Any) -> dict[str, Any]:
    """Validate and sanitize hook data input."""
    if not isinstance(hook_data, dict):
        logger.warning(f"Invalid hook_data type: {type(hook_data)}")
        return {}

    validated: dict[str, Any] = {}

    tool_name = hook_data.get("tool_name")
    if isinstance(tool_name, str) and len(tool_name) < 256:
        validated["tool_name"] = tool_name
    else:
        validated["tool_name"] = "unknown"

    context_tokens = hook_data.get("context_tokens")
    if isinstance(context_tokens, int) and 0 <= context_tokens < 10_000_000:
        validated["context_tokens"] = context_tokens
    else:
        validated["context_tokens"] = 0

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


# ── Global State ─────────────────────────────────────────────────────────

SESSION_ID_ENV = "RTFI_SESSION_ID"
CURRENT_SESSION_FILE = LOG_DIR / "current_session"

db = Database()
settings = load_settings()
engine = RiskEngine(
    threshold=settings["threshold"],
    max_tokens=settings["max_tokens"],
    max_agents=settings["max_agents"],
    max_steps=settings["max_steps"],
    max_tools_per_min=settings["max_tools_per_min"],
    agent_decay_seconds=settings.get("agent_decay_seconds", 300),
)
statsd = get_statsd()
checkpoint_tools: set[str] = settings.get("checkpoint_tools", {"AskUserQuestion"})


# ── State Management ─────────────────────────────────────────────────────


def _hydrate_session(session_id: str) -> bool:
    """Load session and its persisted state into the engine. Returns True if successful."""
    if engine.get_session(session_id):
        return True
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
    """Save the engine's current session state to the database.

    Uses public API (get_session_state) instead of accessing engine._sessions directly.
    Uses save_session with session_state param to avoid H1 ordering hazard.
    """
    state = engine.get_session_state(session_id)
    if state:
        db.save_session(state.session, session_state=state.to_dict())


def _write_session_id(session_id: str) -> None:
    """Write session ID to CLAUDE_ENV_FILE and ~/.rtfi/current_session (H2 fix)."""
    # Write to CLAUDE_ENV_FILE (dotenv format, no 'export' prefix)
    env_file = validate_env_file_path(os.environ.get("CLAUDE_ENV_FILE"))
    if env_file:
        try:
            with open(env_file, "a") as f:
                f.write(f'{SESSION_ID_ENV}="{session_id}"\n')
        except Exception as e:
            logger.warning(f"Failed to write to env file: {e}")

    # Also write to ~/.rtfi/current_session as fallback
    try:
        CURRENT_SESSION_FILE.write_text(session_id)
    except Exception as e:
        logger.warning(f"Failed to write current_session file: {e}")


def _resolve_session_id() -> str | None:
    """Resolve the current session ID from env, file fallback, or DB lookup (H2 fix)."""
    # 1. Environment variable (primary)
    session_id = os.environ.get(SESSION_ID_ENV)
    if session_id:
        return session_id

    # 2. ~/.rtfi/current_session file (fallback for shells without env inheritance)
    if CURRENT_SESSION_FILE.exists():
        try:
            session_id = CURRENT_SESSION_FILE.read_text().strip()
            if session_id:
                return session_id
        except Exception:
            pass

    # 3. DB lookup by project_dir (last resort)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        session = db.find_active_session(project_dir)
        if session:
            log_audit("SESSION_FALLBACK_LOOKUP", session.id, {"project_dir": project_dir})
            return session.id

    return None


# ── Hook Handlers ────────────────────────────────────────────────────────


def handle_session_start(hook_data: dict[str, Any]) -> dict[str, Any]:
    """Handle SessionStart - initialize new session."""
    validate_hook_data(hook_data)
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

    _write_session_id(session_id)
    log_audit("SESSION_START", session_id, {"threshold": settings["threshold"]})

    return {
        "continue": True,
        "systemMessage": f"RTFI: Session tracking started (threshold: {settings['threshold']})",
    }


def handle_pre_tool_use(hook_data: dict[str, Any]) -> dict[str, Any]:
    """Handle PreToolUse - track tool calls and calculate risk."""
    validated = validate_hook_data(hook_data)
    session_id = _resolve_session_id()

    if not session_id:
        # No session found anywhere — create one
        session_id = str(uuid.uuid4())
        os.environ[SESSION_ID_ENV] = session_id
        session = Session(id=session_id, project_dir=os.environ.get("CLAUDE_PROJECT_DIR"))
        engine.start_session(session)
        db.save_session(session)
        _write_session_id(session_id)
        log_audit("SESSION_AUTO_START", session_id, {})
    else:
        os.environ[SESSION_ID_ENV] = session_id  # Ensure env is set
        if not _hydrate_session(session_id):
            session = Session(id=session_id, project_dir=os.environ.get("CLAUDE_PROJECT_DIR"))
            engine.start_session(session)
            db.save_session(session)
            log_audit("SESSION_AUTO_START", session_id, {})

    tool_name = validated.get("tool_name", "unknown")

    # Classify event type
    if tool_name == "Task":
        event_type = EventType.AGENT_SPAWN
    elif tool_name in checkpoint_tools:
        event_type = EventType.CHECKPOINT
    else:
        event_type = EventType.TOOL_CALL

    event = RiskEvent(
        session_id=session_id,
        event_type=event_type,
        tool_name=tool_name,
        context_tokens=validated.get("context_tokens", 0),
        metadata={"hook": "pre_tool_use"},
    )

    score = engine.process_event(event)
    db.save_event(event)
    _persist_state(session_id)

    # Emit metrics
    if statsd:
        statsd.gauge("risk_score", score.total)
        statsd.incr("tool_calls")
        if event_type == EventType.AGENT_SPAWN:
            statsd.incr("agent_spawns")

    result: dict[str, Any] = {"continue": True}

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
            result["hookSpecificOutput"] = {"permissionDecision": "deny"}
            result["systemMessage"] = warning + " Action blocked by RTFI."
        elif settings["action_mode"] == "confirm":
            result["hookSpecificOutput"] = {"permissionDecision": "ask"}
            result["systemMessage"] = warning + " Confirm to proceed."
        else:
            result["systemMessage"] = warning

    return result


def handle_post_tool_use(hook_data: dict[str, Any]) -> dict[str, Any]:
    """Handle PostToolUse - update context after tool execution."""
    validated = validate_hook_data(hook_data)
    session_id = os.environ.get(SESSION_ID_ENV)

    if not session_id:
        return {"continue": True}

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


def handle_stop(hook_data: dict[str, Any]) -> dict[str, Any]:
    """Handle Stop - finalize session. Preserves session_state for post-session analytics (AC-2)."""
    session_id = os.environ.get(SESSION_ID_ENV)

    if not session_id:
        return {"decision": "approve"}

    # Hydrate session state from DB before ending
    _hydrate_session(session_id)

    # Persist final state snapshot BEFORE ending the session (AC-2)
    _persist_state(session_id)

    session = engine.end_session(session_id)

    # Fallback: load directly from DB if engine didn't have it
    if not session:
        session = db.get_session(session_id)

    if session:
        # Calculate final score from persisted state
        state_dict = db.load_session_state(session_id)
        if state_dict:
            temp_state = SessionState.from_dict(
                state_dict, session,
                agent_decay_seconds=settings.get("agent_decay_seconds", 300),
            )
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
        # save_session preserves session_state (H1 fix — no longer uses INSERT OR REPLACE)
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


# ── Entry Point ──────────────────────────────────────────────────────────


def main() -> None:
    """Entry point for hook execution."""
    import time

    _start_time = time.monotonic()
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"continue": True, "error": "No hook type specified"}))
            return

        hook_type = sys.argv[1]

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

        if statsd:
            elapsed_ms = (time.monotonic() - _start_time) * 1000
            statsd.timing("hook_latency_ms", elapsed_ms)

    except Exception as e:
        logger.exception(f"Unhandled exception in hook handler: {e}")
        print(json.dumps({"continue": True, "decision": "approve"}))
    finally:
        db.close()


if __name__ == "__main__":
    main()
