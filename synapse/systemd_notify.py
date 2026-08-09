"""Minimal ``sd_notify`` client for systemd supervision (SPEC_PRODUCTION §4).

Sends datagrams to ``$NOTIFY_SOCKET`` (systemd protocol). No
external dependency; **inactive (strict no-op) when ``$NOTIFY_SOCKET`` is
absent** — daemon behavior outside systemd (development, tests)
is strictly unchanged.

Messages emitted:
- ``READY=1``: the service finished its initialization;
- ``WATCHDOG=1``: periodic heartbeat (the service is alive) —
  required by ``WatchdogSec=`` in the units (a freeze = kill + restart);
- ``STOPPING=1``: stopping (clean stop).

Any send error is silent: a system without systemd (or an
unreachable socket) must never fail a daemon.
"""

from __future__ import annotations

import os
import socket
import threading

_DEFAULT_WATCHDOG_INTERVAL = 10.0  # WatchdogSec=30 in the units (×3 margin)


def notify(message: str) -> bool:
    """Sends an sd_notify message; False if no socket or on error."""
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return False
    # Abstract socket (Linux): the path starts with '@' in
    # $NOTIFY_SOCKET, '\0' is required for the real address.
    if sock_path.startswith("@"):
        sock_path = "\0" + sock_path[1:]
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.sendto(message.encode("utf-8"), sock_path)
            return True
        finally:
            sock.close()
    except OSError:
        return False


def ready() -> bool:
    """Signals that the service is ready (READY=1)."""
    return notify("READY=1")


def stopping() -> bool:
    """Signals that the service is stopping (STOPPING=1)."""
    return notify("STOPPING=1")


def watchdog() -> bool:
    """Emits a heartbeat (WATCHDOG=1)."""
    return notify("WATCHDOG=1")


class WatchdogThread:
    """Heartbeat thread: ``WATCHDOG=1`` every ``interval``
    seconds, as long as the daemon lives. Harmless outside systemd (the sends
    fail silently)."""

    def __init__(self, interval: float = _DEFAULT_WATCHDOG_INTERVAL) -> None:
        self._interval = interval
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="synapse-sdnotify",
                         daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            watchdog()


class watchdog_context:
    """Systemd supervision context around the blocking service.

    Usage ::

        with watchdog_context():
            server.start()   # blocks; heartbeats are emitted in parallel

    On entry: ``READY=1`` + heartbeat thread start.
    On exit: thread stop + ``STOPPING=1``.
    """

    def __init__(self, interval: float = _DEFAULT_WATCHDOG_INTERVAL) -> None:
        self._interval = interval

    def __enter__(self) -> "watchdog_context":
        ready()
        self._thread = WatchdogThread(interval=self._interval)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self._thread.stop()
        stopping()
