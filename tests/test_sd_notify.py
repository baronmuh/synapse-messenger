"""Tests for the sd_notify client (SPEC_PRODUCTION §4): sending to
$NOTIFY_SOCKET, strict no-op outside systemd, watchdog thread, and the
READY/STOPPING context around the blocking service.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

import synapse.systemd_notify as sd


@pytest.fixture()
def notify_socket(tmp_path, monkeypatch):
    """Receiving Unix datagram socket + NOTIFY_SOCKET pointed at it."""
    path = str(tmp_path / "notify.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    received = []
    stop = threading.Event()

    def _reader():
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            received.append(data.decode("utf-8"))

    threading.Thread(target=_reader, daemon=True).start()
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    yield received, sock
    stop.set()
    sock.close()


def test_notify_noop_without_socket(monkeypatch):
    """Without $NOTIFY_SOCKET: strict no-op (False, no exception)."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sd.notify("READY=1") is False
    assert sd.ready() is False
    assert sd.stopping() is False
    assert sd.watchdog() is False


def test_notify_delivers_message(notify_socket):
    received, _ = notify_socket
    assert sd.notify("READY=1") is True
    deadline = time.monotonic() + 2.0
    while not received and time.monotonic() < deadline:
        time.sleep(0.05)
    assert received == ["READY=1"]


def test_notify_abstract_socket(tmp_path, monkeypatch):
    """Linux abstract socket: path '@name' → address '\\0name'."""
    received = []

    class _Dgram:
        def __init__(self, family, type_):  # noqa: ANN001
            self.addr = None

        def sendto(self, data, addr):  # noqa: ANN001
            received.append((data, addr))

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", _Dgram)
    monkeypatch.setenv("NOTIFY_SOCKET", "@/tmp/synapse-notify-abstract")
    assert sd.notify("WATCHDOG=1") is True
    assert received[0][1] == "\0/tmp/synapse-notify-abstract"


def test_notify_silent_on_oserror(notify_socket, monkeypatch):
    """A send error is silent (never raises an exception)."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/chemin/inexistant/notify.sock")
    assert sd.notify("READY=1") is False


def test_watchdog_thread_pings(notify_socket, monkeypatch):
    """The watchdog thread emits WATCHDOG=1 at the requested interval."""
    received, _ = notify_socket
    thread = sd.WatchdogThread(interval=0.1)
    thread.start()
    deadline = time.monotonic() + 1.5
    while received.count("WATCHDOG=1") < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    thread.stop()
    assert received.count("WATCHDOG=1") >= 2


def test_watchdog_context_readiness(notify_socket):
    """The context emits READY=1 on entry and STOPPING=1 on exit."""
    received, _ = notify_socket
    with sd.watchdog_context(interval=0.1):
        time.sleep(0.3)  # let the thread emit at least one beat
    deadline = time.monotonic() + 2.0
    while "STOPPING=1" not in received and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "READY=1" in received
    assert "STOPPING=1" in received
    assert received.count("WATCHDOG=1") >= 1
