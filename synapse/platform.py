"""Cross-platform primitives: transport choice, default paths, process control.

Synapse is a local-first service; the API transport is a Unix socket on
POSIX systems (no network exposure) and a loopback TCP socket with a
per-run token on Windows, where ``socketserver.UnixStreamServer`` is not
reliable. This module centralizes every platform-dependent primitive so the
rest of the codebase stays portable.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

TRANSPORT_UNIX = "unix"
TRANSPORT_TCP = "tcp"


def is_windows() -> bool:
    return os.name == "nt"


def default_transport() -> str:
    """Transport used when the configuration does not specify one."""
    return TRANSPORT_TCP if is_windows() else TRANSPORT_UNIX


def default_paths() -> dict[str, str]:
    """Default data/run/log/backup/config directories for the current OS.

    Linux keeps the historical systemd-oriented locations; macOS and
    Windows use per-user directories (no root privileges required).
    """
    if is_windows():
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        root = base / "Synapse"
        return {
            "storage": str(root / "data"),
            "run": str(root / "run"),
            "log": str(root / "logs"),
            "backup": str(root / "backups"),
            "config": str(root / "config.json"),
            "secrets": str(root / "secrets"),
        }
    if sys.platform == "darwin":
        root = Path.home() / ".synapse"
        return {
            "storage": str(root / "data"),
            "run": str(root / "run"),
            "log": str(root / "logs"),
            "backup": str(root / "backups"),
            "config": str(root / "config.json"),
            "secrets": str(root / "secrets"),
        }
    return {
        "storage": "/var/lib/synapse",
        "run": "/var/run/synapse",
        "log": "/var/log/synapse",
        "backup": "/var/backups/synapse",
        "config": "/etc/synapse/config.json",
        "secrets": "/etc/synapse/secrets",
    }


# ---------------------------------------------------------------------------
# Process control (pid liveness, graceful stop, force kill, spawn flags)
# ---------------------------------------------------------------------------


def process_alive(pid: int | None) -> bool:
    """True if the process exists.

    POSIX: ``os.kill(pid, 0)`` probe. Windows: ``os.kill(pid, 0)`` would
    call TerminateProcess (it kills!), so a handle-based probe is used
    instead (OpenProcess + GetExitCodeProcess).
    """
    if not pid or pid <= 0:
        return False
    if is_windows():
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not visible: alive
    except OSError:
        return True  # undeterminable: conservative (treat as alive)


def _windows_process_alive(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    STILL_ACTIVE = 259
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def send_stop_signal(pid: int) -> bool:
    """Graceful stop request; True if delivered (process alive).

    POSIX: SIGTERM. Windows: CTRL_BREAK_EVENT — the daemons are spawned in
    their own process group (CREATE_NEW_PROCESS_GROUP) and install a
    SIGBREAK handler, giving them the same clean-shutdown path.
    """
    if is_windows():
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            return True
        except OSError:
            return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def send_kill_signal(pid: int) -> bool:
    """Unconditional termination; True if delivered.

    POSIX: SIGKILL. Windows: ``os.kill(pid, SIGKILL)`` maps to
    TerminateProcess, which is exactly the unconditional kill we want.
    """
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def spawn_kwargs() -> dict:
    """Keyword arguments for daemon subprocess.Popen (detached session).

    POSIX: ``start_new_session=True``. Windows: ``creationflags`` with a
    new process group + detached process (start_new_session is POSIX-only).
    """
    if is_windows():
        return {
            "creationflags": (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        }
    return {"start_new_session": True}


def install_stop_handlers(handler) -> None:
    """Installs graceful-stop signal handlers.

    POSIX: SIGTERM + SIGINT. Windows: SIGBREAK (CTRL_BREAK_EVENT, the only
    catchable shutdown signal for detached console processes) in addition
    to SIGINT.
    """
    try:
        signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError):  # not available on this platform
        pass
    try:
        signal.signal(signal.SIGBREAK, handler)  # Windows only
    except (ValueError, OSError, AttributeError):
        pass
    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):
        pass


# ---------------------------------------------------------------------------
# Console encoding
# ---------------------------------------------------------------------------


def ensure_utf8_stdio() -> None:
    """Forces UTF-8 on stdout/stderr when the platform default is not UTF-8.

    On Windows, redirected console output defaults to the system codepage
    (e.g. cp1252) and printing Unicode characters (arrows, em-dashes, §)
    raises UnicodeEncodeError. Reconfiguring to UTF-8 with a safe error
    handler keeps the CLI robust on every platform. ``PYTHONUTF8=1`` is
    also documented for users who prefer the global mode.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and getattr(stream, "encoding", None) != "utf-8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass
