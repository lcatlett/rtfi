"""RTFI Core - Real-Time Instruction Compliance Risk Scoring.

Consolidated domain module: models, scoring engine, database, state management,
configuration, and metrics. This is the single source of truth for all RTFI
domain logic.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator

__version__ = "1.2.0"

__all__ = [
    # Models
    "EventType",
    "SessionOutcome",
    "RiskScore",
    "RiskEvent",
    "Session",
    # Engine
    "RiskEngine",
    "SessionState",
    # Database
    "Database",
    # Config
    "load_settings",
    "DEFAULT_SETTINGS",
    # Metrics
    "get_statsd",
    # Utilities
    "estimate_tokens",
    # Version
    "__version__",
]

# ── Risk Level Taxonomy (canonical, used by ALL components) ──────────────

RISK_LEVELS = [
    (0, 29, "NORMAL", "green"),
    (30, 69, "ELEVATED", "amber"),
    (70, 100, "HIGH RISK", "red"),
]


def risk_level(score: float) -> str:
    """Return canonical risk level label for a score."""
    if score < 30:
        return "NORMAL"
    if score < 70:
        return "ELEVATED"
    return "HIGH RISK"


def risk_color(score: float) -> str:
    """Return canonical color for a score."""
    if score < 30:
        return "green"
    if score < 70:
        return "amber"
    return "red"


# ── StatsD Client ────────────────────────────────────────────────────────


class StatsD:
    """Minimal StatsD client using UDP (no dependencies)."""

    def __init__(
        self, host: str = "localhost", port: int = 8125, prefix: str = "rtfi"
    ) -> None:
        self.host = host
        self.port = port
        self.prefix = prefix
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def gauge(self, name: str, value: float) -> None:
        self._send(f"{self.prefix}.{name}:{value}|g")

    def incr(self, name: str, count: int = 1) -> None:
        self._send(f"{self.prefix}.{name}:{count}|c")

    def timing(self, name: str, ms: float) -> None:
        self._send(f"{self.prefix}.{name}:{ms}|ms")

    def _send(self, data: str) -> None:
        try:
            self._sock.sendto(data.encode(), (self.host, self.port))
        except OSError:
            pass  # Fire-and-forget — never block hook execution


def get_statsd() -> StatsD | None:
    """Return a StatsD client if RTFI_STATSD_HOST is set, else None."""
    host = os.environ.get("RTFI_STATSD_HOST")
    if not host:
        return None
    port = int(os.environ.get("RTFI_STATSD_PORT", "8125"))
    return StatsD(host=host, port=port)


# ── Enums ────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    """Types of events tracked by RTFI."""

    TOOL_CALL = "tool_call"
    AGENT_SPAWN = "agent_spawn"
    RESPONSE = "response"
    CHECKPOINT = "checkpoint"
    SESSION_START = "session_start"
    SESSION_END = "session_end"


class SessionOutcome(str, Enum):
    """Possible session outcomes."""

    COMPLETED = "completed"
    CHECKPOINT_TRIGGERED = "checkpoint_triggered"
    USER_ABORTED = "user_aborted"
    IN_PROGRESS = "in_progress"


# ── Data Models ─────────────────────────────────────────────────────────


@dataclass
class RiskScore:
    """Calculated risk score with component breakdown."""

    total: float = 0.0
    context_length: float = 0.0
    agent_fanout: float = 0.0
    autonomy_depth: float = 0.0
    decision_velocity: float = 0.0
    instruction_displacement: float = 0.0
    threshold_exceeded: bool = False

    @classmethod
    def calculate(
        cls,
        tokens: int,
        active_agents: int,
        steps_since_confirm: int,
        tools_per_minute: float,
        threshold: float = 70.0,
        max_tokens: int = 128000,
        max_agents: int = 5,
        max_steps: int = 10,
        max_tools_per_min: float = 20.0,
        skill_tokens_injected: int = 0,
        instruction_tokens: int = 0,
    ) -> RiskScore:
        """Calculate risk score from session state."""
        weights = {
            "context_length": 0.20,
            "agent_fanout": 0.30,
            "autonomy_depth": 0.25,
            "decision_velocity": 0.15,
            "instruction_displacement": 0.10,
        }

        displacement = (
            min(1.0, skill_tokens_injected / instruction_tokens)
            if instruction_tokens > 0
            else 0.0
        )

        factors = {
            "context_length": min(1.0, tokens / max_tokens) if max_tokens > 0 else 0.0,
            "agent_fanout": min(1.0, active_agents / max_agents) if max_agents > 0 else 0.0,
            "autonomy_depth": min(1.0, steps_since_confirm / max_steps) if max_steps > 0 else 0.0,
            "decision_velocity": min(1.0, tools_per_minute / max_tools_per_min) if max_tools_per_min > 0 else 0.0,
            "instruction_displacement": displacement,
        }

        total = sum(factors[k] * weights[k] for k in weights) * 100

        return cls(
            total=round(total, 2),
            context_length=round(factors["context_length"], 3),
            agent_fanout=round(factors["agent_fanout"], 3),
            autonomy_depth=round(factors["autonomy_depth"], 3),
            decision_velocity=round(factors["decision_velocity"], 3),
            instruction_displacement=round(factors["instruction_displacement"], 3),
            threshold_exceeded=total >= threshold,
        )


@dataclass
class RiskEvent:
    """A single event in a session that affects risk score."""

    session_id: str
    event_type: EventType
    id: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tool_name: str | None = None
    context_tokens: int = 0
    risk_score: RiskScore | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """An RTFI-monitored session."""

    id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    instruction_source: str | None = None
    instruction_hash: str | None = None
    final_risk_score: float | None = None
    outcome: SessionOutcome = SessionOutcome.IN_PROGRESS
    peak_risk_score: float = 0.0
    total_tool_calls: int = 0
    total_agent_spawns: int = 0
    project_dir: str | None = None


# ── Database ─────────────────────────────────────────────────────────────

DEFAULT_DB_PATH = Path(
    os.environ.get("RTFI_DB_PATH", str(Path.home() / ".rtfi" / "rtfi.db"))
)

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
    outcome TEXT DEFAULT 'in_progress',
    session_state TEXT,
    project_dir TEXT
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
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_dir);
"""


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime string, handling both naive and tz-aware formats."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Database:
    """SQLite database for RTFI session and event storage."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._conn_obj: sqlite3.Connection | None = None
        self._init_schema()
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        """Get a database connection with pragmas enabled."""
        if self._conn_obj is None:
            self._conn_obj = sqlite3.connect(self.db_path)
            self._conn_obj.execute("PRAGMA foreign_keys = ON")
        return self._conn_obj

    def close(self) -> None:
        """Close the cached connection."""
        if self._conn_obj:
            self._conn_obj.close()
            self._conn_obj = None

    def _init_schema(self) -> None:
        """Initialize database schema with migration support."""
        conn = self._connect()
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA foreign_keys = ON")
        # Migrations for existing databases that lack new columns
        cursor = conn.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        if "session_state" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN session_state TEXT")
        if "project_dir" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN project_dir TEXT")
        conn.commit()

    def save_session(
        self, session: Session, session_state: dict[str, Any] | None = None
    ) -> None:
        """Save or update a session. Preserves session_state on updates (H1 fix).

        Uses INSERT OR IGNORE + UPDATE to avoid the DELETE semantics of
        INSERT OR REPLACE, which would clear the session_state column.
        """
        conn = self._connect()
        state_json = json.dumps(session_state) if session_state is not None else None

        # Insert if new row
        conn.execute(
            """INSERT OR IGNORE INTO sessions
            (id, started_at, ended_at, instruction_source, instruction_hash,
             final_risk_score, peak_risk_score, total_tool_calls,
             total_agent_spawns, outcome, project_dir, session_state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                getattr(session, "project_dir", None),
                state_json,
            ),
        )

        # Update existing row (preserves session_state unless explicitly provided)
        cols = [
            "started_at=?", "ended_at=?", "instruction_source=?", "instruction_hash=?",
            "final_risk_score=?", "peak_risk_score=?", "total_tool_calls=?",
            "total_agent_spawns=?", "outcome=?", "project_dir=?",
        ]
        params: list[Any] = [
            session.started_at.isoformat(),
            session.ended_at.isoformat() if session.ended_at else None,
            session.instruction_source,
            session.instruction_hash,
            session.final_risk_score,
            session.peak_risk_score,
            session.total_tool_calls,
            session.total_agent_spawns,
            session.outcome.value,
            getattr(session, "project_dir", None),
        ]
        if session_state is not None:
            cols.append("session_state=?")
            params.append(state_json)
        params.append(session.id)
        conn.execute(f"UPDATE sessions SET {', '.join(cols)} WHERE id=?", params)
        conn.commit()

    def save_session_state(self, session_id: str, state_dict: dict[str, Any]) -> None:
        """Persist session state as JSON."""
        conn = self._connect()
        conn.execute(
            "UPDATE sessions SET session_state = ? WHERE id = ?",
            (json.dumps(state_dict), session_id),
        )
        conn.commit()

    def load_session_state(self, session_id: str) -> dict[str, Any] | None:
        """Load persisted session state."""
        conn = self._connect()
        row = conn.execute(
            "SELECT session_state FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return None

    def save_event(self, event: RiskEvent) -> int:
        """Save an event and return its ID."""
        conn = self._connect()
        cursor = conn.execute(
            """INSERT INTO risk_events
            (session_id, timestamp, event_type, tool_name, context_tokens,
             risk_score_total, risk_score_factors, threshold_exceeded, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.session_id,
                event.timestamp.isoformat(),
                event.event_type.value,
                event.tool_name,
                event.context_tokens,
                event.risk_score.total if event.risk_score else None,
                json.dumps(asdict(event.risk_score)) if event.risk_score else None,
                1 if event.risk_score and event.risk_score.threshold_exceeded else 0,
                json.dumps(event.metadata) if event.metadata else None,
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by ID."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.row_factory = None
        if row:
            return self._row_to_session(row)
        return None

    def find_session_by_prefix(self, prefix: str) -> Session | None:
        """Find a session by ID prefix."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sessions WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
        conn.row_factory = None
        if row:
            return self._row_to_session(row)
        return None

    def find_active_session(self, project_dir: str) -> Session | None:
        """Find the most recent in-progress session for a project (H2 fallback)."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        row = conn.execute(
            """SELECT * FROM sessions
            WHERE project_dir = ? AND outcome = 'in_progress' AND started_at > ?
            ORDER BY started_at DESC LIMIT 1""",
            (project_dir, cutoff),
        ).fetchone()
        conn.row_factory = None
        if row:
            return self._row_to_session(row)
        return None

    def get_recent_sessions(
        self, limit: int = 20, project_dir: str | None = None
    ) -> list[Session]:
        """Get most recent sessions, optionally filtered by project."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        if project_dir:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE project_dir = ? ORDER BY started_at DESC LIMIT ?",
                (project_dir, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.row_factory = None
        return [self._row_to_session(row) for row in rows]

    def get_session_events(self, session_id: str) -> Iterator[RiskEvent]:
        """Get all events for a session."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM risk_events WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        conn.row_factory = None
        for row in rows:
            yield self._row_to_event(row)

    def get_high_risk_sessions(
        self, threshold: float = 70.0, limit: int = 20, project_dir: str | None = None
    ) -> list[Session]:
        """Get sessions that exceeded risk threshold."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        if project_dir:
            rows = conn.execute(
                """SELECT * FROM sessions
                WHERE peak_risk_score >= ? AND project_dir = ?
                ORDER BY peak_risk_score DESC LIMIT ?""",
                (threshold, project_dir, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM sessions
                WHERE peak_risk_score >= ?
                ORDER BY peak_risk_score DESC LIMIT ?""",
                (threshold, limit),
            ).fetchall()
        conn.row_factory = None
        return [self._row_to_session(row) for row in rows]

    def purge_old_sessions(self, days: int = 90) -> int:
        """Delete sessions and events older than specified days. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = self._connect()
        conn.execute(
            """DELETE FROM risk_events
            WHERE session_id IN (
                SELECT id FROM sessions WHERE started_at < ?
            )""",
            (cutoff,),
        )
        cursor = conn.execute(
            "DELETE FROM sessions WHERE started_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount

    def get_stats(self, threshold: float = 70.0) -> dict[str, Any]:
        """Get aggregate statistics. Uses configured threshold for high-risk count."""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        high_risk = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE peak_risk_score >= ?",
            (threshold,),
        ).fetchone()[0]
        total_events = conn.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0]

        # Additional stats for dashboard
        total_tool_calls = conn.execute(
            "SELECT COALESCE(SUM(total_tool_calls), 0) FROM sessions"
        ).fetchone()[0]
        total_agent_spawns = conn.execute(
            "SELECT COALESCE(SUM(total_agent_spawns), 0) FROM sessions"
        ).fetchone()[0]
        avg_risk = conn.execute(
            "SELECT COALESCE(AVG(peak_risk_score), 0) FROM sessions WHERE peak_risk_score > 0"
        ).fetchone()[0]

        return {
            "total_sessions": total,
            "high_risk_sessions": high_risk,
            "total_events": total_events,
            "total_tool_calls": total_tool_calls,
            "total_agent_spawns": total_agent_spawns,
            "avg_risk_score": round(avg_risk, 1),
            "database_path": str(self.db_path),
        }

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        """Convert a database row to a Session."""
        try:
            project_dir = row["project_dir"]
        except (IndexError, KeyError):
            project_dir = None

        return Session(
            id=row["id"],
            started_at=_parse_datetime(row["started_at"]),
            ended_at=_parse_datetime(row["ended_at"]) if row["ended_at"] else None,
            instruction_source=row["instruction_source"],
            instruction_hash=row["instruction_hash"],
            final_risk_score=row["final_risk_score"],
            peak_risk_score=row["peak_risk_score"] or 0,
            total_tool_calls=row["total_tool_calls"] or 0,
            total_agent_spawns=row["total_agent_spawns"] or 0,
            outcome=SessionOutcome(row["outcome"]),
            project_dir=project_dir,
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> RiskEvent:
        """Convert a database row to a RiskEvent."""
        risk_score = None
        if row["risk_score_factors"]:
            factors = json.loads(row["risk_score_factors"])
            risk_score = RiskScore(**factors)

        return RiskEvent(
            id=row["id"],
            session_id=row["session_id"],
            timestamp=_parse_datetime(row["timestamp"]),
            event_type=EventType(row["event_type"]),
            tool_name=row["tool_name"],
            context_tokens=row["context_tokens"],
            risk_score=risk_score,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )


# ── Scoring Engine ───────────────────────────────────────────────────────

DEFAULT_AGENT_DECAY_SECONDS = 300  # 5 minutes


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using ~4 chars/token heuristic."""
    return max(1, len(text) // 4)


@dataclass
class SessionState:
    """Mutable state for a monitored session."""

    session: Session
    tokens: int = 0
    steps_since_confirm: int = 0
    tool_calls_timestamps: list[datetime] = field(default_factory=list)
    agent_spawn_timestamps: list[datetime] = field(default_factory=list)
    agent_decay_seconds: int = DEFAULT_AGENT_DECAY_SECONDS
    instruction_tokens: int = 0
    skill_tokens_injected: int = 0
    pre_skill_tokens: int | None = None
    last_context_tokens: int = 0

    @property
    def active_agents(self) -> int:
        """Count agents spawned within the decay window."""
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - self.agent_decay_seconds
        return len([t for t in self.agent_spawn_timestamps if t.timestamp() > cutoff])

    @property
    def tools_per_minute(self) -> float:
        """Calculate tool calls per minute over the last minute."""
        if not self.tool_calls_timestamps:
            return 0.0
        now = datetime.now(timezone.utc)
        one_minute_ago = now.timestamp() - 60
        recent = [t for t in self.tool_calls_timestamps if t.timestamp() > one_minute_ago]
        return float(len(recent))

    def prune_old_timestamps(self) -> None:
        """Remove timestamps older than their respective windows to prevent memory growth."""
        now = datetime.now(timezone.utc)
        if len(self.tool_calls_timestamps) > 100:
            two_minutes_ago = now.timestamp() - 120
            self.tool_calls_timestamps = [
                t for t in self.tool_calls_timestamps if t.timestamp() > two_minutes_ago
            ]
        if len(self.agent_spawn_timestamps) > 50:
            ten_minutes_ago = now.timestamp() - 600
            self.agent_spawn_timestamps = [
                t for t in self.agent_spawn_timestamps if t.timestamp() > ten_minutes_ago
            ]

    def to_dict(self) -> dict[str, Any]:
        """Serialize mutable state fields to a dict for persistence."""
        return {
            "tokens": self.tokens,
            "active_agents": len(self.agent_spawn_timestamps),
            "steps_since_confirm": self.steps_since_confirm,
            "tool_timestamps": [t.isoformat() for t in self.tool_calls_timestamps],
            "agent_spawn_timestamps": [t.isoformat() for t in self.agent_spawn_timestamps],
            "instruction_tokens": self.instruction_tokens,
            "skill_tokens_injected": self.skill_tokens_injected,
            "last_context_tokens": self.last_context_tokens,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        session: Session,
        agent_decay_seconds: int = DEFAULT_AGENT_DECAY_SECONDS,
    ) -> SessionState:
        """Restore session state from a persisted dict."""
        tool_timestamps: list[datetime] = []
        for ts in data.get("tool_timestamps", []):
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                tool_timestamps.append(dt)
            except (ValueError, TypeError):
                continue

        agent_timestamps: list[datetime] = []
        for ts in data.get("agent_spawn_timestamps", []):
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                agent_timestamps.append(dt)
            except (ValueError, TypeError):
                continue

        # Backward compat: if no agent_spawn_timestamps but active_agents > 0,
        # create synthetic timestamps for legacy data
        if not agent_timestamps and data.get("active_agents", 0) > 0:
            now = datetime.now(timezone.utc)
            for _ in range(data["active_agents"]):
                agent_timestamps.append(now)

        return cls(
            session=session,
            tokens=data.get("tokens", 0),
            steps_since_confirm=data.get("steps_since_confirm", 0),
            tool_calls_timestamps=tool_timestamps,
            agent_spawn_timestamps=agent_timestamps,
            agent_decay_seconds=agent_decay_seconds,
            instruction_tokens=data.get("instruction_tokens", 0),
            skill_tokens_injected=data.get("skill_tokens_injected", 0),
            last_context_tokens=data.get("last_context_tokens", 0),
        )


class RiskEngine:
    """Core risk scoring engine."""

    def __init__(
        self,
        threshold: float = 70.0,
        on_threshold_exceeded: Callable[[Session, RiskScore], None] | None = None,
        max_tokens: int = 128000,
        max_agents: int = 5,
        max_steps: int = 10,
        max_tools_per_min: float = 20.0,
        agent_decay_seconds: int = DEFAULT_AGENT_DECAY_SECONDS,
    ) -> None:
        self.threshold = threshold
        self.on_threshold_exceeded = on_threshold_exceeded
        self.max_tokens = max_tokens
        self.max_agents = max_agents
        self.max_steps = max_steps
        self.max_tools_per_min = max_tools_per_min
        self.agent_decay_seconds = agent_decay_seconds
        self._sessions: dict[str, SessionState] = {}

    def get_session_state(self, session_id: str) -> SessionState | None:
        """Public API to access session state (replaces direct _sessions access)."""
        return self._sessions.get(session_id)

    def start_session(self, session: Session) -> None:
        """Begin tracking a new session."""
        self._sessions[session.id] = SessionState(
            session=session, agent_decay_seconds=self.agent_decay_seconds
        )

    def restore_session(self, session: Session, state_dict: dict[str, Any]) -> None:
        """Restore a session with persisted state."""
        self._sessions[session.id] = SessionState.from_dict(
            state_dict, session, agent_decay_seconds=self.agent_decay_seconds
        )

    def end_session(self, session_id: str) -> Session | None:
        """End and return a session."""
        state = self._sessions.pop(session_id, None)
        if state:
            state.session.ended_at = datetime.now(timezone.utc)
            return state.session
        return None

    def get_session(self, session_id: str) -> Session | None:
        """Get current session."""
        state = self._sessions.get(session_id)
        return state.session if state else None

    def process_event(self, event: RiskEvent) -> RiskScore:
        """Process an event and return updated risk score."""
        state = self._sessions.get(event.session_id)
        if not state:
            raise ValueError(f"Unknown session: {event.session_id}")

        state.prune_old_timestamps()

        if event.event_type == EventType.TOOL_CALL:
            state.tool_calls_timestamps.append(event.timestamp)
            state.steps_since_confirm += 1
            state.session.total_tool_calls += 1
            if event.context_tokens:
                state.tokens = event.context_tokens

        elif event.event_type == EventType.AGENT_SPAWN:
            state.agent_spawn_timestamps.append(event.timestamp)
            state.session.total_agent_spawns += 1

        elif event.event_type == EventType.CHECKPOINT:
            state.steps_since_confirm = 0
            state.tool_calls_timestamps.append(event.timestamp)
            state.session.total_tool_calls += 1

        elif event.event_type == EventType.RESPONSE:
            if event.context_tokens:
                state.tokens = event.context_tokens

        score = RiskScore.calculate(
            tokens=state.tokens,
            active_agents=state.active_agents,
            steps_since_confirm=state.steps_since_confirm,
            tools_per_minute=state.tools_per_minute,
            threshold=self.threshold,
            max_tokens=self.max_tokens,
            max_agents=self.max_agents,
            max_steps=self.max_steps,
            max_tools_per_min=self.max_tools_per_min,
            skill_tokens_injected=state.skill_tokens_injected,
            instruction_tokens=state.instruction_tokens,
        )

        if score.total > state.session.peak_risk_score:
            state.session.peak_risk_score = score.total

        if score.threshold_exceeded and self.on_threshold_exceeded:
            self.on_threshold_exceeded(state.session, score)

        event.risk_score = score
        return score

    def get_current_score(self, session_id: str) -> RiskScore | None:
        """Get current risk score for a session without processing new event."""
        state = self._sessions.get(session_id)
        if not state:
            return None

        return RiskScore.calculate(
            tokens=state.tokens,
            active_agents=state.active_agents,
            steps_since_confirm=state.steps_since_confirm,
            tools_per_minute=state.tools_per_minute,
            threshold=self.threshold,
            max_tokens=self.max_tokens,
            max_agents=self.max_agents,
            max_steps=self.max_steps,
            max_tools_per_min=self.max_tools_per_min,
            skill_tokens_injected=state.skill_tokens_injected,
            instruction_tokens=state.instruction_tokens,
        )


# ── Configuration ────────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict[str, Any] = {
    "threshold": 70.0,
    "retention_days": 90,
    "action_mode": "alert",
    "log_level": "INFO",
    "max_tokens": 128000,
    "max_agents": 5,
    "max_steps": 10,
    "max_tools_per_min": 20.0,
    "agent_decay_seconds": DEFAULT_AGENT_DECAY_SECONDS,
    "checkpoint_tools": "AskUserQuestion",
    "stale_session_hours": 2,
    "instruction_tokens": 0,
    "system_prompt_tokens": 2000,
    "displacement_weight": 0.10,
}

_LOG_DIR = Path.home() / ".rtfi"


def load_settings(log_dir: Path | None = None) -> dict[str, Any]:
    """Load settings from config file and environment variables.

    Priority (highest wins):
    1. Environment variables (RTFI_THRESHOLD, RTFI_ACTION_MODE, etc.)
    2. Config file (~/.rtfi/config.env)
    3. Legacy settings file (.claude/rtfi.local.md)
    4. Built-in defaults
    """
    import logging

    logger = logging.getLogger("rtfi")
    config: dict[str, Any] = dict(DEFAULT_SETTINGS)
    log_dir = log_dir or _LOG_DIR

    # Layer 1: Read config.env file
    config_path = log_dir / "config.env"
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

    # Layer 3: Environment variables override everything
    _env_overrides: list[tuple[str, str, type, Any, tuple[float, float] | None]] = [
        ("threshold", "RTFI_THRESHOLD", float, 70.0, (0, 100)),
        ("retention_days", "RTFI_RETENTION_DAYS", int, 90, (1, 3650)),
        ("action_mode", "RTFI_ACTION_MODE", str, "alert", None),
        ("max_tokens", "RTFI_MAX_TOKENS", int, 128000, (1000, 10_000_000)),
        ("max_agents", "RTFI_MAX_AGENTS", int, 5, (1, 1000)),
        ("max_steps", "RTFI_MAX_STEPS", int, 10, (1, 1000)),
        ("max_tools_per_min", "RTFI_MAX_TOOLS_PER_MIN", float, 20.0, (1, 1000)),
        ("agent_decay_seconds", "RTFI_AGENT_DECAY_SECONDS", int, DEFAULT_AGENT_DECAY_SECONDS, (10, 3600)),
        ("stale_session_hours", "RTFI_STALE_SESSION_HOURS", int, 2, (1, 168)),
        ("instruction_tokens", "RTFI_INSTRUCTION_TOKENS", int, 0, (0, 1_000_000)),
        ("system_prompt_tokens", "RTFI_SYSTEM_PROMPT_TOKENS", int, 2000, (0, 100_000)),
        ("displacement_weight", "RTFI_DISPLACEMENT_WEIGHT", float, 0.10, (0.0, 1.0)),
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
            try:
                config[key] = parser(config[key])
            except (ValueError, TypeError):
                config[key] = default

    # Validate action_mode
    if config["action_mode"] not in ("alert", "block", "confirm"):
        config["action_mode"] = "alert"

    # Parse checkpoint_tools (comma-separated string to set)
    ct = os.environ.get("RTFI_CHECKPOINT_TOOLS", str(config.get("checkpoint_tools", "")))
    config["checkpoint_tools"] = {t.strip() for t in ct.split(",") if t.strip()}

    logger.info(f"Loaded settings: threshold={config['threshold']}, mode={config['action_mode']}")
    return config
