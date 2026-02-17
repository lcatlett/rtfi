"""Risk scoring engine - the core of RTFI."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from rtfi.models.events import EventType, RiskEvent, RiskScore, Session

AGENT_DECAY_SECONDS = 300  # 5 minutes (H6)


@dataclass
class SessionState:
    """Mutable state for a monitored session."""

    session: Session
    tokens: int = 0
    steps_since_confirm: int = 0
    tool_calls_timestamps: list[datetime] = field(default_factory=list)
    agent_spawn_timestamps: list[datetime] = field(default_factory=list)  # H6: time-decay

    @property
    def active_agents(self) -> int:
        """Count agents spawned within the last 5 minutes (H6)."""
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - AGENT_DECAY_SECONDS
        return len([t for t in self.agent_spawn_timestamps if t.timestamp() > cutoff])

    @property
    def tools_per_minute(self) -> float:
        """Calculate tool calls per minute over the last minute."""
        if not self.tool_calls_timestamps:
            return 0.0
        now = datetime.now(timezone.utc)
        one_minute_ago = now.timestamp() - 60
        recent = [t for t in self.tool_calls_timestamps if t.timestamp() > one_minute_ago]
        return len(recent)

    def prune_old_timestamps(self) -> None:
        """Remove timestamps older than 10 minutes to prevent memory growth."""
        now = datetime.now(timezone.utc)
        ten_minutes_ago = now.timestamp() - 600
        if len(self.tool_calls_timestamps) > 100:
            two_minutes_ago = now.timestamp() - 120
            self.tool_calls_timestamps = [
                t for t in self.tool_calls_timestamps if t.timestamp() > two_minutes_ago
            ]
        # Prune agent timestamps older than 10 minutes (H6)
        if len(self.agent_spawn_timestamps) > 50:
            self.agent_spawn_timestamps = [
                t for t in self.agent_spawn_timestamps if t.timestamp() > ten_minutes_ago
            ]

    def to_dict(self) -> dict:
        """Serialize mutable state fields to a dict for persistence."""
        return {
            "tokens": self.tokens,
            "active_agents": len(self.agent_spawn_timestamps),  # total spawns for compat
            "steps_since_confirm": self.steps_since_confirm,
            "tool_timestamps": [t.isoformat() for t in self.tool_calls_timestamps],
            "agent_spawn_timestamps": [t.isoformat() for t in self.agent_spawn_timestamps],
        }

    @classmethod
    def from_dict(cls, data: dict, session: Session) -> "SessionState":
        """Restore session state from a persisted dict."""
        tool_timestamps = []
        for ts in data.get("tool_timestamps", []):
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                tool_timestamps.append(dt)
            except (ValueError, TypeError):
                continue

        agent_timestamps = []
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
            for i in range(data["active_agents"]):
                agent_timestamps.append(now)

        return cls(
            session=session,
            tokens=data.get("tokens", 0),
            steps_since_confirm=data.get("steps_since_confirm", 0),
            tool_calls_timestamps=tool_timestamps,
            agent_spawn_timestamps=agent_timestamps,
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
    ):
        self.threshold = threshold
        self.on_threshold_exceeded = on_threshold_exceeded
        self.max_tokens = max_tokens
        self.max_agents = max_agents
        self.max_steps = max_steps
        self.max_tools_per_min = max_tools_per_min
        self._sessions: dict[str, SessionState] = {}

    def start_session(self, session: Session) -> None:
        """Begin tracking a new session."""
        self._sessions[session.id] = SessionState(session=session)

    def restore_session(self, session: Session, state_dict: dict) -> None:
        """Restore a session with persisted state."""
        self._sessions[session.id] = SessionState.from_dict(state_dict, session)

    def end_session(self, session_id: str) -> Session | None:
        """End and return a session."""
        state = self._sessions.pop(session_id, None)
        if state:
            state.session.ended_at = datetime.now(timezone.utc)
            return state.session
        return None

    def get_session(self, session_id: str) -> Session | None:
        """Get current session state."""
        state = self._sessions.get(session_id)
        return state.session if state else None

    def process_event(self, event: RiskEvent) -> RiskScore:
        """Process an event and return updated risk score."""
        state = self._sessions.get(event.session_id)
        if not state:
            raise ValueError(f"Unknown session: {event.session_id}")

        # Prune old timestamps to prevent memory growth
        state.prune_old_timestamps()

        # Update state based on event type
        if event.event_type == EventType.TOOL_CALL:
            state.tool_calls_timestamps.append(event.timestamp)
            state.steps_since_confirm += 1
            state.session.total_tool_calls += 1
            if event.context_tokens:
                state.tokens = event.context_tokens

        elif event.event_type == EventType.AGENT_SPAWN:
            state.agent_spawn_timestamps.append(event.timestamp)  # H6: track timestamp
            state.session.total_agent_spawns += 1

        elif event.event_type == EventType.CHECKPOINT:
            state.steps_since_confirm = 0

        elif event.event_type == EventType.RESPONSE:
            if event.context_tokens:
                state.tokens = event.context_tokens

        # Calculate new risk score (L6: configurable thresholds)
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
        )

        # Track peak score
        if score.total > state.session.peak_risk_score:
            state.session.peak_risk_score = score.total

        # Fire callback if threshold exceeded
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
        )
