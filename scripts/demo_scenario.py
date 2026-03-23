#!/usr/bin/env python3
"""RTFI Demo Scenario — drives the dashboard gauge with a synthetic high-risk session.

Run this with the RTFI dashboard open to watch the gauge climb in real-time.
Simulates a non-compliant Claude session: agent fan-out, rapid tool calls,
no human checkpoints — the risk score escalates to threshold breach.

Usage:
    # Terminal 1: start dashboard (keep it open)
    python3 scripts/rtfi_dashboard.py --no-browser
    open http://localhost:7430

    # Terminal 2: run demo
    python3 scripts/demo_scenario.py                    # combined scenario
    python3 scripts/demo_scenario.py --scenario fanout   # pure agent fan-out
    python3 scripts/demo_scenario.py --scenario velocity # rapid tool calls
    python3 scripts/demo_scenario.py --fast              # no delays (instant)
"""

import argparse
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from rtfi_core import Database, EventType, RiskEngine, RiskEvent, RiskScore, Session, SessionOutcome, SessionState

THRESHOLD = 70.0

# ANSI colors
_G = "\033[32m"   # green
_Y = "\033[33m"   # amber
_R = "\033[31m"   # red
_B = "\033[34m"   # blue
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _color_for(score: float) -> str:
    if score >= THRESHOLD:
        return _R
    if score >= THRESHOLD * 0.7:
        return _Y
    return _G


def _bar(score: float, width: int = 40) -> str:
    filled = int(score / 100 * width)
    c = _color_for(score)
    return c + "█" * filled + _DIM + "░" * (width - filled) + _RESET


def _print_score(score: RiskScore, event_label: str = ""):
    c = _color_for(score.total)
    bar = _bar(score.total)
    label = "NORMAL  " if score.total < THRESHOLD * 0.7 else ("ELEVATED" if score.total < THRESHOLD else "HIGH RISK")
    marker = f" {_R}◄ THRESHOLD EXCEEDED{_RESET}" if score.threshold_exceeded else ""
    print(f"  {bar} {c}{_BOLD}{score.total:5.1f}{_RESET} {c}{label}{_RESET}{marker}")
    if event_label:
        print(f"        {_DIM}{event_label}{_RESET}")


def _section(title: str):
    print(f"\n{_BOLD}{'─' * 60}{_RESET}")
    print(f"{_BOLD}  {title}{_RESET}")
    print(f"{_BOLD}{'─' * 60}{_RESET}")


def _step(n: int, label: str):
    print(f"\n  {_DIM}[{n:02d}]{_RESET} {label}")


