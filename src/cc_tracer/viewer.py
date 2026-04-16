#!/usr/bin/env python3
"""cc-trace CLI — view and analyze Claude Code session traces.

Usage:
    cc-trace list                     List all traced sessions
    cc-trace view [SESSION|latest]    Show formatted timeline
    cc-trace stats [SESSION|latest]   Show tool usage statistics
    cc-trace tail                     Live-follow the latest session
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

TRACE_DIR = Path.home() / ".cc-tracer" / "traces"

EVENT_COLORS = {
    "SessionStart": "bold green",
    "SessionEnd": "bold red",
    "UserPromptSubmit": "bold cyan",
    "Stop": "yellow",
    "PreToolUse": "blue",
    "PostToolUse": "magenta",
}

console = Console()


def _resolve_session(session: str | None) -> Path:
    """Resolve a session ID or 'latest' to a trace file path."""
    if session is None or session == "latest":
        latest = TRACE_DIR / "latest"
        if latest.is_symlink():
            return TRACE_DIR / os.readlink(str(latest))
        # Fall back to most recent file
        files = sorted(TRACE_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
        if not files:
            console.print("[red]No trace files found.[/red]")
            sys.exit(1)
        return files[-1]
    path = TRACE_DIR / f"{session}.jsonl"
    if not path.exists():
        console.print(f"[red]Trace file not found: {path}[/red]")
        sys.exit(1)
    return path


def _load_records(path: Path) -> list[dict]:
    records = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def cmd_list(_args):
    """List all traced sessions."""
    if not TRACE_DIR.exists():
        console.print("[yellow]No traces directory found.[/yellow]")
        return

    files = sorted(TRACE_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        console.print("[yellow]No trace files found.[/yellow]")
        return

    table = Table(title="Traced Sessions")
    table.add_column("Session ID", style="cyan")
    table.add_column("Date", style="green")
    table.add_column("Events", justify="right")
    table.add_column("Tools Used", justify="right")

    for f in files:
        if f.name == "latest" or f.is_symlink():
            continue
        records = _load_records(f)
        if not records:
            continue
        ts = records[0].get("ts", "")[:19]
        tool_count = sum(1 for r in records if r.get("event") == "PreToolUse")
        table.add_row(f.stem, ts, str(len(records)), str(tool_count))

    console.print(table)


def cmd_view(args):
    """Show formatted timeline for a session."""
    path = _resolve_session(args.session)
    records = _load_records(path)
    if not records:
        console.print("[yellow]No records found.[/yellow]")
        return

    console.print(f"\n[bold]Session: {path.stem}[/bold]")
    console.print(f"[dim]{len(records)} events[/dim]\n")

    # Build tool duration map: tool_use_id -> (pre_ts, post_ts)
    pre_times: dict[str, str] = {}
    post_times: dict[str, str] = {}
    for r in records:
        tuid = r.get("tool_use_id")
        if tuid:
            if r["event"] == "PreToolUse":
                pre_times[tuid] = r["ts"]
            elif r["event"] == "PostToolUse":
                post_times[tuid] = r["ts"]

    for r in records:
        event = r.get("event", "?")
        color = EVENT_COLORS.get(event, "white")
        ts = r.get("ts", "")
        # Show only time portion
        time_str = ts[11:19] if len(ts) >= 19 else ts

        line = Text()
        line.append(f"  {time_str}  ", style="dim")
        line.append(f"{event:<20}", style=color)

        # Event-specific details
        if event == "SessionStart":
            line.append(f"model={r.get('model', '?')}  source={r.get('source', '?')}", style="dim")
        elif event == "SessionEnd":
            line.append(f"reason={r.get('reason', '?')}", style="dim")
        elif event == "UserPromptSubmit":
            prompt = r.get("prompt", "")
            line.append(prompt[:80], style="white")
            if len(prompt) > 80:
                line.append("...", style="dim")
        elif event == "PreToolUse":
            tool = r.get("tool_name", "?")
            cmd = r.get("command")
            line.append(f"{tool}", style="bold")
            if cmd:
                line.append(f"  $ {cmd[:60]}", style="dim")
        elif event == "PostToolUse":
            tool = r.get("tool_name", "?")
            size = r.get("response_size", 0)
            tuid = r.get("tool_use_id")
            duration = ""
            if tuid and tuid in pre_times and tuid in post_times:
                try:
                    t1 = datetime.fromisoformat(pre_times[tuid])
                    t2 = datetime.fromisoformat(post_times[tuid])
                    dur_ms = (t2 - t1).total_seconds() * 1000
                    duration = f"  {dur_ms:.0f}ms"
                except (ValueError, TypeError):
                    pass
            line.append(f"{tool}", style="bold")
            line.append(f"  {size}B{duration}", style="dim")
        elif event == "Stop":
            llm = r.get("llm_response")
            if llm:
                # Show first 120 chars inline, then full text indented below
                preview = llm[:120].replace("\n", " ")
                line.append(preview, style="white")
                if len(llm) > 120:
                    line.append("...", style="dim")

        # Subagent context
        if r.get("agent_id"):
            line.append(f"  [{r.get('agent_type', '?')}]", style="italic dim")

        console.print(line)

    console.print()


def cmd_stats(args):
    """Show tool usage statistics for a session."""
    path = _resolve_session(args.session)
    records = _load_records(path)
    if not records:
        console.print("[yellow]No records found.[/yellow]")
        return

    console.print(f"\n[bold]Stats: {path.stem}[/bold]\n")

    # Tool usage counts
    tool_counts = Counter(r.get("tool_name") for r in records if r.get("event") == "PreToolUse")

    if tool_counts:
        table = Table(title="Tool Usage")
        table.add_column("Tool", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Bar")
        max_count = max(tool_counts.values())
        for tool, count in tool_counts.most_common():
            bar_len = int(count / max_count * 30)
            table.add_row(tool, str(count), "█" * bar_len)
        console.print(table)

    # Event breakdown
    event_counts = Counter(r.get("event") for r in records)
    console.print(f"\n[bold]Events:[/bold] {len(records)} total")
    for event, count in event_counts.most_common():
        console.print(f"  {event:<25} {count}")

    # Duration (first to last record)
    if len(records) >= 2:
        try:
            t1 = datetime.fromisoformat(records[0]["ts"])
            t2 = datetime.fromisoformat(records[-1]["ts"])
            dur = (t2 - t1).total_seconds()
            console.print(f"\n[bold]Duration:[/bold] {dur:.1f}s")
        except (ValueError, KeyError):
            pass

    # Prompt count
    prompt_count = sum(1 for r in records if r.get("event") == "UserPromptSubmit")
    console.print(f"[bold]Prompts:[/bold] {prompt_count}")
    console.print()


def cmd_tail(_args):
    """Live-follow the latest session trace file."""
    path = _resolve_session("latest")
    console.print(f"[dim]Tailing {path}  (Ctrl+C to stop)[/dim]\n")

    try:
        with open(path) as f:
            # Go to end
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    try:
                        r = json.loads(line)
                        event = r.get("event", "?")
                        color = EVENT_COLORS.get(event, "white")
                        ts = r.get("ts", "")[11:19]
                        tool = r.get("tool_name", "")
                        console.print(f"  {ts}  [{color}]{event:<20}[/{color}]  {tool}")
                    except json.JSONDecodeError:
                        pass
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


def main():
    parser = argparse.ArgumentParser(prog="cc-trace", description="Claude Code session trace viewer")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List traced sessions")

    p_view = sub.add_parser("view", help="Show session timeline")
    p_view.add_argument("session", nargs="?", default="latest")

    p_stats = sub.add_parser("stats", help="Show tool usage stats")
    p_stats.add_argument("session", nargs="?", default="latest")

    sub.add_parser("tail", help="Live-follow latest session")

    args = parser.parse_args()

    commands = {
        "list": cmd_list,
        "view": cmd_view,
        "stats": cmd_stats,
        "tail": cmd_tail,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
