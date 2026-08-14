"""Internal entry points for detached processes (SPEC_CLI §4.2/§4.3/§4.13).

``synapse server start`` (without ``--foreground``) detaches a process that
runs ``_daemon server``; ``web start`` and ``a2a start`` do the same.
Each daemon:

1. loads the configuration and sets up file logging;
2. writes its PID file (``run_dir/<service>.pid``, 0600);
3. installs the signal handlers (SIGTERM/SIGINT → clean stop);
4. starts the service and stays in the foreground of ITS OWN process;
5. removes the PID file on stop (clean or forced).

Startup errors go to stderr (captured by the parent, which
detects failure via a timeout on the socket/PID file).

Parent watch (auditor F1, 2026-08-11): a daemon whose invoker exported
``SYNAPSE_WATCH_PARENT=1`` receives the invoker's PID (and start time)
in its environment and exits when that process disappears — so a
pytest worker killed mid-test can never orphan its daemon. Production
never sets the variable: detached services keep their usual lifetime.
"""

from __future__ import annotations

import argparse
import logging
import os
from .. import platform
import sys
import threading
import time
from typing import Callable

from ..config import Config
from .common import remove_pid_file, write_pid_file

logger = logging.getLogger("synapse.cli.daemon")

# Poll interval of the parent-watch thread.
_WATCH_POLL_SECONDS = 1.0


def watch_parent_env() -> dict | None:
    """Daemon environment additions when the invoker asked for parent-watch.

    Test harnesses export ``SYNAPSE_WATCH_PARENT=1`` so that a daemon
    started by a pytest worker exits when that worker is killed (no
    orphaned daemons — auditor F1). Production never sets the variable:
    detached services keep their usual lifetime. Returns ``None`` when
    the watch was not requested; otherwise a dict carrying the invoker's
    PID and start time (the start time defeats PID reuse).
    """
    if os.environ.get("SYNAPSE_WATCH_PARENT") != "1":
        return None
    pid = os.getppid()
    return {
        "SYNAPSE_DAEMON_PARENT_PID": str(pid),
        "SYNAPSE_DAEMON_PARENT_START": _process_starttime(pid) or "",
    }


