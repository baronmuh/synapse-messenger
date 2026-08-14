"""Transport abstraction: Unix socket (POSIX) or loopback TCP (Windows).

The API protocol is unchanged on both transports: one JSON request per
line, response then close. On TCP, the client first sends a single line
with the per-run token (``<run_dir>/transport.token``, 0600) which the
server verifies in constant time; the listener binds 127.0.0.1 only, so
the surface is loopback + token — the local-first security equivalent of
the Unix socket's filesystem permissions.
"""

from __future__ import annotations

import os
import secrets
import socket
from pathlib import Path

from . import platform
from .config import Config

TRANSPORT_UNIX = "unix"
TRANSPORT_TCP = "tcp"

DEFAULT_TRANSPORT_PORT = 7910
TOKEN_FILENAME = "transport.token"
TOKEN_BYTES = 32

# Bind host for the TCP transport: loopback only, never a network address.
TCP_BIND_HOST = "127.0.0.1"


def resolve_transport(config: Config) -> str:
    """Effective transport for a configuration ("" or unset = platform default)."""
    value = (getattr(config, "transport", "") or "").strip().lower()
    if value in (platform.TRANSPORT_UNIX, platform.TRANSPORT_TCP):
        return value
    if value:
        raise ValueError(f"unknown transport '{value}' (expected 'unix' or 'tcp')")
    return platform.default_transport()


def transport_port(config: Config) -> int:
    """Effective TCP port (only meaningful for the TCP transport)."""
    return getattr(config, "transport_port", None) or DEFAULT_TRANSPORT_PORT


def run_dir(config: Config) -> str:
    """Runtime directory: PID files, web token, transport token.

    Explicit ``config.run_dir`` wins; otherwise the Unix socket's parent
    directory (historical behavior), and for TCP the platform default.
    """
    explicit = getattr(config, "run_dir", "") or ""
    if explicit:
        return explicit
    if resolve_transport(config) == platform.TRANSPORT_UNIX:
        return os.path.dirname(config.socket_path)
    return platform.default_paths()["run"]


def token_path(config: Config) -> str:
    return os.path.join(run_dir(config), TOKEN_FILENAME)


def read_token_from(run_dir_path: str | None) -> str | None:
    """The transport token from an explicit run directory, or None."""
    if not run_dir_path:
        return None
    try:
        value = Path(run_dir_path, TOKEN_FILENAME).read_text(encoding="ascii").strip()
        return value or None
    except OSError:
        return None


def read_token(config: Config) -> str | None:
    """The transport token, or None if the server never created one."""
    return read_token_from(run_dir(config))


def ensure_token(config: Config) -> str:
    """Loads the token or generates + persists it (0600 on POSIX)."""
    existing = read_token(config)
    if existing:
        return existing
    token = secrets.token_hex(TOKEN_BYTES)
    path = Path(token_path(config))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("ascii") + b"\n")
    finally:
        os.close(fd)
    return token


def remove_token(config: Config) -> None:
    try:
        os.unlink(token_path(config))
    except FileNotFoundError:
        pass


def transport_responds(config: Config) -> bool:
    """True if a local transport endpoint is listening (server up).

    TCP: a connect() probe on the loopback port (no data exchanged).
    Unix: a connect() probe on the socket path.
    """
    try:
        if resolve_transport(config) == platform.TRANSPORT_TCP:
            with socket.create_connection(
                (TCP_BIND_HOST, transport_port(config)), timeout=1.0
            ):
                return True
        else:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.settimeout(1.0)
                sock.connect(config.socket_path)
            finally:
                sock.close()
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Client connection
# ---------------------------------------------------------------------------


def connect(config: Config) -> socket.socket:
    """Opens a connection to the service according to the transport.

    For TCP, the per-run token is sent as the first line before any
    request payload (the server closes the connection on mismatch).
    """
    if resolve_transport(config) == platform.TRANSPORT_TCP:
        sock = socket.create_connection((TCP_BIND_HOST, transport_port(config)), timeout=10.0)
        token = read_token(config)
        if not token:
            sock.close()
            raise OSError(
                f"transport token missing at {token_path(config)} (server not started?)"
            )
        sock.sendall(token.encode("ascii") + b"\n")
        return sock
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Read timeout for server responses. Env-configurable (SYNAPSE_SOCKET_TIMEOUT,
    # default 10s): parallel test workers on a loaded machine can push a request
    # past 10s; production keeps the default.
    sock.settimeout(float(os.environ.get("SYNAPSE_SOCKET_TIMEOUT", "10")))
    sock.connect(config.socket_path)
    return sock
