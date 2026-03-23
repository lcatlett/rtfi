#!/usr/bin/env python3
"""RTFI statusline helper - outputs current session risk score.

Shows LIVE score (recalculated from session_state), not peak score.
Reads config from ~/.rtfi/config.env for consistent normalization ceilings.
Uses canonical risk level taxonomy: NORMAL / ELEVATED / HIGH RISK.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(os.environ.get("RTFI_DB_PATH", str(Path.home() / ".rtfi" / "rtfi.db")))
CURRENT_SESSION_FILE = Path.home() / ".rtfi" / "current_session"
CONFIG_PATH = Path.home() / ".rtfi" / "config.env"

# Default normalization ceilings (match rtfi_core defaults)
DEFAULTS = {
    "max_tokens": 128000,
    "max_agents": 5,
    "max_steps": 10,
    "max_tools_per_min": 20.0,
    "threshold": 70.0,
}


def _load_config() -> dict:
    """Load config for scoring consistency with hook handler."""
    config = dict(DEFAULTS)

    # Read from config.env if available
    if CONFIG_PATH.exists():
        try:
            for line in CONFIG_PATH.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip().lower()
                    if key in config:
                        try:
                            config[key] = type(config[key])(value.strip())
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass

    # Env vars override
    env_map = {
        "RTFI_MAX_TOKENS": ("max_tokens", int),
        "RTFI_MAX_AGENTS": ("max_agents", int),
        "RTFI_MAX_STEPS": ("max_steps", int),
        "RTFI_MAX_TOOLS_PER_MIN": ("max_tools_per_min", float),
        "RTFI_THRESHOLD": ("threshold", float),
    }
    for env_var, (key, parser) in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            try:
                config[key] = parser(val)
            except (ValueError, TypeError):
                pass

    return config


def _calc_live_score(state: dict, config: dict) -> float:
    """Calculate live risk score from session state dict (same formula as RiskScore.calculate)."""
    from datetime import datetime, timezone

    tokens = state.get("tokens", 0)
    steps = state.get("steps_since_confirm", 0)

    # Calculate active agents from timestamps (decay window)
    now = datetime.now(timezone.utc).timestamp()
    decay = 300  # Default 5 minutes
    agent_timestamps = state.get("agent_spawn_timestamps", [])
    active_agents = 0
    for ts in agent_timestamps:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.timestamp() > (now - decay):
                active_agents += 1
        except (ValueError, TypeError):
            continue

    # Calculate tools per minute
    tool_timestamps = state.get("tool_timestamps", [])
    one_min_ago = now - 60
    tools_per_minute = 0
    for ts in tool_timestamps:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.timestamp() > one_min_ago:
                tools_per_minute += 1
        except (ValueError, TypeError):
            continue

    # Weighted formula
    mt = config["max_tokens"]
    ma = config["max_agents"]
    ms = config["max_steps"]
    mtpm = config["max_tools_per_min"]

    cl = min(1.0, tokens / mt) if mt > 0 else 0.0
    af = min(1.0, active_agents / ma) if ma > 0 else 0.0
    ad = min(1.0, steps / ms) if ms > 0 else 0.0
    dv = min(1.0, tools_per_minute / mtpm) if mtpm > 0 else 0.0

    total = (cl * 0.25 + af * 0.30 + ad * 0.25 + dv * 0.20) * 100
    return round(total, 1)


def _risk_level(score: float) -> str:
    """Canonical risk level label (AC-5)."""
    if score < 30:
        return "NORMAL"
    if score < 70:
        return "ELEVATED"
    return "HIGH RISK"


def get_current_risk() -> dict:
    """Get current session risk data using live score."""
    session_id = os.environ.get("RTFI_SESSION_ID", "")
    if not session_id and CURRENT_SESSION_FILE.exists():
        try:
            session_id = CURRENT_SESSION_FILE.read_text().strip()
        except Exception:
            pass

    if not session_id or not DB_PATH.exists():
        return {"score": 0, "peak": 0, "level": "NORMAL", "label": "--"}

    config = _load_config()

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=1)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT session_state, peak_risk_score, total_tool_calls, total_agent_spawns "
            "FROM sessions WHERE id = ? AND outcome = 'in_progress'",
            (session_id,),
        ).fetchone()
        conn.close()

        if not row:
            return {"score": 0, "peak": 0, "level": "NORMAL", "label": "--"}

        state_json, peak, tool_calls, agent_spawns = row
        peak = peak or 0

        # Calculate LIVE score from session_state (not peak)
        if state_json:
            state = json.loads(state_json)
            score = _calc_live_score(state, config)
        else:
            score = peak

        level = _risk_level(score)

        return {
            "score": score,
            "peak": round(peak, 1),
            "level": level,
            "label": f"{score:.0f}",
            "tool_calls": tool_calls or 0,
            "agent_spawns": agent_spawns or 0,
        }
    except Exception:
        return {"score": 0, "peak": 0, "level": "NORMAL", "label": "--"}


def main() -> None:
    data = get_current_risk()
    if "--json" in sys.argv:
        print(json.dumps(data))
    else:
        print(data["label"])


if __name__ == "__main__":
    main()
