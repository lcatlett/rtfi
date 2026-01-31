"""SQLite storage for sessions and events."""

import json
import sqlite3
from pathlib import Path
from typing import Iterator

from rtfi.models.events import (
    EventType,
    RiskEvent,
    RiskScore,
    Session,
    SessionOutcome,
)

DEFAULT_DB_PATH = Path.home() / ".rtfi" / "rtfi.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
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

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    context_tokens INTEGER DEFAULT 0,
    risk_score_total REAL,
    risk_score_factors TEXT,
    threshold_exceeded INTEGER DEFAULT 0,
    metadata TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON risk_events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON risk_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_outcome ON sessions(outcome);
"""


class Database:
    """SQLite database for RTFI session and event storage."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)

    def save_session(self, session: Session) -> None:
        """Save or update a session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                (id, started_at, ended_at, instruction_source, instruction_hash,
                 final_risk_score, peak_risk_score, total_tool_calls,
                 total_agent_spawns, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.started_at.isoformat(),
                    session.ended_at.isoformat() if session.ended_at else None,
                    session.instruction_source,
                    session.instruction_hash,
                    session.final_risk_score,
                    session.peak_risk_score,
                    session.total_tool_calls,
                    session.total_agent_spawns,
                    session.outcome.value,
                ),
            )

    def save_event(self, event: RiskEvent) -> int:
        """Save an event and return its ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO risk_events
                (session_id, timestamp, event_type, tool_name, context_tokens,
                 risk_score_total, risk_score_factors, threshold_exceeded, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    event.timestamp.isoformat(),
                    event.event_type.value,
                    event.tool_name,
                    event.context_tokens,
                    event.risk_score.total if event.risk_score else None,
                    json.dumps(event.risk_score.model_dump()) if event.risk_score else None,
                    1 if event.risk_score and event.risk_score.threshold_exceeded else 0,
                    json.dumps(event.metadata) if event.metadata else None,
                ),
            )
            return cursor.lastrowid or 0

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row:
                return self._row_to_session(row)
        return None

    def get_recent_sessions(self, limit: int = 20) -> list[Session]:
        """Get most recent sessions."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_session(row) for row in rows]

    def get_session_events(self, session_id: str) -> Iterator[RiskEvent]:
        """Get all events for a session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM risk_events WHERE session_id = ? ORDER BY timestamp",
                (session_id,),
            )
            for row in rows:
                yield self._row_to_event(row)

    def get_high_risk_sessions(self, threshold: float = 70.0, limit: int = 20) -> list[Session]:
        """Get sessions that exceeded risk threshold."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM sessions
                WHERE peak_risk_score >= ?
                ORDER BY peak_risk_score DESC LIMIT ?
                """,
                (threshold, limit),
            ).fetchall()
            return [self._row_to_session(row) for row in rows]

    def purge_old_sessions(self, days: int = 90) -> int:
        """Delete sessions and events older than specified days. Returns count deleted."""
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            # Delete events first (foreign key)
            conn.execute(
                """
                DELETE FROM risk_events
                WHERE session_id IN (
                    SELECT id FROM sessions WHERE started_at < ?
                )
                """,
                (cutoff,),
            )
            # Delete sessions
            cursor = conn.execute(
                "DELETE FROM sessions WHERE started_at < ?",
                (cutoff,),
            )
            return cursor.rowcount

    def get_stats(self) -> dict:
        """Get aggregate statistics for health check."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            high_risk = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE peak_risk_score >= 70"
            ).fetchone()[0]
            total_events = conn.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0]
            return {
                "total_sessions": total,
                "high_risk_sessions": high_risk,
                "total_events": total_events,
                "database_path": str(self.db_path),
            }

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        """Convert a database row to a Session."""
        from datetime import datetime

        return Session(
            id=row["id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            instruction_source=row["instruction_source"],
            instruction_hash=row["instruction_hash"],
            final_risk_score=row["final_risk_score"],
            peak_risk_score=row["peak_risk_score"] or 0,
            total_tool_calls=row["total_tool_calls"] or 0,
            total_agent_spawns=row["total_agent_spawns"] or 0,
            outcome=SessionOutcome(row["outcome"]),
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> RiskEvent:
        """Convert a database row to a RiskEvent."""
        from datetime import datetime

        risk_score = None
        if row["risk_score_factors"]:
            factors = json.loads(row["risk_score_factors"])
            risk_score = RiskScore(**factors)

        return RiskEvent(
            id=row["id"],
            session_id=row["session_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            event_type=EventType(row["event_type"]),
            tool_name=row["tool_name"],
            context_tokens=row["context_tokens"],
            risk_score=risk_score,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
