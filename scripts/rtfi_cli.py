#!/usr/bin/env python3
"""RTFI CLI - Command-line interface for the plugin."""

import argparse
import sys
from pathlib import Path

# Add rtfi package to path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

# Check dependencies (no auto-install — H5)
try:
    import pydantic  # noqa: F401
except ImportError:
    print("Error: Missing dependency 'pydantic'. Run: pip3 install pydantic>=2.0.0")
    sys.exit(1)

from rtfi.storage.database import Database


def cmd_sessions(args):
    """List recent sessions."""
    db = Database()
    sessions = db.get_recent_sessions(limit=args.limit, project_dir=args.project)

    if not sessions:
        print("No sessions recorded yet.")
        return

    print(f"\n{'ID':<12} {'Started':<16} {'Peak Risk':>10} {'Tools':>6} {'Agents':>7} {'Outcome':<12}")
    print("-" * 70)

    for s in sessions:
        risk_indicator = ""
        if s.peak_risk_score >= 70:
            risk_indicator = " (!)"
        elif s.peak_risk_score >= 50:
            risk_indicator = " (*)"

        print(
            f"{s.id[:8]+'...':<12} "
            f"{s.started_at.strftime('%Y-%m-%d %H:%M'):<16} "
            f"{s.peak_risk_score:>9.1f}{risk_indicator} "
            f"{s.total_tool_calls:>6} "
            f"{s.total_agent_spawns:>7} "
            f"{s.outcome.value:<12}"
        )

    print(f"\nShowing {len(sessions)} sessions. (!) = high risk, (*) = medium risk")


def cmd_risky(args):
    """Show high-risk sessions."""
    db = Database()
    sessions = db.get_high_risk_sessions(
        threshold=args.threshold, limit=args.limit, project_dir=args.project
    )

    if not sessions:
        print(f"No sessions exceeded risk threshold {args.threshold}.")
        return

    print(f"\nHigh-Risk Sessions (threshold: {args.threshold})")
    print(f"\n{'ID':<12} {'Started':<16} {'Peak Risk':>10} {'Tools':>6} {'Agents':>7}")
    print("-" * 60)

    for s in sessions:
        print(
            f"{s.id[:8]+'...':<12} "
            f"{s.started_at.strftime('%Y-%m-%d %H:%M'):<16} "
            f"{s.peak_risk_score:>10.1f} "
            f"{s.total_tool_calls:>6} "
            f"{s.total_agent_spawns:>7}"
        )

    print(f"\n{len(sessions)} sessions exceeded threshold.")


def cmd_show(args):
    """Show session details."""
    db = Database()

    # Use SQL prefix lookup (M3) instead of loading all sessions
    session = db.find_session_by_prefix(args.session_id)

    if not session:
        print(f"Session not found: {args.session_id}")
        return

    print(f"\nSession: {session.id}")
    print(f"Started: {session.started_at}")
    print(f"Ended: {session.ended_at or 'In progress'}")
    print(f"Outcome: {session.outcome.value}")
    print(f"Peak Risk Score: {session.peak_risk_score:.1f}")
    print(f"Final Risk Score: {session.final_risk_score or 'N/A'}")
    print(f"Total Tool Calls: {session.total_tool_calls}")
    print(f"Total Agent Spawns: {session.total_agent_spawns}")

    # Show events
    events = list(db.get_session_events(session.id))
    if events:
        print(f"\nEvents ({len(events)}):")
        print(f"{'Time':<10} {'Type':<12} {'Tool':<20} {'Risk':>8}")
        print("-" * 55)

        for e in events[:50]:
            risk_str = f"{e.risk_score.total:.1f}" if e.risk_score else "-"
            print(
                f"{e.timestamp.strftime('%H:%M:%S'):<10} "
                f"{e.event_type.value:<12} "
                f"{(e.tool_name or '-'):<20} "
                f"{risk_str:>8}"
            )

        if len(events) > 50:
            print(f"\n... and {len(events) - 50} more events")


def cmd_status(args):
    """Show RTFI status."""
    db = Database()
    stats = db.get_stats()

    print("\nRTFI Status")
    print("=" * 40)
    print(f"Database: {stats['database_path']}")
    print(f"Total Sessions: {stats['total_sessions']}")
    print(f"High-Risk Sessions: {stats['high_risk_sessions']}")
    print(f"Total Events: {stats['total_events']}")


def cmd_health(args):
    """Run health check."""
    import os

    print("\nRTFI Health Check")
    print("=" * 50)

    errors = []

    # Check dependencies
    try:
        import pydantic

        print(f"[OK] pydantic {pydantic.__version__}")
    except ImportError:
        errors.append("pydantic not installed")
        print("[ERROR] pydantic not installed")

    # Check database
    try:
        db = Database()
        stats = db.get_stats()
        print(f"[OK] Database: {stats['database_path']}")
        print(f"     Sessions: {stats['total_sessions']}, Events: {stats['total_events']}")
    except Exception as e:
        errors.append(f"Database error: {e}")
        print(f"[ERROR] Database: {e}")

    # Check log files
    log_dir = Path.home() / ".rtfi"
    log_file = log_dir / "rtfi.log"
    audit_file = log_dir / "audit.log"

    if log_file.exists():
        print(f"[OK] Log file: {log_file}")
    else:
        print(f"[WARN] Log file not found: {log_file}")

    if audit_file.exists():
        print(f"[OK] Audit log: {audit_file}")
    else:
        print(f"[WARN] Audit log not found: {audit_file}")

    # Check environment settings
    threshold = os.environ.get("RTFI_THRESHOLD", "70.0 (default)")
    action_mode = os.environ.get("RTFI_ACTION_MODE", "alert (default)")
    retention = os.environ.get("RTFI_RETENTION_DAYS", "90 (default)")

    print(f"\nSettings:")
    print(f"  Threshold: {threshold}")
    print(f"  Action Mode: {action_mode}")
    print(f"  Retention Days: {retention}")

    # Summary
    if errors:
        print(f"\n[FAIL] Health check failed with {len(errors)} error(s)")
        return 1
    else:
        print(f"\n[PASS] All systems operational")
        return 0


def main():
    parser = argparse.ArgumentParser(description="RTFI CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # sessions command
    sessions_parser = subparsers.add_parser("sessions", help="List recent sessions")
    sessions_parser.add_argument("--limit", "-n", type=int, default=20)
    sessions_parser.add_argument("--project", "-p", type=str, default=None,
                                 help="Filter by project directory")

    # risky command
    risky_parser = subparsers.add_parser("risky", help="Show high-risk sessions")
    risky_parser.add_argument("--threshold", "-t", type=float, default=70.0)
    risky_parser.add_argument("--limit", "-n", type=int, default=20)
    risky_parser.add_argument("--project", "-p", type=str, default=None,
                              help="Filter by project directory")

    # show command
    show_parser = subparsers.add_parser("show", help="Show session details")
    show_parser.add_argument("session_id", help="Session ID or prefix")

    # status command
    subparsers.add_parser("status", help="Show RTFI status")

    # health command
    subparsers.add_parser("health", help="Run health check")

    args = parser.parse_args()

    commands = {
        "sessions": cmd_sessions,
        "risky": cmd_risky,
        "show": cmd_show,
        "status": cmd_status,
        "health": cmd_health,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