def run_scenario(scenario: str, delay: float, db: Database, engine: RiskEngine) -> str:
    """Run a named scenario. Returns the session_id created."""
    session_id = str(uuid.uuid4())
    session = Session(
        id=session_id,
        project_dir=str(Path.cwd()),
    )
    engine.start_session(session)
    db.save_session(session)

    def emit(event_type: EventType, tool: str, tokens: int = 0, label: str = ""):
        event = RiskEvent(
            session_id=session_id,
            event_type=event_type,
            tool_name=tool,
            context_tokens=tokens,
            metadata={"demo": True, "scenario": scenario},
        )
        score = engine.process_event(event)
        db.save_event(event)
        # Persist state so dashboard /frag/live reflects it immediately
        state = engine.get_session_state(session_id)
        if state:
            db.save_session(state.session)
            db.save_session_state(session_id, state.to_dict())
        _print_score(score, label or f"{event_type.value}: {tool}")
        if delay:
            time.sleep(delay)
        return score

    def checkpoint(label: str = "Human confirmation"):
        """Inject a checkpoint event (resets autonomy_depth counter)."""
        event = RiskEvent(
            session_id=session_id,
            event_type=EventType.CHECKPOINT,
            tool_name=None,
            metadata={"demo": True, "label": label},
        )
        engine.process_event(event)
        db.save_event(event)
        state = engine.get_session_state(session_id)
        if state:
            db.save_session(state.session)
            db.save_session_state(session_id, state.to_dict())
        print(f"  {_G}  ✓ CHECKPOINT: {label}{_RESET}")
        if delay:
            time.sleep(delay * 0.5)

    if scenario == "fanout":
        _section("Scenario: Agent Fan-out (weight ×0.30)")
        print(f"  {_DIM}Simulates Claude spawning parallel subagents without permission bounds.{_RESET}")
        print(f"  {_DIM}Session: {session_id[:16]}...{_RESET}")

        _step(1, "Normal tool calls — baseline")
        emit(EventType.TOOL_CALL, "Read", 8000, "Reading files")
        emit(EventType.TOOL_CALL, "Bash", 9000, "Running command")

        _step(2, "First agent spawn")
        emit(EventType.AGENT_SPAWN, "Task", 12000, "Spawning research agent")
        emit(EventType.TOOL_CALL, "Read", 14000, "Agent reads more files")

        _step(3, "Second agent spawn — fanout begins")
        emit(EventType.AGENT_SPAWN, "Task", 18000, "Spawning implementation agent")
        emit(EventType.TOOL_CALL, "Write", 20000, "Agent writes code")

        _step(4, "Third agent spawn — fanout accelerating")
        emit(EventType.AGENT_SPAWN, "Task", 25000, "Spawning test agent")
        emit(EventType.TOOL_CALL, "Bash", 28000, "Running tests")

        _step(5, "Fourth agent spawn — threshold zone")
        emit(EventType.AGENT_SPAWN, "Task", 32000, "Spawning deploy agent")
        emit(EventType.TOOL_CALL, "Bash", 35000, "Deploy script running")
        emit(EventType.TOOL_CALL, "Bash", 36000, "Checking deploy status")
        emit(EventType.TOOL_CALL, "Read", 37000, "Reading deploy log")
        emit(EventType.TOOL_CALL, "Bash", 38000, "Verifying services")

        _step(6, "Fifth agent spawn — BREACH")
        emit(EventType.AGENT_SPAWN, "Task", 42000, "Spawning monitoring agent")
        emit(EventType.TOOL_CALL, "Bash", 45000, "Monitor script started")

    elif scenario == "velocity":
        _section("Scenario: Decision Velocity + Autonomy Drift")
        print(f"  {_DIM}Simulates Claude making rapid tool calls without pausing for confirmation.{_RESET}")
        print(f"  {_DIM}Session: {session_id[:16]}...{_RESET}")

        _step(1, "Slow start — user approves")
        emit(EventType.TOOL_CALL, "Read", 5000, "Reading CLAUDE.md")
        checkpoint("User says: proceed with refactor")

        _step(2, "Speed picks up — autonomy climbing")
        emit(EventType.TOOL_CALL, "Read", 8000, "Reading source files")
        emit(EventType.TOOL_CALL, "Read", 9000)
        emit(EventType.TOOL_CALL, "Read", 10000)
        emit(EventType.TOOL_CALL, "Edit", 12000, "Making changes")
        emit(EventType.TOOL_CALL, "Edit", 13000)

        _step(3, "Velocity increasing — no checkpoint asked")
        # Rapid calls in quick succession override delay
        _orig_delay = delay
        rapid_delay = min(delay, 0.1)
        for tool in ["Edit", "Edit", "Bash", "Write", "Edit", "Bash", "Read", "Edit"]:
            event = RiskEvent(
                session_id=session_id,
                event_type=EventType.TOOL_CALL,
                tool_name=tool,
                context_tokens=15000 + len(tool) * 1000,
                metadata={"demo": True, "rapid": True},
            )
            score = engine.process_event(event)
            db.save_event(event)
            state = engine.get_session_state(session_id)
            if state:
                db.save_session(state.session)
                db.save_session_state(session_id, state.to_dict())
            _print_score(score, f"rapid: {tool}")
            if rapid_delay:
                time.sleep(rapid_delay)

        _step(4, "Autonomy maxed — BREACH")
        emit(EventType.TOOL_CALL, "Bash", 20000, "Running destructive command without asking")

    else:  # combined (default)
        _section("Scenario: Combined — Realistic High-Risk Session")
        print(f"  {_DIM}Simulates a session that violates multiple constraints simultaneously.{_RESET}")
        print(f"  {_DIM}Session: {session_id[:16]}...{_RESET}")
        print()
        print(f"  {_DIM}Declared constraints:{_RESET}")
        print(f"  {_DIM}  • Ask before any destructive Bash command{_RESET}")
        print(f"  {_DIM}  • Maximum 2 parallel agents{_RESET}")
        print(f"  {_DIM}  • Confirm every 5 tool calls{_RESET}")

        _step(1, "Session starts — instructions loaded")
        emit(EventType.TOOL_CALL, "Read", 6000, "Claude reads CLAUDE.md and project files")
        checkpoint("User: implement the feature")

        _step(2, "First few steps — compliant")
        emit(EventType.TOOL_CALL, "Read", 10000, "Reading codebase")
        emit(EventType.TOOL_CALL, "Glob", 11000, "Finding relevant files")
        emit(EventType.TOOL_CALL, "Grep", 12000, "Searching for patterns")

        _step(3, "Agent spawned (1 of 2 — within limit)")
        emit(EventType.AGENT_SPAWN, "Task", 15000, "Spawning research subagent [OK — within limit]")
        emit(EventType.TOOL_CALL, "Read", 18000, "Agent reading more files")
        emit(EventType.TOOL_CALL, "Read", 19000)

        _step(4, "Second agent spawned (2 of 2 — at limit)")
        emit(EventType.AGENT_SPAWN, "Task", 22000, "Spawning implementation agent [OK — at limit]")

        _step(5, "VIOLATION: Third agent spawned without asking (exceeds limit)")
        emit(EventType.AGENT_SPAWN, "Task", 28000, "⚠ Spawning 3rd agent — instruction says max 2")

        _step(6, "Autonomy drift — no checkpoint after 8 tool calls")
        emit(EventType.TOOL_CALL, "Edit", 30000, "Editing source file")
        emit(EventType.TOOL_CALL, "Edit", 32000)
        emit(EventType.TOOL_CALL, "Write", 34000, "Writing new file")
        emit(EventType.TOOL_CALL, "Write", 35000)

        _step(7, "Fourth agent spawn (exceeds limit further)")
        emit(EventType.AGENT_SPAWN, "Task", 38000, "Spawning validation agent — still no confirmation")
        emit(EventType.TOOL_CALL, "Bash", 40000, "Validation script running")

        _step(8, "Fifth agent + VIOLATION: Destructive Bash without asking — BREACH")
        emit(EventType.AGENT_SPAWN, "Task", 42000, "Spawning cleanup agent — 5th agent, limit was 2")
        emit(EventType.TOOL_CALL, "Bash", 45000, "⚠ 'rm -rf old_build/' — no confirmation requested")

    return session_id


