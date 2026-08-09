"""``web`` group (SPEC_CLI §4.3): lifecycle of the web interface."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from ..platform import spawn_kwargs

from .common import (
    EXIT_OK,
    EXIT_UNAVAILABLE,
    config_arg_path,
    emit,
    emit_error,
    http_get,
    level_int,
    pid_alive,
    read_pid_file,
    read_web_token,
    remove_pid_file,
    resolve_config,
    service_state,
    socket_responds,
    stop_service,
    table,
    wait_ready,
    write_pid_file,
)

GROUP = "web"

_EXAMPLES = """\
Examples:
  synapse web start                  start the web interface (detached, port 8080)
  synapse web start --port 9000      choose the port
  synapse web stop                   clean stop
  synapse web restart                restart
  synapse web status --json          full state as JSON
  synapse web logs --follow          follow the web logs
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="human web interface (start, stop, status, logs)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("start", parents=[common], help="starts the web interface")
    a.add_argument("--port", type=int, default=None,
                   help="listen port (default: $SYNAPSE_WEB_PORT, else 8080)")
    a.add_argument("--foreground", action="store_true",
                   help="stay in the foreground (logs on stdout)")
    a.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                   default=None)
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_start)

    a = actions.add_parser("stop", parents=[common], help="stop the web")
    a.add_argument("--force", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_stop)

    a = actions.add_parser("restart", parents=[common], help="stop then start")
    a.add_argument("--port", type=int, default=None,
                   help="listen port (default: $SYNAPSE_WEB_PORT, else 8080)")
    a.add_argument("--foreground", action="store_true")
    a.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                   default=None)
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_restart)

    a = actions.add_parser("status", parents=[common], help="web state")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_status)

    a = actions.add_parser("logs", parents=[common], help="web logs")
    a.add_argument("--follow", "-f", action="store_true")
    a.add_argument("--lines", type=int, default=100)
    a.add_argument("--level", default=None,
                   help="filter by level (unavailable: see 'synapse server logs')")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_logs)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _require_server(config) -> None:  # noqa: ANN001
    """The web requires a started local server (SPEC_CLI §4.3): exit code 3 otherwise."""
    if not socket_responds(config) or read_web_token(config) is None:
        raise SystemExit(emit_error(
            "local service not ready: the server must be started "
            "(synapse server start) before the web interface",
            code=EXIT_UNAVAILABLE,
        ))


def _resolve_web_port(args: argparse.Namespace) -> int:
    """Web port: ``--port`` > ``$SYNAPSE_WEB_PORT`` > 8080.

    The environment variable allows the tests to be isolated on a machine
    where production already listens on the default port (SPEC_PRODUCTION
    §10.5)."""
    port = getattr(args, "port", None)
    if port is not None:
        return port
    try:
        return int(os.environ.get("SYNAPSE_WEB_PORT") or 8080)
    except ValueError:
        return 8080


def _cmd_start(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    _require_server(config)
    port = _resolve_web_port(args)
    if args.foreground:
        return _run_web_foreground(config, port, args.log_level)
    info = read_pid_file(config, "web")
    if info and info.get("pid") and pid_alive(info["pid"]):
        print(f"web interface already running (PID {info['pid']})")
        return EXIT_OK

    cmd = [sys.executable, "-m", "synapse.cli", "_daemon", "web",
           "--config", config_arg_path(args), "--port", str(port)]
    if getattr(args, "log_level", None):
        cmd += ["--log-level", args.log_level]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, **spawn_kwargs(),
        )
    except OSError as exc:
        return emit_error(f"cannot start the web interface: {exc}")

    ready = wait_ready(
        lambda: _web_responding(config, port),
        timeout=15.0,
    )
    if not ready:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return emit_error(
            f"the web interface did not start within 15s (see "
            f"{config.log_dir}/web.error.log)"
        )
    info = read_pid_file(config, "web") or {}
    print(f"web interface started (PID {info.get('pid')}, port {port})")
    return EXIT_OK


def _web_responding(config, port: int) -> bool:  # noqa: ANN001
    code, _ = http_get(port, "/api/orgs")
    return code == 200 and read_pid_file(config, "web") is not None


def _run_web_foreground(config, port: int, log_level: str | None) -> int:
    from ..logging_setup import setup_logging
    from ..systemd_notify import watchdog_context
    from ..web import SynapseWebUI
    from .daemon import _install_stop_event

    setup_logging(config, verbose=True, log_name="web.log",
                  error_log_name="web.error.log", level=level_int(log_level))
    web = SynapseWebUI(config, port=port)
    write_pid_file(config, "web", {"command": "web", "port": port})
    stop_event = _install_stop_event()
    try:
        with watchdog_context():
            web.start()
            print(f"web interface at http://127.0.0.1:{web.port} (Ctrl-C to stop)")
            stop_event.wait()
    finally:
        web.stop()
        remove_pid_file(config, "web")
    return EXIT_OK


def _cmd_stop(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    code, message = stop_service(config, "web", force=args.force)
    print(message)
    return code


def _cmd_restart(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    code, message = stop_service(config, "web", force=False)
    if code != EXIT_OK:
        print(message)
        return code
    print(message)
    return _cmd_start(args)


def _cmd_status(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    info = read_pid_file(config, "web") or {}
    pid = info.get("pid")
    alive = pid_alive(pid)
    port = info.get("port") or 8080
    code, status = http_get(port, "/api/status")
    http_ok = code == 200
    # Web-specific double check: live PID AND HTTP responds
    # (SPEC_CLI §2.2) — no Unix socket here.
    if not alive:
        state = "stopped"
    elif http_ok:
        state = "running"
    else:
        state = "degraded"
    payload = {
        "state": state,
        "pid": pid,
        "port": port,
        "started_at": info.get("started_at"),
        "version": info.get("version"),
        "http_ok": http_ok,
        "sessions_active": status.get("sessions_active") if status else None,
        "started_at_web": status.get("started_at") if status else None,
    }

    if getattr(args, "json", False):
        return emit(args, payload)
    if state == "stopped":
        print("web interface stopped")
        return EXIT_OK
    state_label = ("DEGRADEDE" if state == "degraded"
                   else "running")
    rows = [
        [f"interface web {state_label} (PID {pid})"],
        [f"  port          {payload['port']}"],
        [f"  version       {payload.get('version') or 'unknown'}"],
        [f"  started       {payload.get('started_at') or 'unknown'}"],
        [f"  http /api/orgs {'200' if payload['http_ok'] else 'unreachable'}"],
        [f"  sessions      {payload.get('sessions_active', 'n/a')}"],
    ]
    print(table(rows))
    return EXIT_OK


def _cmd_logs(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if getattr(args, "level", None):
        return emit_error(
            "the log format (JSON, SPEC.txt §4) does not contain a "
            "level: the --level filter is unavailable"
        )
    from .logs import tail_log

    path = os.path.join(config.log_dir, "web.log")
    return tail_log(path, lines=args.lines, follow=args.follow)
