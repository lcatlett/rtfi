"""RTFI CLI - Command-line interface for risk tracking."""

import click
from rich.console import Console
from rich.table import Table

from rtfi.storage.database import Database

console = Console()


@click.group()
@click.version_option()
def cli() -> None:
    """RTFI - Real-Time Instruction Compliance Risk Scoring."""
    pass


@cli.command()
@click.option("--limit", "-n", default=20, help="Number of sessions to show")
def sessions(limit: int) -> None:
    """List recent sessions."""
    db = Database()
    recent = db.get_recent_sessions(limit=limit)

    if not recent:
        console.print("[dim]No sessions recorded yet.[/dim]")
        return

    table = Table(title="Recent Sessions")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Started", style="cyan")
    table.add_column("Peak Risk", justify="right")
    table.add_column("Tools", justify="right")
    table.add_column("Agents", justify="right")
    table.add_column("Outcome")

    for s in recent:
        risk_style = "green"
        if s.peak_risk_score >= 70:
            risk_style = "red bold"
        elif s.peak_risk_score >= 50:
            risk_style = "yellow"

        table.add_row(
            s.id[:8] + "...",
            s.started_at.strftime("%Y-%m-%d %H:%M"),
            f"[{risk_style}]{s.peak_risk_score:.1f}[/{risk_style}]",
            str(s.total_tool_calls),
            str(s.total_agent_spawns),
            s.outcome.value,
        )

    console.print(table)


@cli.command()
@click.option("--threshold", "-t", default=70.0, help="Risk threshold")
@click.option("--limit", "-n", default=20, help="Number of sessions to show")
def risky(threshold: float, limit: int) -> None:
    """Show high-risk sessions."""
    db = Database()
    high_risk = db.get_high_risk_sessions(threshold=threshold, limit=limit)

    if not high_risk:
        console.print(f"[green]No sessions exceeded risk threshold {threshold}.[/green]")
        return

    table = Table(title=f"High-Risk Sessions (threshold: {threshold})")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Started", style="cyan")
    table.add_column("Peak Risk", justify="right", style="red bold")
    table.add_column("Tools", justify="right")
    table.add_column("Agents", justify="right")

    for s in high_risk:
        table.add_row(
            s.id[:8] + "...",
            s.started_at.strftime("%Y-%m-%d %H:%M"),
            f"{s.peak_risk_score:.1f}",
            str(s.total_tool_calls),
            str(s.total_agent_spawns),
        )

    console.print(table)


@cli.command()
@click.argument("session_id")
def show(session_id: str) -> None:
    """Show details for a session."""
    db = Database()

    # Try to find session by prefix
    all_sessions = db.get_recent_sessions(limit=1000)
    matches = [s for s in all_sessions if s.id.startswith(session_id)]

    if not matches:
        console.print(f"[red]Session not found: {session_id}[/red]")
        return

    session = matches[0]

    console.print(f"\n[bold]Session: {session.id}[/bold]")
    console.print(f"Started: {session.started_at}")
    console.print(f"Ended: {session.ended_at or 'In progress'}")
    console.print(f"Outcome: {session.outcome.value}")
    console.print(f"Peak Risk Score: [{'red' if session.peak_risk_score >= 70 else 'green'}]{session.peak_risk_score:.1f}[/]")
    console.print(f"Total Tool Calls: {session.total_tool_calls}")
    console.print(f"Total Agent Spawns: {session.total_agent_spawns}")

    # Show events
    events = list(db.get_session_events(session.id))
    if events:
        console.print(f"\n[bold]Events ({len(events)}):[/bold]")

        table = Table()
        table.add_column("Time", style="dim")
        table.add_column("Type")
        table.add_column("Tool")
        table.add_column("Risk", justify="right")

        for e in events[:50]:  # Limit display
            risk_str = ""
            if e.risk_score:
                style = "green"
                if e.risk_score.total >= 70:
                    style = "red"
                elif e.risk_score.total >= 50:
                    style = "yellow"
                risk_str = f"[{style}]{e.risk_score.total:.1f}[/{style}]"

            table.add_row(
                e.timestamp.strftime("%H:%M:%S"),
                e.event_type.value,
                e.tool_name or "-",
                risk_str,
            )

        console.print(table)


@cli.command()
def status() -> None:
    """Show RTFI status and statistics."""
    db = Database()

    all_sessions = db.get_recent_sessions(limit=1000)
    high_risk = [s for s in all_sessions if s.peak_risk_score >= 70]

    console.print("\n[bold]RTFI Status[/bold]")
    console.print(f"Database: {db.db_path}")
    console.print(f"Total Sessions: {len(all_sessions)}")
    console.print(f"High-Risk Sessions: {len(high_risk)}")

    if all_sessions:
        avg_risk = sum(s.peak_risk_score for s in all_sessions) / len(all_sessions)
        console.print(f"Average Peak Risk: {avg_risk:.1f}")

        total_tools = sum(s.total_tool_calls for s in all_sessions)
        total_agents = sum(s.total_agent_spawns for s in all_sessions)
        console.print(f"Total Tool Calls: {total_tools}")
        console.print(f"Total Agent Spawns: {total_agents}")


if __name__ == "__main__":
    cli()
