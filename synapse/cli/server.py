"""``server`` group (SPEC_CLI §4.2): lifecycle of the main service."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from ..config import Config
from ..client import ApiClientError, Client, ClientTransportError
from ..platform import spawn_kwargs
from .common import (
    EXIT_OK,
    CliError,
    emit,
    emit_error,
    read_pid_file,
    remove_pid_file,
    resolve_config,
    run_dir,
    service_state,
    socket_responds,
    stop_service,
    table,
    wait_ready,
)

GROUP = "server"

_EXAMPLES = """\
Examples:
  synapse server start                      start the server (detached)
  synapse server start --foreground         start in the foreground
  synapse server start --log-level debug    logging level
  synapse server stop                       clean stop (SIGTERM)
  synapse server stop --force               forced stop (SIGKILL)
  synapse server restart                    restart
  synapse server status --json              full state as JSON
  synapse server logs --follow              follow the logs
  synapse server config --json              configuration effective
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="server administration (start, stop, status, logs, config)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("start", parents=[common], help="starts the main server")
    a.add_argument("--foreground", action="store_true",
                   help="stay in the foreground (logs on stdout)")
    a.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                   default=None, help="logging level (default: info)")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_start)

    a = actions.add_parser("stop", parents=[common], help="clean server stop")
    a.add_argument("--force", action="store_true",
                   help="SIGKILL after a 15s wait")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_stop)

    a = actions.add_parser("restart", parents=[common],
                           help="stops then starts the server")
    a.add_argument("--foreground", action="store_true")
    a.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                   default=None)
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_restart)

    a = actions.add_parser("status", parents=[common], help="service state")
    a.add_argument("--json", action="store_true", help="machine JSON output")
    a.set_defaults(run=_cmd_status)

    a = actions.add_parser("logs", parents=[common], help="server logs")
    a.add_argument("--follow", "-f", action="store_true", help="continuous follow")
    a.add_argument("--lines", type=int, default=100, help="number of lines (default: 100)")
    a.add_argument("--level", default=None,
                   help="filter by level (unavailable: the JSON logs do not "
                        "contain a level — see SPEC.txt §4)")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_logs)

    a = actions.add_parser("config", parents=[common], help="configuration effective")
    a.add_argument("--json", action="store_true", help="machine JSON output")
    a.add_argument("--show-secrets", action="store_true",
                   help="show sensitive values (dangerous)")
    a.set_defaults(run=_cmd_config)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_start(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.foreground:
        return _run_server_foreground(config, args.log_level)
    return _start_detached(config, args)


def _run_server_foreground(config: Config, log_level: str | None) -> int:
    """Foreground server: logging + PID file + signals."""
    from ..logging_setup import setup_logging
    from ..server import SynapseServer
    from ..systemd_notify import watchdog_context
    from .common import write_pid_file
    import signal as signal_mod
    import threading

    setup_logging(config, verbose=True, level=_level_int(log_level))
    write_pid_file(config, "synapse", {"command": "server"})
    server = SynapseServer(config)

    def _shutdown(_signum, _frame) -> None:  # noqa: ANN001
        threading.Thread(target=server.stop, daemon=True).start()

    signal_mod.signal(signal_mod.SIGTERM, _shutdown)
    signal_mod.signal(signal_mod.SIGINT, _shutdown)
    try:
        # READY + WATCHDOG heartbeats under systemd; no-op outside systemd.
        with watchdog_context():
            server.start()
    finally:
        remove_pid_file(config, "synapse")
    return EXIT_OK


def _level_int(value: str | None) -> int:
    import logging as _logging

    if value is None:
        return _logging.INFO
    return getattr(_logging, value.upper())


def _start_detached(config: Config, args: argparse.Namespace) -> int:
    """Detached start: idempotent (SPEC_CLI §2), checks state, launches
    the daemon and waits until the socket + PID file are ready."""
    state = service_state(config, "synapse")
    if state["state"] == "running":
        print(f"server already running (PID {state['pid'] or 'unknown'})")
        return EXIT_OK
    if state["state"] == "degraded":
        return emit_error(
            f"degraded state: PID {state['pid']} alive but socket "
            f"{config.socket_path} silent — stop it first (synapse server stop "
            "--force) then start it again"
        )

    # Pre-checks: free storage lock (an active
    # legacy server, without a PID file, already holds the lock).
    lock_path = config.lock_path
    if os.path.exists(lock_path):
        from ..server import lock_is_stale

        if not lock_is_stale(lock_path):
            return emit_error(
                f"another service already uses {config.storage_dir} (lock "
                f"{lock_path}) — server already running?"
            )

    # Config path for the daemon: the subcommand --config, else the root
    # --config, else the environment (SPEC_CLI §2 resolution order).
    cfg_path = None
    if getattr(args, "config", None):
        cfg_path = args.config
    elif getattr(args, "config_root", None):
        cfg_path = args.config_root
    else:
        cfg_path = _config_path_from_env()
    cmd = [sys.executable, "-m", "synapse.cli", "_daemon", "server",
           "--config", os.path.abspath(cfg_path) if cfg_path else cfg_path]
    if getattr(args, "log_level", None):
        cmd += ["--log-level", args.log_level]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **spawn_kwargs(),
        )
    except OSError as exc:
        return emit_error(f"cannot start the server: {exc}")

    ready = wait_ready(
        lambda: socket_responds(config)
        and read_pid_file(config, "synapse") is not None,
        timeout=15.0,
    )
    if not ready:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return emit_error(
            f"the server did not start within 15s (see "
            f"{config.log_dir}/synapse.error.log)"
        )
    info = read_pid_file(config, "synapse") or {}
    print(f"server started (PID {info.get('pid')}, version {info.get('version')})")
    return EXIT_OK