def finalize_session(session_id: str, engine: RiskEngine, db: Database) -> Session:
    """End the session and persist final state."""
    session = engine.end_session(session_id)
    if not session:
        session = db.get_session(session_id)
    if session:
        session.outcome = SessionOutcome.COMPLETED
        session.ended_at = datetime.now(timezone.utc)
        # Compute final score from state
        state_dict = db.load_session_state(session_id)
        if state_dict:
            temp_state = SessionState.from_dict(state_dict, session)
            final_score = RiskScore.calculate(
                tokens=temp_state.tokens,
                active_agents=temp_state.active_agents,
                steps_since_confirm=temp_state.steps_since_confirm,
                tools_per_minute=temp_state.tools_per_minute,
                threshold=THRESHOLD,
            )
            session.final_risk_score = final_score.total
        else:
            session.final_risk_score = session.peak_risk_score
        db.save_session(session)
    return session


def print_summary(session: Session, session_id: str):
    _section("Session Summary")
    c = _color_for(session.peak_risk_score)
    print(f"  Session ID  : {session_id}")
    print(f"  Peak Risk   : {c}{_BOLD}{session.peak_risk_score:.1f}{_RESET}")
    print(f"  Final Risk  : {session.final_risk_score:.1f}" if session.final_risk_score else "  Final Risk  : —")
    print(f"  Tool Calls  : {session.total_tool_calls}")
    print(f"  Agent Spawns: {session.total_agent_spawns}")
    print(f"  Outcome     : {session.outcome.value}")
    print()
    print(f"  {_BOLD}Next steps:{_RESET}")
    print(f"  {_DIM}  • Dashboard: http://localhost:7430 — click this session row{_RESET}")
    print(f"  {_DIM}  • CLI:       python3 scripts/rtfi_cli.py show {session_id[:8]}{_RESET}")
    print(f"  {_DIM}  • Analyze:   python3 scripts/demo_compliance_check.py {session_id[:8]}{_RESET}")

    if session.peak_risk_score >= THRESHOLD:
        print()
        print(f"  {_R}{_BOLD}⚠  Threshold exceeded — RTFI would have issued this warning to Claude:{_RESET}")
        print(f"  {_R}  \"RTFI WARNING: Risk score {session.peak_risk_score:.1f} exceeds threshold {THRESHOLD}.{_RESET}")
        print(f"  {_R}   High probability of instruction non-compliance.\"{_RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="RTFI Demo — runs a synthetic high-risk session against the live database"
    )
    parser.add_argument(
        "--scenario",
        choices=["fanout", "velocity", "combined"],
        default="combined",
        help="Which risk scenario to simulate (default: combined)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="No delays between events (for testing, not live demo)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="Seconds between events (default: 0.6)",
    )
    args = parser.parse_args()
    delay = 0.0 if args.fast else args.delay

    print(f"\n{_BOLD}RTFI Demo Scenario{_RESET}")
    print(f"{_DIM}Scenario: {args.scenario} | Delay: {delay}s{_RESET}")
    print(f"{_DIM}Writing events to live database — dashboard will update in real-time.{_RESET}")

    db = Database()
    engine = RiskEngine(threshold=THRESHOLD)

    try:
        session_id = run_scenario(args.scenario, delay, db, engine)
        session = finalize_session(session_id, engine, db)
        if session:
            print_summary(session, session_id)
        print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
