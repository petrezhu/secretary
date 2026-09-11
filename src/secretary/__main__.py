"""Entry point: python -m secretary or secretary CLI."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path

from secretary.config import load_config
from secretary.daemon import SecretaryDaemon

PID_FILE = Path("/tmp/secretary.pid")
LOG_FILE = Path("/tmp/secretary.log")

# ── Icon / exit-code helpers ─────────────────────────────────────────

_STATUS_ICON = {"ok": "✓", "warning": "⚠", "critical": "✗"}
_EXIT_CODES = {"ok": 0, "warning": 1, "critical": 2}


def _worst_status(statuses: list[str]) -> str:
    """Return the most severe status from a list."""
    severity = {"ok": 0, "warning": 1, "critical": 2}
    worst = "ok"
    for s in statuses:
        if severity.get(s, 0) > severity.get(worst, 0):
            worst = s
    return worst


# ── Argument parser ──────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the full CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="secretary",
        description=(
            "Secretary — personal digital assistant daemon.\n\n"
            "Monitors goals, tasks, and system health; runs scheduled checks; "
            "sends notifications on anomalies."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # ── check ────────────────────────────────────────────────────────
    check_p = sub.add_parser(
        "check",
        help="Run all health checks once and print results",
        description=(
            "Execute all registered health checks (goals, tasks, deadman, "
            "resource guard) and display results. Exits 0 if all OK, "
            "1 if any warning, 2 if any critical."
        ),
    )
    check_p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON array",
    )
    check_p.add_argument("--config", default=None, help="Config file path")

    # ── start ────────────────────────────────────────────────────────
    start_p = sub.add_parser(
        "start",
        help="Start the Secretary daemon",
        description=(
            "Start the Secretary daemon in the foreground (default) or "
            "as a background process (--daemon). The daemon runs a periodic "
            "tick loop: resource check → scheduler → check pipeline."
        ),
    )
    start_p.add_argument(
        "--daemon",
        action="store_true",
        help="Fork to background and write PID file",
    )
    start_p.add_argument("--config", default=None, help="Config file path")

    # ── status ───────────────────────────────────────────────────────
    sub.add_parser(
        "status",
        help="Show daemon status (running / stopped / PID)",
        description=(
            "Check whether the Secretary daemon is running by inspecting "
            "the PID file at /tmp/secretary.pid."
        ),
    )

    # ── stop ─────────────────────────────────────────────────────────
    sub.add_parser(
        "stop",
        help="Stop a running daemon (sends SIGTERM)",
        description="Send SIGTERM to the background daemon process.",
    )

    # ── morning / evening ────────────────────────────────────────────
    sub.add_parser("morning", help="Generate morning briefing (stub)")
    sub.add_parser("evening", help="Generate evening report (stub)")

    # ── resource ─────────────────────────────────────────────────────
    sub.add_parser("resource", help="Show resource status (stub)")

    # ── audit ────────────────────────────────────────────────────────
    audit_p = sub.add_parser("audit", help="Audit log operations (stub)")
    audit_sub = audit_p.add_subparsers(dest="audit_command")
    audit_sub.add_parser("list", help="List recent entries")
    show_p = audit_sub.add_parser("show", help="Show entry details")
    show_p.add_argument("entry_id", type=int)

    # ── config ───────────────────────────────────────────────────────
    config_p = sub.add_parser("config", help="Config operations (stub)")
    config_sub = config_p.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="Show current config")
    config_sub.add_parser("validate", help="Validate config")

    # ── serve ────────────────────────────────────────────────────────
    serve_p = sub.add_parser(
        "serve",
        help="Start the message gateway HTTP server",
        description=(
            "Start the Secretary message gateway as an HTTP server. "
            "Provides REST endpoints for sending messages via QQ/Email "
            "and a WebSocket for real-time streaming (future)."
        ),
    )
    serve_p.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    serve_p.add_argument("--port", type=int, default=8900, help="Bind port (default: 8900)")
    serve_p.add_argument("--log-level", default="info", help="Log level (default: info)")

    return parser


# ── Command implementations ──────────────────────────────────────────


def cmd_check(args: argparse.Namespace) -> int:
    """Run all checks once and print human-readable or JSON output.

    Returns exit code: 0=all ok, 1=warning, 2=critical.
    """
    config = load_config(args.config)
    daemon = SecretaryDaemon(config)
    results = asyncio.run(daemon.run_checks())

    if args.json_output:
        output = json.dumps([r.to_dict() for r in results], indent=2, default=str)
        print(output)
    else:
        # Header
        print("Secretary check results:")
        print("─" * 40)
        for r in results:
            icon = _STATUS_ICON.get(r.status, "?")
            print(f"  {icon} {r.name}: {r.message}")
        print("─" * 40)

        # Summary
        statuses = [r.status for r in results]
        worst = _worst_status(statuses)
        ok_count = statuses.count("ok")
        warn_count = statuses.count("warning")
        crit_count = statuses.count("critical")
        print(f"  Summary: {ok_count} ok, {warn_count} warning, {crit_count} critical → {worst}")

    worst = _worst_status([r.status for r in results])
    return _EXIT_CODES.get(worst, 2)


def _write_pid(pid: int) -> None:
    """Write PID to the PID file."""
    PID_FILE.write_text(str(pid))


def _read_pid() -> int | None:
    """Read PID from the PID file, or None if not present."""
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _cleanup_pid() -> None:
    """Remove the PID file if it exists."""
    with contextlib.suppress(OSError):
        PID_FILE.unlink(missing_ok=True)


def cmd_start(args: argparse.Namespace) -> int:
    """Start the Secretary daemon.

    In foreground mode, runs the event loop directly.
    In --daemon mode, double-forks to detach from the terminal,
    writes a PID file, and redirects output to /tmp/secretary.log.
    """
    # Guard: already running?
    existing = _read_pid()
    if existing is not None and _is_process_alive(existing):
        print(f"Secretary daemon already running (PID {existing}).", file=sys.stderr)
        return 1

    config = load_config(args.config)

    if args.daemon:
        return _start_daemonized(config)

    # Foreground mode
    daemon = SecretaryDaemon(config)

    def _on_signal(sig, _frame):
        _cleanup_pid()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    _write_pid(os.getpid())
    try:
        asyncio.run(daemon.start())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        _cleanup_pid()
    return 0


def _start_daemonized(config) -> int:
    """Double-fork to daemonize, write PID file, redirect I/O."""
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent: print PID and exit
        print(f"Secretary daemon started (PID {pid}).")
        return 0

    # Child: become session leader
    os.setsid()

    # Second fork
    pid = os.fork()
    if pid > 0:
        # Intermediate child exits
        os._exit(0)

    # Grandchild: the actual daemon
    _write_pid(os.getpid())

    # Redirect stdio to log file
    log_fd = os.open(str(LOG_FILE), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)

    # Redirect stdin from /dev/null
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    daemon = SecretaryDaemon(config)
    try:
        asyncio.run(daemon.start())
    except Exception:
        import traceback

        traceback.print_exc()
    finally:
        _cleanup_pid()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the message gateway HTTP server."""
    from secretary.gateway.server import create_server

    create_server(
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    """Send SIGTERM to the running daemon."""
    pid = _read_pid()
    if pid is None:
        print("Secretary daemon is not running (no PID file).")
        return 1

    if not _is_process_alive(pid):
        print(f"Stale PID file (process {pid} not found). Cleaning up.")
        _cleanup_pid()
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to Secretary daemon (PID {pid}).")
    except ProcessLookupError:
        print(f"Process {pid} already exited.")
        _cleanup_pid()
        return 1
    except PermissionError:
        print(f"Permission denied sending signal to PID {pid}.", file=sys.stderr)
        return 1

    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    """Show daemon status by inspecting the PID file."""
    pid = _read_pid()

    if pid is None:
        print("Secretary daemon is not running.")
        return 1

    if _is_process_alive(pid):
        print(f"Secretary daemon is running (PID {pid}).")
        return 0
    else:
        print(f"Secretary daemon is not running (stale PID file: {pid}).")
        _cleanup_pid()
        return 1


# ── Main dispatcher ──────────────────────────────────────────────────


def main() -> int:
    """CLI entry point."""
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    cmd = args.command
    if cmd == "check":
        return cmd_check(args)
    elif cmd == "start":
        return cmd_start(args)
    elif cmd == "stop":
        return cmd_stop(args)
    elif cmd == "status":
        return cmd_status(args)
    elif cmd == "morning":
        print("Morning briefing: not implemented yet")
        return 0
    elif cmd == "evening":
        print("Evening report: not implemented yet")
        return 0
    elif cmd == "resource":
        print("Resource status: not implemented yet")
        return 0
    elif cmd == "audit":
        print("Audit log: not implemented yet")
        return 0
    elif cmd == "config":
        print("Config: not implemented yet")
        return 0
    elif cmd == "serve":
        return cmd_serve(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
