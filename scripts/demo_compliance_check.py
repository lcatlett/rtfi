#!/usr/bin/env python3
"""RTFI Compliance Check — validates whether a session followed declared constraints.

Given a session ID and an optional set of CLAUDE.md-style constraints, this tool:
  1. Loads the event sequence from the database
  2. Replays the session to reconstruct per-event risk scores
  3. Checks each constraint against actual behavior
  4. Produces a concrete "instruction X was violated at step Y" report

Usage:
    python3 scripts/demo_compliance_check.py <session-id-prefix>
    python3 scripts/demo_compliance_check.py <session-id-prefix> --constraints constraints.json
    python3 scripts/demo_compliance_check.py --latest
    python3 scripts/demo_compliance_check.py --latest --json

Constraints file format (JSON):
    [
      {"id": "C1", "name": "Max parallel agents", "max_agents": 2},
      {"id": "C2", "name": "Confirm every N steps", "max_steps_without_confirm": 5},
      {"id": "C3", "name": "No destructive Bash without ask", "destructive_tools": ["Bash"]},
      {"id": "C4", "name": "Context window guard", "max_tokens": 80000}
    ]
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from rtfi_core import Database, EventType, RiskEngine, RiskEvent, RiskScore, SessionOutcome, SessionState

# ANSI
_G = "\033[32m"
_Y = "\033[33m"
_R = "\033[31m"
_B = "\033[34m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

THRESHOLD = 70.0

# Default constraints — representative of a typical CLAUDE.md
DEFAULT_CONSTRAINTS = [
    {
        "id": "C1",
        "name": "Max 2 parallel agents",
        "description": "Do not spawn more than 2 subagents without asking",
        "max_agents": 2,
    },
    {
        "id": "C2",
        "name": "Confirm every 5 tool calls",
        "description": "Ask for confirmation after every 5 consecutive tool calls without a checkpoint",
        "max_steps_without_confirm": 5,
    },
    {
        "id": "C3",
        "name": "Context window guard",
        "description": "Warn if context exceeds 80k tokens (instructions may be degraded)",
        "max_tokens": 80000,
    },
    {
        "id": "C4",
        "name": "Risk threshold",
        "description": f"Overall risk score must stay below {THRESHOLD}",
        "max_risk_score": THRESHOLD,
    },
]


@dataclass
class Violation:
    constraint_id: str
    constraint_name: str
    step: int
    tool: str | None
    event_type: str
    timestamp: datetime
    evidence: str
    severity: str  # "warning" | "violation"


@dataclass
class ConstraintCheck:
    constraint: dict
    violations: list[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(v.severity == "violation" for v in self.violations)

    @property
    def status(self) -> str:
        if not self.violations:
            return "PASS"
        if self.passed:
            return "WARN"
        return "FAIL"


def replay_session(
    events: list[RiskEvent],
    session,
    engine: RiskEngine,
) -> list[tuple[RiskEvent, RiskScore | None]]:
    """Replay event sequence through scoring engine, return (event, score) pairs."""
    engine.start_session(session)
    results = []
    for event in events:
        try:
            if event.event_type in (EventType.TOOL_CALL, EventType.AGENT_SPAWN, EventType.RESPONSE, EventType.CHECKPOINT):
                score = engine.process_event(event)
                results.append((event, score))
            else:
                results.append((event, None))
        except Exception:
            results.append((event, None))
    return results


def check_constraints(
    replay: list[tuple[RiskEvent, RiskScore | None]],
    constraints: list[dict],
) -> list[ConstraintCheck]:
    checks = [ConstraintCheck(constraint=c) for c in constraints]

    active_agents = 0
    steps_since_confirm = 0

    for step_num, (event, score) in enumerate(replay, 1):
        ts = event.timestamp

        if event.event_type == EventType.AGENT_SPAWN:
            active_agents += 1
            steps_since_confirm += 1
        elif event.event_type == EventType.TOOL_CALL:
            steps_since_confirm += 1
        elif event.event_type == EventType.CHECKPOINT:
            steps_since_confirm = 0

        for check in checks:
            c = check.constraint
            cid = c["id"]

            # C1: max agents
            if "max_agents" in c and event.event_type == EventType.AGENT_SPAWN:
                if active_agents > c["max_agents"]:
                    check.violations.append(Violation(
                        constraint_id=cid,
                        constraint_name=c["name"],
                        step=step_num,
                        tool=event.tool_name,
                        event_type=event.event_type.value,
                        timestamp=ts,
                        evidence=(
                            f"Agent #{active_agents} spawned — exceeds limit of {c['max_agents']}. "
                            f"Tool: {event.tool_name or 'Task'}"
                        ),
                        severity="violation",
                    ))

            # C2: max steps without confirm
            if "max_steps_without_confirm" in c:
                if steps_since_confirm > c["max_steps_without_confirm"]:
                    if event.event_type in (EventType.TOOL_CALL, EventType.AGENT_SPAWN):
                        # Only record first breach and every 3rd thereafter to reduce noise
                        breach_n = steps_since_confirm - c["max_steps_without_confirm"]
                        if breach_n == 1 or breach_n % 3 == 0:
                            check.violations.append(Violation(
                                constraint_id=cid,
                                constraint_name=c["name"],
                                step=step_num,
                                tool=event.tool_name,
                                event_type=event.event_type.value,
                                timestamp=ts,
                                evidence=(
                                    f"Step {steps_since_confirm} without confirmation "
                                    f"(limit: {c['max_steps_without_confirm']}). "
                                    f"Tool: {event.tool_name or '—'}"
                                ),
                                severity="violation",
                            ))

            # C3: context token guard
            if "max_tokens" in c and score and score.context_length > 0:
                # Reconstruct approximate token count from context_length factor
                # context_length = tokens / max_tokens_engine; use event.context_tokens directly
                if event.context_tokens and event.context_tokens > c["max_tokens"]:
                    # Only warn once
                    if not any(v.constraint_id == cid for v in check.violations):
                        check.violations.append(Violation(
                            constraint_id=cid,
                            constraint_name=c["name"],
                            step=step_num,
                            tool=event.tool_name,
                            event_type=event.event_type.value,
                            timestamp=ts,
                            evidence=(
                                f"Context at {event.context_tokens:,} tokens — "
                                f"exceeds {c['max_tokens']:,} token guard. "
                                f"Instructions may be degraded."
                            ),
                            severity="warning",
                        ))

            # C4: overall risk threshold
            if "max_risk_score" in c and score:
                if score.threshold_exceeded and score.total >= c["max_risk_score"]:
                    if not any(v.constraint_id == cid and v.step == step_num for v in check.violations):
                        check.violations.append(Violation(
                            constraint_id=cid,
                            constraint_name=c["name"],
                            step=step_num,
                            tool=event.tool_name,
                            event_type=event.event_type.value,
                            timestamp=ts,
                            evidence=(
                                f"Risk score {score.total:.1f} ≥ threshold {c['max_risk_score']}. "
                                f"Factors: context={score.context_length:.2f}, "
                                f"agents={score.agent_fanout:.2f}, "
                                f"autonomy={score.autonomy_depth:.2f}, "
                                f"velocity={score.decision_velocity:.2f}"
                            ),
                            severity="violation",
                        ))

    return checks


def _status_icon(status: str) -> str:
    return {
        "PASS": f"{_G}✓ PASS{_RESET}",
        "WARN": f"{_Y}⚠ WARN{_RESET}",
        "FAIL": f"{_R}✗ FAIL{_RESET}",
    }.get(status, status)


def _severity_icon(severity: str) -> str:
    if severity == "violation":
        return f"{_R}VIOLATION{_RESET}"
    return f"{_Y}WARNING  {_RESET}"


def print_report(session, checks: list[ConstraintCheck], replay: list):
    total = len(checks)
    passed = sum(1 for c in checks if c.status == "PASS")
    warned = sum(1 for c in checks if c.status == "WARN")
    failed = sum(1 for c in checks if c.status == "FAIL")

    print(f"\n{_BOLD}{'═' * 64}{_RESET}")
    print(f"{_BOLD}  RTFI COMPLIANCE REPORT{_RESET}")
    print(f"{_BOLD}{'═' * 64}{_RESET}")
    print(f"  Session  : {session.id}")
    print(f"  Started  : {session.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Events   : {len(replay)}")
    print(f"  Peak Risk: {_color_for(session.peak_risk_score)}{_BOLD}{session.peak_risk_score:.1f}{_RESET}")
    print(f"  Outcome  : {session.outcome.value}")
    print()
    print(f"  Constraints: {_G}{passed} passed{_RESET} / {_Y}{warned} warnings{_RESET} / {_R}{failed} failed{_RESET} of {total} total")

    overall = "COMPLIANT" if failed == 0 else "NON-COMPLIANT"
    overall_color = _G if failed == 0 else _R
    print(f"  Verdict  : {overall_color}{_BOLD}{overall}{_RESET}")
    print(f"\n{_BOLD}{'─' * 64}{_RESET}")

    for check in checks:
        c = check.constraint
        icon = _status_icon(check.status)
        print(f"\n  [{icon}] {_BOLD}{c['id']}: {c['name']}{_RESET}")
        if c.get("description"):
            print(f"          {_DIM}{c['description']}{_RESET}")

        if not check.violations:
            print(f"          {_DIM}No violations detected{_RESET}")
        else:
            for v in check.violations:
                ts = v.timestamp.strftime("%H:%M:%S")
                print(f"\n          {_severity_icon(v.severity)} at step {v.step} ({ts})")
                print(f"          {_DIM}Event: {v.event_type}{' → ' + v.tool if v.tool else ''}{_RESET}")
                print(f"          {v.evidence}")

    # Score decomposition
    print(f"\n{_BOLD}{'─' * 64}{_RESET}")
    print(f"{_BOLD}  Risk Score Decomposition{_RESET}")
    print(f"  {'Factor':<20} {'Weight':>7}  {'Contribution'}")
    print(f"  {'─' * 50}")
    if replay:
        last_score = next(
            (score for _, score in reversed(replay) if score is not None), None
        )
        if last_score:
            factors = [
                ("Context Length",  last_score.context_length,  0.25),
                ("Agent Fanout",    last_score.agent_fanout,    0.30),
                ("Autonomy Depth",  last_score.autonomy_depth,  0.25),
                ("Decision Velocity", last_score.decision_velocity, 0.20),
            ]
            for name, val, weight in factors:
                contrib = val * weight * 100
                c = _color_for(val * 100)
                bar = c + "█" * int(val * 20) + _DIM + "░" * (20 - int(val * 20)) + _RESET
                print(f"  {name:<20} ×{weight:.2f}   {bar}  {c}{contrib:5.1f}pts{_RESET}")
            print(f"  {'─' * 50}")
            tc = _color_for(last_score.total)
            print(f"  {'TOTAL':<20}        {'':21}  {tc}{_BOLD}{last_score.total:5.1f} / 100{_RESET}")

    # What RTFI told Claude
    threshold_events = [(s, e) for (e, s) in replay if s and s.threshold_exceeded]
    if threshold_events:
        print(f"\n{_BOLD}{'─' * 64}{_RESET}")
        print(f"{_BOLD}  ⚠  What RTFI Told Claude (systemMessage at threshold breach){_RESET}")
        first_score = threshold_events[0][0]
        print(f"\n  {_R}\"RTFI WARNING: Risk score {first_score.total:.1f} exceeds threshold {THRESHOLD}.")
        print(f"   Factors: context={first_score.context_length:.2f}, agents={first_score.agent_fanout:.2f},")
        print(f"   autonomy={first_score.autonomy_depth:.2f}, velocity={first_score.decision_velocity:.2f}.")
        print(f"   High probability of instruction non-compliance.\"{_RESET}")

    print(f"\n{_BOLD}{'─' * 64}{_RESET}")
    print(f"  {_DIM}Improve instructions: use 'RTFI session-analyzer' agent for CLAUDE.md suggestions{_RESET}")
    print(f"{_BOLD}{'═' * 64}{_RESET}\n")


def _color_for(score: float) -> str:
    if score >= THRESHOLD:
        return _R
    if score >= THRESHOLD * 0.7:
        return _Y
    return _G


def main():
    parser = argparse.ArgumentParser(
        description="RTFI Compliance Check — validate a session against declared constraints"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("session_id", nargs="?", help="Session ID or prefix (8+ chars)")
    group.add_argument("--latest", action="store_true", help="Use the most recent session")

    parser.add_argument(
        "--constraints",
        type=Path,
        help="Path to JSON file with constraint definitions (uses defaults if omitted)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report as JSON instead of human-readable",
    )
    args = parser.parse_args()

    db = Database()
    try:
        # Resolve session
        if args.latest:
            sessions = db.get_recent_sessions(limit=1)
            if not sessions:
                print("No sessions found.", file=sys.stderr)
                sys.exit(1)
            session = sessions[0]
        else:
            session = db.get_session(args.session_id)
            if not session:
                session = db.find_session_by_prefix(args.session_id)
            if not session:
                print(f"Session not found: {args.session_id}", file=sys.stderr)
                sys.exit(1)

        # Load constraints
        if args.constraints:
            constraints = json.loads(args.constraints.read_text())
        else:
            constraints = DEFAULT_CONSTRAINTS

        # Load events
        events = list(db.get_session_events(session.id))
        if not events:
            print(f"No events found for session {session.id[:16]}...", file=sys.stderr)
            sys.exit(1)

        # Replay through engine
        engine = RiskEngine(threshold=THRESHOLD)
        replay = replay_session(events, session, engine)

        # Check constraints
        checks = check_constraints(replay, constraints)

        if args.json:
            output = {
                "session_id": session.id,
                "peak_risk": session.peak_risk_score,
                "event_count": len(events),
                "outcome": session.outcome.value,
                "verdict": "NON-COMPLIANT" if any(c.status == "FAIL" for c in checks) else "COMPLIANT",
                "constraints": [
                    {
                        "id": c.constraint["id"],
                        "name": c.constraint["name"],
                        "status": c.status,
                        "violations": [
                            {
                                "step": v.step,
                                "severity": v.severity,
                                "event_type": v.event_type,
                                "tool": v.tool,
                                "evidence": v.evidence,
                                "timestamp": v.timestamp.isoformat(),
                            }
                            for v in c.violations
                        ],
                    }
                    for c in checks
                ],
            }
            print(json.dumps(output, indent=2))
        else:
            print_report(session, checks, replay)

    finally:
        db.close()


if __name__ == "__main__":
    main()
