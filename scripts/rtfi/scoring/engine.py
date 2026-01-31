"""Risk scoring engine - the core of RTFI."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from rtfi.models.events import EventType, RiskEvent, RiskScore, Session


@dataclass
class SessionState:
    """Mutable state for a monitored session."""

    session: Session
    tokens: int = 0
    active_agents: int = 0
    steps_since_confirm: int = 0
    tool_calls_timestamps: list[datetime] = field(default_factory=list)

    @property
    def tools_per_minute(self) -> float:
        """Calculate tool calls per minute over the last minute."""
        if not self.tool_calls_timestamps:
            return 0.0
        now = datetime.now()
        one_minute_ago = now.timestamp() - 60
        recent = [t for t in self.tool_calls_timestamps if t.timestamp() > one_minute_ago]
        return len(recent)

    def prune_old_timestamps(self) -> None:
        """Remove timestamps older than 2 minutes to prevent memory growth."""
        if len(self.tool_calls_timestamps) > 100:  # Only prune when list gets large
            now = datetime.now()
            two_minutes_ago = now.timestamp() - 120
            self.tool_calls_timestamps = [
                t for t in self.tool_calls_timestamps if t.timestamp() > two_minutes_ago
            ]


class RiskEngine:
    """Core risk scoring engine."""

    def __init__(
        self,
        threshold: float = 70.0,
        on_threshold_exceeded: Callable[[Session, RiskScore], None] | None = None,
    ):
        self.threshold = threshold
        self.on_threshold_exceeded = on_threshold_exceeded
        self._sessions: dict[str, SessionState] = {}

    def start_session(self, session: Session) -> None:
        """Begin tracking a new session."""
        self._sessions[session.id] = SessionState(session=session)

    def end_session(self, session_id: str) -> Session | None:
        """End and return a session."""
        state = self._sessions.pop(session_id, None)
        if state:
            state.session.ended_at = datetime.now()
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
            state.active_agents += 1
            state.session.total_agent_spawns += 1

        elif event.event_type == EventType.CHECKPOINT:
            state.steps_since_confirm = 0

        elif event.event_type == EventType.RESPONSE:
            if event.context_tokens:
                state.tokens = event.context_tokens

        # Calculate new risk score
        score = RiskScore.calculate(
            tokens=state.tokens,
            active_agents=state.active_agents,
            steps_since_confirm=state.steps_since_confirm,
            tools_per_minute=state.tools_per_minute,
            threshold=self.threshold,
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
        )
