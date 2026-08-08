"""Internal entry points for detached processes (SPEC_CLI §4.2/§4.3/§4.13).

``synapse server start`` (without ``--foreground``) detaches a process that
runs ``_daemon server``; ``web start`` and ``a2a start`` do the same.
Chaque daemon :

1. charge la configuration et configure la journalisation fichier ;
2. writes its PID file (``run_dir/<service>.pid``, 0600);
3. installs the signal handlers (SIGTERM/SIGINT → clean stop);
4. starts the service and stays in the foreground of ITS OWN process;
5. removes the PID file on stop (clean or forced).

Startup errors go to stderr (captured by the parent, which
detects failure via a timeout on the socket/PID file).
"""

from __future__ import annotations

import argparse
import logging
from .. import platform
import sys
import threading

from ..config import Config
from .common import remove_pid_file, write_pid_file

logger = logging.getLogger("synapse.cli.daemon")


def _load_config(path: str | None) -> Config:
    try:
        return Config.load(path)
    except ValueError as exc:
        print(f"synapse: {exc}", file=sys.stderr)
        sys.exit(1)


def _install_shutdown_handlers(stop_fn) -> None:  # noqa: ANN001
    def _shutdown(_signum, _frame) -> None:  # noqa: ANN001
        threading.Thread(target=stop_fn, daemon=True).start()

    platform.install_stop_handlers(_shutdown)


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

    def _shutdown(_signum, _frame) -> None:  # noqa: ANN001
        nonlocal shutdown_thread
        # The cleanup (socket + web token) runs in this thread. The main
        # thread must join it before exiting, otherwise the process can
        # die between the two unlinks and leave the web token behind.
        shutdown_thread = threading.Thread(target=server.stop, daemon=False)
        shutdown_thread.start()

    platform.install_stop_handlers(_shutdown)
    try:
        # READY + battements WATCHDOG sous systemd ; no-op hors systemd.
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
    try:
        with watchdog_context():
            web.start()
            stop_event.wait()  # SIGTERM/SIGINT → sortie propre
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
    try:
        with watchdog_context():
            bridge.start()
            stop_event.wait()  # SIGTERM/SIGINT → sortie propre
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
    p.add_argument("--token", required=True)
    p.add_argument("--log-level", default=None)

    args = parser.parse_args(argv)
    if args.service == "server":
        run_server_daemon(args.config, args.log_level)
    elif args.service == "web":
        run_web_daemon(args.config, args.port, args.log_level)
    elif args.service == "a2a":
        # Le mot de passe de l'agent arrive sur stdin (le parent le lit et
        # le transmet par le pipe — jamais en argument ni en environnement).
        password = sys.stdin.readline().rstrip("\n")
        if not password:
            print("synapse _daemon a2a: mot de passe de l'agent absent",
                  file=sys.stderr)
            return 1
        run_a2a_daemon(args.config, args.agent_name, args.port, args.token,
                       args.log_level, password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
