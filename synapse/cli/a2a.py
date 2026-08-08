"""``a2a`` group (SPEC_CLI §4.13): A2A interoperability bridge."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from ..platform import spawn_kwargs

from .common import (
    getpass_get,
    EXIT_OK,
    EXIT_UNAVAILABLE,
    emit,
    emit_error,
    http_get,
    read_pid_file,
    remove_pid_file,
    resolve_config,
    socket_responds,
    stop_service,
    table,
    wait_ready,
    write_pid_file,
)

GROUP = "a2a"

_EXAMPLES = """\
Examples:
  synapse a2a start --agent-name support            (password + token via stdin)
  synapse a2a start --agent-name support --foreground
  synapse a2a stop
  synapse a2a status --json
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="A2A bridge (start, stop, status)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("start", parents=[common],
                           help="starts the A2A bridge (started server required)")
    a.add_argument("--agent-name", required=True, help="exposed agent")
    a.add_argument("--port", type=int, default=None,
                   help="listen port (default: $SYNAPSE_A2A_PORT, else 8090)")
    a.add_argument("--foreground", action="store_true")
    a.add_argument("--password-stdin", action="store_true",
                   help="read the agent password from stdin")
    a.add_argument("--token-stdin", action="store_true",
                   help="read the bridge access token from stdin")
    a.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                   default=None)
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_start)

    a = actions.add_parser("stop", parents=[common], help="stops the bridge")
    a.add_argument("--force", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_stop)

    a = actions.add_parser("status", parents=[common], help="bridge state")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_status)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _resolve_a2a_port(args: argparse.Namespace) -> int:
    """Bridge port: ``--port`` > ``$SYNAPSE_A2A_PORT`` > 8090.

    Same isolation mechanism as ``SYNAPSE_WEB_PORT`` (tests on a
    machine where production already listens)."""
    port = getattr(args, "port", None)
    if port is not None:
        return port
    try:
        return int(os.environ.get("SYNAPSE_A2A_PORT") or 8090)
    except ValueError:
        return 8090


def _cmd_start(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if not socket_responds(config):
        raise SystemExit(emit_error(
            "local service not ready: the server must be started "
            "(synapse server start) before the A2A bridge",
            code=EXIT_UNAVAILABLE,
        ))
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass_get(f"Password of agent '{args.agent_name}' : ")
    if not password:
        return emit_error("empty agent password")
    if args.token_stdin:
        token = sys.stdin.readline().rstrip("\n")
    else:
        token = getpass_get("Bridge access token: ")
    if not token:
        return emit_error("an access token is required (--token-stdin or getpass)")

    info = read_pid_file(config, "a2a")
    if info and _alive(info.get("pid")):
        print(f"A2A bridge already running (PID {info['pid']})")
        return EXIT_OK

    if args.foreground:
        return _run_foreground(config, args, password, token)

    cmd = [sys.executable, "-m", "synapse.cli", "_daemon", "a2a",
           "--config", _config_arg(args), "--agent-name", args.agent_name,
           "--port", str(_resolve_a2a_port(args)), "--token", token]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, **spawn_kwargs(),
        )
        if proc.stdin is not None:
            proc.stdin.write((password + "\n").encode("utf-8"))
            proc.stdin.close()
    except OSError as exc:
        return emit_error(f"cannot start the A2A bridge: {exc}")

    ready = wait_ready(
        lambda: _a2a_responding(config, args.port),
        timeout=15.0,
    )
    if not ready:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return emit_error(
            f"the A2A bridge did not start within 15s (see "
            f"{config.log_dir}/a2a.error.log)"
        )
    info = read_pid_file(config, "a2a") or {}
    print(f"A2A bridge started (PID {info.get('pid')}, port {args.port}, "
          f"agent {args.agent_name})")
    return EXIT_OK


def _config_arg(args: argparse.Namespace) -> str:
    path = getattr(args, "config", None) or getattr(args, "config_root", None)
    if path:
        return os.path.abspath(path)
    path = os.environ.get("SYNAPSE_CONFIG") or os.environ.get("Synapse_CONFIG")
    return os.path.abspath(path) if path else ""


def _alive(pid) -> bool:  # noqa: ANN001
    from .common import pid_alive

    return pid_alive(pid)


def _a2a_responding(config, port: int) -> bool:  # noqa: ANN001
    code, _ = http_get(port, "/", timeout=2.0)
    return code >= 200 and read_pid_file(config, "a2a") is not None


def _run_foreground(config, args, password: str, token: str) -> int:
    from ..a2a_bridge import A2ABridge
    from ..logging_setup import setup_logging
    from ..systemd_notify import watchdog_context
    from .daemon import _install_stop_event

    setup_logging(config, verbose=True, log_name="a2a.log",
                  error_log_name="a2a.error.log", level=_level_int(args.log_level))
    bridge = A2ABridge(config, args.agent_name, password, args.port, token=token)
    write_pid_file(config, "a2a", {"command": "a2a", "port": args.port,
                                   "agent_name": args.agent_name})
    stop_event = _install_stop_event()
    try:
        with watchdog_context():
            bridge.start()
            print(f"A2A bridge at http://127.0.0.1:{args.port} "
                  f"(agent {args.agent_name}, Ctrl-C to stop)")
            stop_event.wait()
    finally:
        bridge.stop()
        remove_pid_file(config, "a2a")
    return EXIT_OK


def _level_int(value: str | None) -> int:
    import logging as _logging

    if value is None:
        return _logging.INFO
    return getattr(_logging, value.upper())


def _cmd_stop(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    code, message = stop_service(config, "a2a", force=args.force)
    print(message)
    return code


def _cmd_status(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    info = read_pid_file(config, "a2a") or {}
    pid = info.get("pid")
    alive = _alive(pid)
    port = info.get("port") or 8090
    code, _ = http_get(port, "/", timeout=2.0)
    http_ok = 200 <= code < 500
    # Bridge-specific double check: live PID AND HTTP responds.
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
        "agent_name": info.get("agent_name"),
        "started_at": info.get("started_at"),
        "version": info.get("version"),
        "http_ok": http_ok,
    }
    if getattr(args, "json", False):
        return emit(args, payload)
    if state == "stopped":
        print("A2A bridge stopped")
        return EXIT_OK
    label = ("DEGRADED" if state == "degraded" else "running")
    rows = [
        [f"A2A bridge {label} (PID {pid})"],
        [f"  port          {payload['port']}"],
        [f"  agent         {payload.get('agent_name') or 'unknown'}"],
        [f"  http          {'responding' if payload['http_ok'] else 'silent'}"],
        [f"  started       {payload.get('started_at') or 'unknown'}"],
    ]
    print(table(rows))
    return EXIT_OK