def _process_starttime(pid: int) -> str | None:
    """The ``starttime`` field of ``/proc/<pid>/stat``, or ``None``."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            stat = fh.read()
    except OSError:
        return None
    # The comm field may contain spaces/parens: split after the last ')'.
    # starttime is field 22 (index 19 after the state field).
    rest = stat.rsplit(")", 1)[-1].split()
    return rest[19] if len(rest) > 19 else None


def _parent_gone(pid: int, start: str | None) -> bool:
    """True when the watched parent is gone (or its identity changed)."""
    if not os.path.isdir(f"/proc/{pid}"):
        return True
    return bool(start) and _process_starttime(pid) != start


def _install_parent_watch(on_parent_dead: Callable[[], None]) -> None:
    """Starts the parent-watch thread when the daemon was asked to watch.

    Reads ``SYNAPSE_DAEMON_PARENT_PID``/``SYNAPSE_DAEMON_PARENT_START``
    (injected by the CLI when the invoker exported ``SYNAPSE_WATCH_PARENT``)
    and calls ``on_parent_dead`` as soon as that process disappears — a
    killed test worker can never orphan the daemon, even on SIGKILL
    (no atexit/finally runs in that case). POSIX-only (/proc); on
    Windows the watch is skipped.
    """
    pid_s = os.environ.get("SYNAPSE_DAEMON_PARENT_PID")
    if not pid_s or platform.is_windows():  # pragma: no cover - POSIX-only
        return
    try:
        pid = int(pid_s)
    except ValueError:
        return
    start = os.environ.get("SYNAPSE_DAEMON_PARENT_START") or None

    def _watch() -> None:
        while not _parent_gone(pid, start):
            time.sleep(_WATCH_POLL_SECONDS)
        logger.info("daemon: watched parent %d is gone — shutting down", pid)
        on_parent_dead()

    threading.Thread(target=_watch, name="parent-watch", daemon=True).start()


def _load_config(path: str | None) -> Config:
    try:
        return Config.load(path)
    except ValueError as exc:
        print(f"synapse: {exc}", file=sys.stderr)
        sys.exit(1)


def _install_stop_event() -> threading.Event:
    """Installs SIGTERM/SIGINT (and SIGBREAK on Windows) → stop event
    (the main thread waits on the event instead of sleeping: the process
    truly exits on the requested stop, then cleans up its PID file)."""
    stop_event = threading.Event()

    def _shutdown(_signum, _frame) -> None:  # noqa: ANN001
        stop_event.set()

    platform.install_stop_handlers(_shutdown)
    return stop_event


def run_server_daemon(config_path: str | None, log_level: str | None) -> None:
    """Detached server process (``synapse server start`` without --foreground)."""
    from ..logging_setup import setup_logging
    from ..server import SynapseServer
    from ..systemd_notify import watchdog_context

    config = _load_config(config_path)
    setup_logging(config, level=_log_level(log_level))
    write_pid_file(config, "synapse", {"command": "server"})
    server = SynapseServer(config)
    shutdown_thread: threading.Thread | None = None

    def _request_stop(_signum=None, _frame=None) -> None:
        nonlocal shutdown_thread
        # The cleanup (socket + web token) runs in this thread. The main
        # thread must join it before exiting, otherwise the process can
        # die between the two unlinks and leave the web token behind.
        shutdown_thread = threading.Thread(target=server.stop, daemon=False)
        shutdown_thread.start()

    platform.install_stop_handlers(_request_stop)
    # A killed test worker must never orphan the daemon (auditor F1):
    # when the invoker asked for parent-watching, exit with the parent.
    _install_parent_watch(lambda: _request_stop(None, None))
    try:
        # READY + WATCHDOG heartbeats under systemd; no-op outside systemd.
        with watchdog_context():
            server.start()  # blocks; removes socket + web token on stop
    finally:
        if shutdown_thread is not None:
            shutdown_thread.join(timeout=10.0)
        remove_pid_file(config, "synapse")


def run_web_daemon(config_path: str | None, port: int, log_level: str | None) -> None:
    """Detached web process (``synapse web start`` without --foreground)."""
    from ..logging_setup import setup_logging
    from ..systemd_notify import watchdog_context
    from ..web import SynapseWebUI

    config = _load_config(config_path)
    setup_logging(config, log_name="web.log", error_log_name="web.error.log",
                  level=_log_level(log_level))
    web = SynapseWebUI(config, port=port)
    write_pid_file(config, "web", {"command": "web", "port": port})
    stop_event = _install_stop_event()
    _install_parent_watch(stop_event.set)
    try:
        with watchdog_context():
            web.start()
            stop_event.wait()  # SIGTERM/SIGINT → clean exit
    finally:
        web.stop()
        remove_pid_file(config, "web")


def run_a2a_daemon(config_path: str | None, agent_name: str, port: int,
                   token: str, log_level: str | None, password: str) -> None:
    """Detached A2A bridge process (``synapse a2a start`` without --foreground)."""
    from ..a2a_bridge import A2ABridge
    from ..logging_setup import setup_logging
    from ..systemd_notify import watchdog_context

    config = _load_config(config_path)
    setup_logging(config, log_name="a2a.log", error_log_name="a2a.error.log",
                  level=_log_level(log_level))
    bridge = A2ABridge(config, agent_name, password, port, token=token)
    write_pid_file(config, "a2a", {"command": "a2a", "port": port,
                                   "agent_name": agent_name})
    stop_event = _install_stop_event()
    _install_parent_watch(stop_event.set)
    try:
        with watchdog_context():
            bridge.start()
            stop_event.wait()  # SIGTERM/SIGINT → clean exit
    finally:
        bridge.stop()
        remove_pid_file(config, "a2a")


def _log_level(value: str | None) -> int:
    import logging as _logging

    if value is None:
        return _logging.INFO
    return getattr(_logging, value.upper(), _logging.INFO)


def main(argv: list[str] | None = None) -> int:
    """Internal entry point: ``synapse _daemon <service> [options]``."""
    parser = argparse.ArgumentParser(prog="synapse _daemon", add_help=False)
    sub = parser.add_subparsers(dest="service", required=True)

    p = sub.add_parser("server")
    p.add_argument("--config", default=None)
    p.add_argument("--log-level", default=None)

    p = sub.add_parser("web")
    p.add_argument("--config", default=None)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--log-level", default=None)

    p = sub.add_parser("a2a")
    p.add_argument("--config", default=None)
    p.add_argument("--agent-name", required=True)
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--log-level", default=None)

    args = parser.parse_args(argv)
    if args.service == "server":
        run_server_daemon(args.config, args.log_level)
    elif args.service == "web":
        run_web_daemon(args.config, args.port, args.log_level)
    elif args.service == "a2a":
        # The agent's password and the bridge access token arrive on
        # stdin (the parent writes them through the pipe — never as
        # arguments nor environment variables).
        password = sys.stdin.readline().rstrip("\n")
        token = sys.stdin.readline().rstrip("\n")
        if not password:
            print("synapse _daemon a2a: agent password missing",
                  file=sys.stderr)
            return 1
        if not token:
            print("synapse _daemon a2a: bridge access token missing",
                  file=sys.stderr)
            return 1
        run_a2a_daemon(args.config, args.agent_name, args.port,
                       token, args.log_level, password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
