"""Unit tests for synapse.platform cross-platform primitives.

These cover the Windows-only and error branches that a POSIX host never
exercises at runtime: Windows path layout, Windows process-alive probing,
signal error paths, and detached-spawn flags. No server is started; the
platform flag is monkeypatched so both branches are exercised.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
from types import SimpleNamespace

import pytest

import synapse.platform as platform

# Windows-only constants, simulated on POSIX: the production code references
# them inside guarded branches that never run on a real POSIX host. SIGBREAK
# must not collide with any POSIX signal number.
CTRL_BREAK_EVENT = 0x00000002  # signal.CTRL_BREAK_EVENT on Windows
SIGBREAK = 0x7F0001  # signal.SIGBREAK on Windows


@pytest.fixture()
def win(monkeypatch):
    """Forces the module to behave as on Windows."""
    monkeypatch.setattr(platform, "is_windows", lambda: True)
    return platform


@pytest.fixture()
def posix(monkeypatch):
    monkeypatch.setattr(platform, "is_windows", lambda: False)
    return platform


class _FakeULong:
    def __init__(self, value=0):
        self.value = value


@pytest.fixture()
def fake_ctypes(monkeypatch):
    """Stubs the ``ctypes`` bindings used by ``_windows_process_alive``.

    ``windll`` does not exist on POSIX, so it is installed as a proxy whose
    ``kernel32`` each test populates with the desired stub. ``c_ulong`` and
    ``byref`` exist everywhere and are only replaced to record values.
    """
    windll = SimpleNamespace(kernel32=None)
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    monkeypatch.setattr(ctypes, "c_ulong", _FakeULong)
    monkeypatch.setattr(ctypes, "byref", lambda x: x)
    return windll


# ---------------------------------------------------------------------------
# transport / paths
# ---------------------------------------------------------------------------


def test_default_transport_posix(posix):
    assert posix.default_transport() == "unix"


def test_default_transport_windows(win):
    assert win.default_transport() == "tcp"


def test_default_paths_linux(posix, monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", "linux")
    paths = posix.default_paths()
    assert paths["config"] == "/etc/synapse/config.json"
    assert paths["storage"] == "/var/lib/synapse"


def test_default_paths_darwin(posix, monkeypatch, tmp_path):
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform.Path, "home", lambda: tmp_path)
    paths = posix.default_paths()
    assert paths["config"] == str(tmp_path / ".synapse" / "config.json")


def test_default_paths_windows_localappdata(win, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    paths = win.default_paths()
    assert paths["config"] == str(tmp_path / "LocalAppData" / "Synapse" / "config.json")


def test_default_paths_windows_home_fallback(win, monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(platform.Path, "home", lambda: tmp_path)
    paths = win.default_paths()
    assert "Synapse" in paths["storage"]


# ---------------------------------------------------------------------------
# process_alive
# ---------------------------------------------------------------------------


def test_process_alive_none(posix):
    assert posix.process_alive(None) is False
    assert posix.process_alive(0) is False
    assert posix.process_alive(-5) is False


def test_process_alive_true_current(posix):
    assert posix.process_alive(os.getpid()) is True


def test_process_alive_lookup_error(posix, monkeypatch):
    def boom(pid, sig):
        raise ProcessLookupError("gone")
    monkeypatch.setattr(platform.os, "kill", boom)
    assert posix.process_alive(999999) is False


def test_process_alive_permission_error(posix, monkeypatch):
    def boom(pid, sig):
        raise PermissionError("hidden")
    monkeypatch.setattr(platform.os, "kill", boom)
    assert posix.process_alive(1) is True


def test_process_alive_os_error(posix, monkeypatch):
    def boom(pid, sig):
        raise OSError("unknown")
    monkeypatch.setattr(platform.os, "kill", boom)
    assert posix.process_alive(1) is True


def test_process_alive_windows_branch(win, monkeypatch):
    calls = {"n": 0}

    def fake_windows_alive(pid):
        calls["n"] += 1
        return True

    monkeypatch.setattr(platform, "_windows_process_alive", fake_windows_alive)
    assert win.process_alive(42) is True
    assert calls["n"] == 1


def test_windows_process_alive_null_handle(win, fake_ctypes):
    class K32:
        def OpenProcess(self, *a):
            return 0  # NULL handle -> not alive

    fake_ctypes.kernel32 = K32()
    assert win._windows_process_alive(1) is False


def test_windows_process_alive_still_active(win, fake_ctypes):
    class K32:
        def OpenProcess(self, *a):
            return 0xDEAD

        def GetExitCodeProcess(self, handle, out):
            out.value = 259  # STILL_ACTIVE
            return 1

        def CloseHandle(self, h):
            pass

    fake_ctypes.kernel32 = K32()
    assert win._windows_process_alive(7) is True


def test_windows_process_alive_exited(win, fake_ctypes):
    class K32:
        def OpenProcess(self, *a):
            return 0xBEEF

        def GetExitCodeProcess(self, handle, out):
            out.value = 0  # not STILL_ACTIVE
            return 1

        def CloseHandle(self, h):
            pass

    fake_ctypes.kernel32 = K32()
    assert win._windows_process_alive(7) is False


def test_windows_process_alive_getexit_fails(win, fake_ctypes):
    class K32:
        def OpenProcess(self, *a):
            return 0xBEEF

        def GetExitCodeProcess(self, handle, out):
            return 0  # failure

        def CloseHandle(self, h):
            pass

    fake_ctypes.kernel32 = K32()
    assert win._windows_process_alive(7) is False


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------


def test_send_stop_signal_posix_ok(posix, monkeypatch):
    sent = {}
    monkeypatch.setattr(platform.os, "kill", lambda pid, sig: sent.update(pid=pid, sig=sig))
    assert posix.send_stop_signal(99) is True
    assert sent == {"pid": 99, "sig": signal.SIGTERM}


def test_send_stop_signal_posix_lookup(posix, monkeypatch):
    def boom(pid, sig):
        raise ProcessLookupError("gone")
    monkeypatch.setattr(platform.os, "kill", boom)
    assert posix.send_stop_signal(99) is False


def test_send_stop_signal_posix_oserror(posix, monkeypatch):
    def boom(pid, sig):
        raise OSError("nope")
    monkeypatch.setattr(platform.os, "kill", boom)
    assert posix.send_stop_signal(99) is False


def test_send_stop_signal_windows(win, monkeypatch):
    sent = {}
    monkeypatch.setattr(platform.signal, "CTRL_BREAK_EVENT", CTRL_BREAK_EVENT,
                        raising=False)
    monkeypatch.setattr(platform.os, "kill", lambda pid, sig: sent.update(pid=pid, sig=sig))
    assert win.send_stop_signal(99) is True
    assert sent["sig"] == CTRL_BREAK_EVENT


def test_send_stop_signal_windows_oserror(win, monkeypatch):
    def boom(pid, sig):
        raise OSError("nope")
    monkeypatch.setattr(platform.signal, "CTRL_BREAK_EVENT", CTRL_BREAK_EVENT,
                        raising=False)
    monkeypatch.setattr(platform.os, "kill", boom)
    assert win.send_stop_signal(99) is False


def test_send_kill_signal_ok(posix, monkeypatch):
    sent = {}
    monkeypatch.setattr(platform.os, "kill", lambda pid, sig: sent.update(sig=sig))
    assert posix.send_kill_signal(1) is True
    assert sent["sig"] == signal.SIGKILL


def test_send_kill_signal_lookup(posix, monkeypatch):
    def boom(pid, sig):
        raise ProcessLookupError("gone")
    monkeypatch.setattr(platform.os, "kill", boom)
    assert posix.send_kill_signal(1) is False


def test_send_kill_signal_oserror(posix, monkeypatch):
    def boom(pid, sig):
        raise OSError("nope")
    monkeypatch.setattr(platform.os, "kill", boom)
    assert posix.send_kill_signal(1) is False


# ---------------------------------------------------------------------------
# spawn_kwargs / handlers / stdio
# ---------------------------------------------------------------------------


def test_spawn_kwargs_posix(posix):
    assert posix.spawn_kwargs() == {"start_new_session": True}


def test_spawn_kwargs_windows(win, monkeypatch):
    # CREATE_NEW_PROCESS_GROUP is a Windows-only constant; stub it so the
    # Windows branch can run on this POSIX host.
    if not hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200,
                            raising=False)
    kw = win.spawn_kwargs()
    assert "creationflags" in kw
    assert kw["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP


def test_install_stop_handlers(posix, monkeypatch):
    installed = {}

    def fake_signal(name, handler):
        installed[name] = handler

    monkeypatch.setattr(platform.signal, "signal", fake_signal)
    handler = lambda *a: None  # noqa: E731
    posix.install_stop_handlers(handler)
    assert installed[signal.SIGTERM] is handler
    assert installed[signal.SIGINT] is handler


def test_install_stop_handlers_sigterm_unavailable(posix, monkeypatch):
    installed = {}

    def fake_signal(name, handler):
        if name == signal.SIGTERM:
            raise ValueError("not available")
        installed[name] = handler

    monkeypatch.setattr(platform.signal, "signal", fake_signal)
    handler = lambda *a: None  # noqa: E731
    posix.install_stop_handlers(handler)
    assert signal.SIGINT in installed


def test_install_stop_handlers_sigbreak_unavailable(posix, monkeypatch):
    installed = {}
    # SIGBREAK does not exist on POSIX; simulate a platform where it exists
    # but the SIGBREAK install is refused (the Windows-only branch).
    monkeypatch.setattr(platform.signal, "SIGBREAK", SIGBREAK, raising=False)

    def fake_signal(name, handler):
        if name == SIGBREAK:
            raise ValueError("not available")
        installed[name] = handler

    monkeypatch.setattr(platform.signal, "signal", fake_signal)
    posix.install_stop_handlers(lambda *a: None)
    assert signal.SIGTERM in installed
    assert signal.SIGINT in installed
    assert SIGBREAK not in installed


def test_ensure_utf8_stdio_unchanged(posix, monkeypatch):
    class Stream:
        def __init__(self):
            self.encoding = "utf-8"
            self.calls = 0

        def reconfigure(self, **kw):
            self.calls += 1

    s = Stream()
    monkeypatch.setattr(platform.sys, "stdout", s)
    monkeypatch.setattr(platform.sys, "stderr", s)
    posix.ensure_utf8_stdio()
    assert s.calls == 0


def test_ensure_utf8_stdio_reconfigures(posix, monkeypatch):
    class Stream:
        def __init__(self):
            self.encoding = "cp1252"
            self.kw = None

        def reconfigure(self, **kw):
            self.kw = kw

    out = Stream()
    err = Stream()
    monkeypatch.setattr(platform.sys, "stdout", out)
    monkeypatch.setattr(platform.sys, "stderr", err)
    posix.ensure_utf8_stdio()
    assert out.kw == {"encoding": "utf-8", "errors": "replace"}
    assert err.kw == {"encoding": "utf-8", "errors": "replace"}


def test_ensure_utf8_stdio_reconfigure_error(posix, monkeypatch):
    class Stream:
        encoding = "ascii"

        def reconfigure(self, **kw):
            raise ValueError("can't reconfigure")

    s = Stream()
    monkeypatch.setattr(platform.sys, "stdout", s)
    monkeypatch.setattr(platform.sys, "stderr", s)
    posix.ensure_utf8_stdio()  # must not raise
