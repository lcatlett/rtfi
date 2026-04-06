#!/usr/bin/env python3
"""RTFI CLI - Command-line interface for the plugin."""

import argparse
import sys
from pathlib import Path

# Add rtfi package to path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from rtfi_core import Database, load_settings, risk_level


def cmd_sessions(args):
    """List recent sessions."""
    db = Database()
    sessions = db.get_recent_sessions(limit=args.limit, project_dir=args.project)

    if not sessions:
        print("No sessions recorded yet.")
        return

    print(
        f"\n{'ID':<12} {'Started':<16} {'Peak Risk':>10} {'Tools':>6} {'Agents':>7} {'Outcome':<12}"
    )
    print("-" * 70)

    for s in sessions:
        level = risk_level(s.peak_risk_score)
        risk_indicator = ""
        if level == "HIGH RISK":
            risk_indicator = " (!)"
        elif level == "ELEVATED":
            risk_indicator = " (*)"

        print(
            f"{s.id[:8] + '...':<12} "
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
            f"{s.id[:8] + '...':<12} "
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


def cmd_checkpoint(args: argparse.Namespace) -> None:
    """Reset autonomy depth for current session (manual checkpoint)."""
    import os
    from pathlib import Path

    session_id = os.environ.get("RTFI_SESSION_ID", "")
    if not session_id:
        session_file = Path.home() / ".rtfi" / "current_session"
        if session_file.exists():
            session_id = session_file.read_text().strip()

    if not session_id:
        print("No active session found. Set RTFI_SESSION_ID or start a session first.")
        return

    db = Database()
    state_dict = db.load_session_state(session_id)
    if not state_dict:
        print(f"No state found for session {session_id[:8]}...")
        return

    state_dict["steps_since_confirm"] = 0
    db.save_session_state(session_id, state_dict)

    from rtfi_core import EventType, RiskEvent

    event = RiskEvent(
        session_id=session_id,
        event_type=EventType.CHECKPOINT,
        tool_name="manual_checkpoint",
        metadata={"source": "cli"},
    )
    db.save_event(event)
    print(f"Checkpoint: autonomy depth reset for session {session_id[:8]}...")


def cmd_status(args: argparse.Namespace) -> None:
    """Show RTFI status."""
    db = Database()
    settings = load_settings()
    stats = db.get_stats(threshold=settings["threshold"])

    print("\nRTFI Status")
    print("=" * 40)
    print(f"Database: {stats['database_path']}")
    print(f"Total Sessions: {stats['total_sessions']}")
    print(f"High-Risk Sessions: {stats['high_risk_sessions']} (threshold: {settings['threshold']})")
    print(f"Total Events: {stats['total_events']}")
    print(f"Total Tool Calls: {stats['total_tool_calls']}")
    print(f"Total Agent Spawns: {stats['total_agent_spawns']}")
    print(f"Avg Risk Score: {stats['avg_risk_score']}")


def cmd_setup(args):
    """First-run setup wizard (L5)."""

    print("\nRTFI Setup")
    print("=" * 50)

    errors = []

    # 1. Check Python version
    v = sys.version_info
    if v >= (3, 10):
        print(f"[OK] Python {v.major}.{v.minor}.{v.micro}")
    else:
        errors.append(f"Python >= 3.10 required, found {v.major}.{v.minor}")
        print(f"[ERROR] Python {v.major}.{v.minor} — requires >= 3.10")

    # 2. Create ~/.rtfi/ with correct permissions
    rtfi_dir = Path.home() / ".rtfi"
    rtfi_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"[OK] Directory: {rtfi_dir}")

    # 4. Create default config.env if not exists
    config_path = rtfi_dir / "config.env"
    if not config_path.exists():
        config_path.write_text(
            "# RTFI Configuration\n"
            "# See: https://github.com/your-org/rtfi\n"
            "\n"
            "# Risk score threshold (0-100)\n"
            "threshold=70.0\n"
            "\n"
            "# Action when threshold exceeded: alert, block, confirm\n"
            "action_mode=alert\n"
            "\n"
            "# Data retention in days (1-3650)\n"
            "retention_days=90\n"
            "\n"
            "# Normalization thresholds (adjust for your workflow)\n"
            "max_tokens=128000\n"
            "max_agents=5\n"
            "max_steps=10\n"
            "max_tools_per_min=20.0\n"
            "\n"
            "# Optional StatsD metrics (uncomment to enable)\n"
            "# statsd_host=localhost\n"
            "# statsd_port=8125\n"
        )
        config_path.chmod(0o600)
        print(f"[OK] Config created: {config_path}")
    else:
        print(f"[OK] Config exists: {config_path}")

    # 5. Initialize database
    try:
        db = Database()
        print(f"[OK] Database: {db.db_path}")
    except Exception as e:
        errors.append(f"Database error: {e}")
        print(f"[ERROR] Database: {e}")

    # 6. Run health check
    print()
    if errors:
        print(f"[FAIL] Setup completed with {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        return 1
    else:
        print("[PASS] Setup complete! RTFI is ready.")
        print("\nNext steps:")
        print(f"  1. Edit {config_path} to customize settings")
        print("  2. Configure hooks in your .claude/settings.json")
        print("  3. Run: python3 scripts/rtfi_cli.py health")
        return 0


def cmd_health(args):
    """Run health check."""
    import os

    print("\nRTFI Health Check")
    print("=" * 50)

    errors = []

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

    print("\nSettings:")
    print(f"  Threshold: {threshold}")
    print(f"  Action Mode: {action_mode}")
    print(f"  Retention Days: {retention}")

    # Summary
    if errors:
        print(f"\n[FAIL] Health check failed with {len(errors)} error(s)")
        return 1
    else:
        print("\n[PASS] All systems operational")
        return 0


def main():
    parser = argparse.ArgumentParser(description="RTFI CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # sessions command
    sessions_parser = subparsers.add_parser("sessions", help="List recent sessions")
    sessions_parser.add_argument("--limit", "-n", type=int, default=20)
    sessions_parser.add_argument(
        "--project", "-p", type=str, default=None, help="Filter by project directory"
    )

    # risky command
    risky_parser = subparsers.add_parser("risky", help="Show high-risk sessions")
    risky_parser.add_argument("--threshold", "-t", type=float, default=70.0)
    risky_parser.add_argument("--limit", "-n", type=int, default=20)
    risky_parser.add_argument(
        "--project", "-p", type=str, default=None, help="Filter by project directory"
    )

    # show command
    show_parser = subparsers.add_parser("show", help="Show session details")
    show_parser.add_argument("session_id", help="Session ID or prefix")

    # status command
    subparsers.add_parser("status", help="Show RTFI status")

    # health command
    subparsers.add_parser("health", help="Run health check")

    # setup command
    subparsers.add_parser("setup", help="First-run setup and environment validation")

    # checkpoint command
    subparsers.add_parser("checkpoint", help="Reset autonomy depth for current session")

    args = parser.parse_args()

    commands = {
        "sessions": cmd_sessions,
        "risky": cmd_risky,
        "show": cmd_show,
        "status": cmd_status,
        "health": cmd_health,
        "setup": cmd_setup,
        "checkpoint": cmd_checkpoint,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
