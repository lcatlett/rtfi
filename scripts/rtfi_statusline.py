#!/usr/bin/env python3
"""RTFI statusline helper - outputs current session risk score."""

import json
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(os.environ.get("RTFI_DB_PATH", str(Path.home() / ".rtfi" / "rtfi.db")))
SESSION_FILE = Path.home() / ".rtfi" / "current_session"


def get_current_risk() -> dict:
    """Get current session risk data."""
    session_id = os.environ.get("RTFI_SESSION_ID", "")
    if not session_id and SESSION_FILE.exists():
        session_id = SESSION_FILE.read_text().strip()

    if not session_id or not DB_PATH.exists():
        return {"score": 0, "level": "unknown", "label": "--"}

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=1)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT peak_risk_score, total_tool_calls, total_agent_spawns "
            "FROM sessions WHERE id = ? AND outcome = 'in_progress'",
            (session_id,),
        ).fetchone()
        conn.close()

        if not row:
            return {"score": 0, "level": "none", "label": "--"}

        score = row[0] or 0
        if score <= 30:
            level = "low"
        elif score <= 50:
            level = "moderate"
        elif score <= 70:
            level = "elevated"
        elif score <= 85:
            level = "high"
        else:
            level = "critical"

        return {
            "score": round(score, 1),
            "level": level,
            "label": f"{score:.0f}",
            "tool_calls": row[1] or 0,
            "agent_spawns": row[2] or 0,
        }
    except Exception:
        return {"score": 0, "level": "error", "label": "--"}


def main():
    data = get_current_risk()
    # If called with --json, output JSON for programmatic use
    if "--json" in sys.argv:
        print(json.dumps(data))
    else:
        # Default: formatted string for statusline
        print(data["label"])


if __name__ == "__main__":
    main()