def _config_path_from_env() -> str | None:
    path = os.environ.get("SYNAPSE_CONFIG") or os.environ.get("Synapse_CONFIG")
    return os.path.abspath(path) if path else None


def _cmd_stop(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    code, message = stop_service(config, "synapse", force=args.force)
    print(message)
    return code


def _cmd_restart(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    code, message = stop_service(config, "synapse", force=False)
    if code != EXIT_OK:
        print(message)
        return code
    print(message)
    if args.foreground:
        return _run_server_foreground(config, args.log_level)
    return _start_detached(config, args)


def _cmd_status(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    state = service_state(config, "synapse")
    info = state["pid_file"] or {}
    payload = {
        "state": state["state"],
        "pid": state["pid"],
        "started_at": info.get("started_at"),
        "version": info.get("version"),
        "socket": config.socket_path,
        "socket_ok": state["socket_ok"],
        "web_token_present": os.path.exists(os.path.join(run_dir(config), "web_token")),
        "database": config.db_path,
    }
    if state["state"] != "stopped":
        # Service counters (get_server_status, org-auth — the local
        # token or the organization credentials serve as proof).
        from .common import resolve_org_auth

        try:
            org, password = resolve_org_auth(config, args)
            status = Client.from_config(config).get_server_status(org, password)
            payload.update({
                "api_version": status.get("api_version"),
                "commands_count": status.get("commands_count"),
                "requests_total": status.get("requests_total"),
                "uptime_seconds": status.get("uptime_seconds"),
            })
        except (ApiClientError, CliError, ClientTransportError) as exc:
            payload["live_error"] = str(exc)

    if getattr(args, "json", False):
        return emit(args, payload)
    if state["state"] == "stopped":
        print("server stopped")
    elif state["state"] == "degraded":
        print(f"server DEGRADED (PID {state['pid']} alive, socket silent)")
    else:
        lines = [
            [f"server running (PID {state['pid']})"],
            [f"  version       {payload.get('version') or 'unknown'}"],
            [f"  started       {payload.get('started_at') or 'unknown'}"],
            [f"  base          {config.db_path}"],
            [f"  requests      {payload.get('requests_total', 'n/a')}"],
            [f"  uptime (s)    {payload.get('uptime_seconds', 'n/a')}"],
            [f"  socket        {'responding' if state['socket_ok'] else 'silent'}"],
            [f"  web token    {'present' if payload['web_token_present'] else 'missing'}"],
        ]
        print(table(lines))
        if payload.get("live_error"):
            print(f"  (counters unavailable: {payload['live_error']})")
    return EXIT_OK


def _cmd_logs(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if getattr(args, "level", None):
        return emit_error(
            "the log format (JSON, SPEC.txt §4) does not contain a "
            "level: the --level filter is unavailable — filter the file "
            "directly (e.g. grep)"
        )
    from .logs import tail_log

    path = os.path.join(config.log_dir, "synapse.log")
    return tail_log(path, lines=args.lines, follow=args.follow)


def _cmd_config(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    data = config.to_dict()
    data["_extra"] = dict(config._extra)
    # Secrets masking (SPEC_CLI §2): extra fields whose name
    # suggests a secret (token, key, password) and key paths.
    secret_keys = {k for k in data["_extra"] if _is_secret_key(k)}
    if not getattr(args, "show_secrets", False):
        for key in secret_keys:
            if key in data["_extra"]:
                data["_extra"][key] = "****"
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [[k, str(v)] for k, v in sorted(data.items()) if k != "_extra"]
    for k, v in sorted(data["_extra"].items()):
        rows.append([f"_extra.{k}", str(v)])
    print(table(rows, ["key", "value"]))
    if not getattr(args, "show_secrets", False):
        print("(sensitive values masked — use --show-secrets to display them)")
    return EXIT_OK


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(tok in lowered for tok in ("token", "password", "passwd", "secret", "key"))
