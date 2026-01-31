"""Event and session data models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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


class RiskScore(BaseModel):
    """Calculated risk score with component breakdown."""

    total: float = Field(ge=0, le=100, description="Overall risk score 0-100")
    context_length: float = Field(ge=0, le=1, description="Context length factor")
    agent_fanout: float = Field(ge=0, le=1, description="Agent fanout factor")
    autonomy_depth: float = Field(ge=0, le=1, description="Autonomy depth factor")
    decision_velocity: float = Field(ge=0, le=1, description="Decision velocity factor")
    threshold_exceeded: bool = False

    @classmethod
    def calculate(
        cls,
        tokens: int,
        active_agents: int,
        steps_since_confirm: int,
        tools_per_minute: float,
        threshold: float = 70.0,
    ) -> "RiskScore":
        """Calculate risk score from session state."""
        weights = {
            "context_length": 0.25,
            "agent_fanout": 0.30,
            "autonomy_depth": 0.25,
            "decision_velocity": 0.20,
        }

        factors = {
            "context_length": min(1.0, tokens / 128000),
            "agent_fanout": min(1.0, active_agents / 5),
            "autonomy_depth": min(1.0, steps_since_confirm / 10),
            "decision_velocity": min(1.0, tools_per_minute / 20),
        }

        total = sum(factors[k] * weights[k] for k in weights) * 100

        return cls(
            total=round(total, 2),
            context_length=round(factors["context_length"], 3),
            agent_fanout=round(factors["agent_fanout"], 3),
            autonomy_depth=round(factors["autonomy_depth"], 3),
            decision_velocity=round(factors["decision_velocity"], 3),
            threshold_exceeded=total >= threshold,
        )


class RiskEvent(BaseModel):
    """A single event in a session that affects risk score."""

    id: int | None = None
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    event_type: EventType
    tool_name: str | None = None
    context_tokens: int = 0
    risk_score: RiskScore | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    """An RTFI-monitored session."""

    id: str
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = None
    instruction_source: str | None = None
    instruction_hash: str | None = None
    final_risk_score: float | None = None
    outcome: SessionOutcome = SessionOutcome.IN_PROGRESS
    peak_risk_score: float = 0.0
    total_tool_calls: int = 0
    total_agent_spawns: int = 0
