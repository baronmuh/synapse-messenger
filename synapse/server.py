"""Unix socket server of the Synapse messaging service.

Transport: a local Unix socket only (no network port). Each
request is a compact JSON object on one line, terminated by ``\\n``. A
request larger than 1 MiB is rejected with ``INVALID_ARGUMENT`` before any
authentification.

The socket, storage and logs belong to the service system account;
the socket is created with ``0600`` permissions in a
``0700`` directory.
"""

from __future__ import annotations

import hmac
import logging
import os
import queue
import secrets
import socket
import socketserver
import sys
import threading
from pathlib import Path

from . import platform
from . import transport
from .config import Config
from .db import StorageError, ensure_storage
from .errors import INVALID_ARGUMENT, INTERNAL_ERROR
from . import jsonutil
from .security import load_or_create_key
from .service import Service

logger = logging.getLogger("synapse.server")

_READ_CHUNK = 65536
# Local anti-DoS guardrails: maximum number of concurrently
# handled connections and a connection inactivity timeout. Beyond
# the bound, a connection briefly waits for a slot then is refused.
MAX_CONCURRENT_CONNECTIONS = 64
CONNECTION_IDLE_TIMEOUT = 60  # seconds
CONNECTION_ACQUIRE_TIMEOUT = 2.0  # seconds


class RequestTooLarge(Exception):
    """Request exceeding the maximum allowed size."""


class _ConnectionHandler(socketserver.StreamRequestHandler):
    """Handles a connection: one or more requests, line by line.

    The service is reachable via ``self.server.service`` (injected by
    ``SynapseServer`` at the server instance level).
    """

    timeout = CONNECTION_IDLE_TIMEOUT  # seconds

    @property
    def service(self) -> Service:
        return self.server.service  # type: ignore[attr-defined]

    def setup(self) -> None:
        super().setup()
        # An inactive connection must not hold a thread indefinitely.
        self.request.settimeout(CONNECTION_IDLE_TIMEOUT)

    def handle(self) -> None:  # noqa: D102
        self._buffer = bytearray()
        # TCP transport: the client opens with a single token line (the
        # listener is loopback-only; the token replaces filesystem
        # permissions). Constant-time comparison; mismatch closes.
        auth_token = getattr(self.server, "auth_token", None)
        if auth_token:
            try:
                line = self._read_line()
            except (OSError, ConnectionError):
                return
            if line is None or not line:
                return
            try:
                candidate = line.decode("ascii", "replace").strip()
            except (ValueError, UnicodeError):
                return
            if not hmac.compare_digest(candidate, auth_token):
                return  # wrong or missing token: drop the connection
        while True:
            try:
                line = self._read_line()
            except RequestTooLarge:
                self._send_error_response(INVALID_ARGUMENT, "Request too large")
                return
            except (OSError, ConnectionError):
                return
            if line is None:  # EOF
                return
            if not line:
                self._send_error_response(INVALID_ARGUMENT, "Empty request")
                continue
            try:
                response, meta = self.service.process(bytes(line))
            except Exception as exc:  # noqa: BLE001 - last line of defense
                # An unexpected failure must never leave the connection
                # without leaking the response or the content.
                meta = {"result": INTERNAL_ERROR, "internal_error": exc}
                response = {
                    "success": False,
                    "data": None,
                    "error": {
                        "code": INTERNAL_ERROR,
                        "message": "Internal service error",
                    },
                }
                logging.getLogger("synapse.error").exception(
                    "Internal error", exc_info=exc
                )
            self._log_request(meta)
            self._write_response(response, pre_serialized=meta.get("pre_serialized"))
            if meta.get("internal_error") is not None:
                logging.getLogger("synapse.error").exception(
                    "Internal error", exc_info=meta["internal_error"]
                )

    # -- reading a line with a size limit --------------------------------
    def _read_line(self) -> bytes | None:
        max_bytes = self.service.config.max_request_bytes
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                if len(line) > max_bytes:
                    raise RequestTooLarge()
                return line
            if len(self._buffer) > max_bytes:
                raise RequestTooLarge()
            try:
                chunk = self.request.recv(_READ_CHUNK)
            except (socket.timeout, TimeoutError):
                return None  # inactive connection: clean close
            if not chunk:
                if self._buffer:
                    # Last line without newline: still processed.
                    # (the size is already guaranteed <= max by the check
                    # performed before reading the chunk)
                    line = bytes(self._buffer)
                    self._buffer.clear()
                    return line
                return None
            self._buffer.extend(chunk)

    # -- response ----------------------------------------------------------
    def _write_response(self, response: dict, pre_serialized: bytes | None = None) -> None:
        if pre_serialized is not None:
            # Pre-serialized static response (full help): no re-encoding.
            payload = pre_serialized
        else:
            payload = jsonutil.dumps(response) + b"\n"
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (OSError, ConnectionError):
            # The client disconnected during the write (RST, BrokenPipe,
            # connection closed before reading): the response can no longer be
            # delivered. The handler loop will see the error/EOF on the next read
            # and close cleanly; no traceback must
            # pollute the logs nor fail the handler thread.
            pass

    def _send_error_response(self, code: str, message: str) -> None:
        response = {"success": False, "data": None, "error": {"code": code, "message": message}}
        # _write_response already absorbs write errors (client gone)
        self._write_response(response)

    def _log_request(self, meta: dict) -> None:
        entry = {
            "username": meta.get("username"),
            "command": meta.get("command"),
            "target_id": meta.get("target_id"),
            "result": meta.get("result", "ok"),
        }
        logger.info("request", extra={k: v for k, v in entry.items() if v is not None})


class _ConnectionPool:
    """Worker thread pool (daemon) reused between connections.

    The "one thread per connection" model of ``socketserver.ThreadingMixIn``
    creates and destroys a thread per request: measured with cProfile, this
    cost reaches ~1.7 ms per request (the client opens one connection per
    command). The pool reuses a fixed number of threads; the bound on
    simultaneous connections (``MAX_CONCURRENT_CONNECTIONS``) stays ensured
    by ``_connection_slots``, the same size as the pool: there is therefore
    never a connection queue beyond the bound.
    """

    def __init__(self, size: int) -> None:
        self._queue: queue.SimpleQueue[tuple | None] = queue.SimpleQueue()
        self._workers = [
            threading.Thread(
                target=self._run, daemon=True, name=f"synapse-worker-{i}"
            )
            for i in range(size)
        ]
        for thread in self._workers:
            thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # stop signal
                return
            fn, args = item
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - a worker never dies
                logger.exception("Error in a pool worker")

    def submit(self, fn, *args) -> None:  # noqa: ANN001
        self._queue.put((fn, args))

    def close(self) -> None:
        """Signals the stop to workers (queued tasks are processed
        first, then each worker exits)."""
        for _ in self._workers:
            self._queue.put(None)


class _PoolMixin:
    """Shared pool/semaphore logic for the local listeners (unix + tcp)."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        # The pool starts BEFORE the bind: the socket only becomes connectable
        # once the workers are ready, avoiding the window where the
        # socket accepts connections while the server is not yet assigned
        # (race detected by the stop tests).
        self._pool = _ConnectionPool(MAX_CONCURRENT_CONNECTIONS)
        try:
            super().__init__(*args, **kwargs)
        except BaseException:
            self._pool.close()
            raise
        # Bounds simultaneously handled connections (local anti-DoS:
        # a burst of connections must not exhaust the threads).
        self._connection_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CONNECTIONS)

    def process_request(self, request, client_address) -> None:  # noqa: ANN001
        # Briefly waits for a slot (inactive connections expire via the
        # timeout) then refuses the excess connection.
        if not self._connection_slots.acquire(timeout=CONNECTION_ACQUIRE_TIMEOUT):
            try:
                request.close()  # type: ignore[attr-defined]
            except OSError:
                pass
            return
        self._pool.submit(self.process_request_thread, request, client_address)

    def process_request_thread(self, request, client_address) -> None:  # noqa: ANN001
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self._pool.close()


class _ThreadingUnixServer(_PoolMixin, socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    pass


class _ThreadingTcpServer(_PoolMixin, socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Loopback TCP listener (Windows transport): binds 127.0.0.1 only."""

    def __init__(self, bind: tuple[str, int], handler_class) -> None:  # noqa: ANN001
        super().__init__(bind, handler_class)
        self.auth_token: str | None = None  # set by SynapseServer.start()


def _build_listener(config: Config, handler_class) -> socketserver.BaseServer:  # noqa: ANN001
    """Builds the local listener for the effective transport."""
    if transport.resolve_transport(config) == transport.TRANSPORT_TCP:
        return _ThreadingTcpServer(
            (transport.TCP_BIND_HOST, transport.transport_port(config)), handler_class
        )
    return _ThreadingUnixServer(config.socket_path, handler_class)


def lock_is_stale(lock_path: str | os.PathLike) -> bool:
    """A lock is stale if its content is the PID of a dead process.

    Shared between the server (restart) and the restore. Any content
    unknown (ex. "restore") or a live PID indicates an active lock.
    """
    try:
        content = Path(lock_path).read_text(encoding="ascii").strip()
    except OSError:
        return False
    try:
        pid = int(content)
    except ValueError:
        return False  # unknown content (ex. "restore"): the lock is active
    if pid <= 0:
        return False
    if platform.process_alive(pid):
        return False  # live process: the lock is active
    return True


class SynapseServer:
    """Controllable Unix socket server (clean start/stop)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.service = Service(config)
        self._server: socketserver.BaseServer | None = None
        self._lock_acquired = False
        self._stop_lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Prepares the storage, acquires the lock, creates the socket and serves."""
        try:
            ensure_storage(self.config)
        except StorageError as exc:
            print(f"synapse-server: {exc}", file=sys.stderr)
            sys.exit(1)
        # The cursor signing key is created at startup:
        # it is part of the storage (included in backups).
        load_or_create_key(self.config.cursor_key_path)
        self._acquire_lock()
        effective = transport.resolve_transport(self.config)
        if effective == transport.TRANSPORT_UNIX:
            self._prepare_socket_path()
        else:
            transport.ensure_token(self.config)
        self._write_web_token()
        server = _build_listener(self.config, _ConnectionHandler)
        server.service = self.service  # type: ignore[attr-defined]
        self._server = server
        if effective == transport.TRANSPORT_TCP:
            server.auth_token = transport.read_token(self.config)  # type: ignore[attr-defined]
            target = f"127.0.0.1:{transport.transport_port(self.config)}"
        else:
            os.chmod(self.config.socket_path, 0o600)
            target = os.path.basename(self.config.socket_path)
        logger.info(
            "server_started",
            extra={"result": "ok", "target_id": target},
        )
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            self.stop()

    def stop(self) -> None:
        """Clean stop: closes the socket, releases the lock.

        Idempotent and thread-safe: ``serve_forever`` also triggers
        ``stop()`` in its ``finally``; the two calls may
        overlap (server thread + calling thread).
        """
        with self._stop_lock:
            server = self._server
            if server is None:
                return
            self._server = None
            try:
                server.shutdown()
            except (OSError, RuntimeError):
                pass
            server.server_close()
        self._remove_socket_file()
        self._release_lock()

    # ------------------------------------------------------------------
    def _acquire_lock(self) -> None:
        lock_path = Path(self.config.lock_path)
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = -1
        for attempt in range(2):
            try:
                fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                if attempt == 0 and lock_is_stale(lock_path):
                    # Lock left by a dead process (crash): we remove it.
                    try:
                        os.unlink(lock_path)
                    except FileNotFoundError:
                        pass
                    continue
                print(
                    f"synapse-server: another service already uses {self.config.storage_dir} "
                    f"(lock {lock_path})",
                    file=sys.stderr,
                )
                sys.exit(1)
        assert fd != -1  # only reached after a successful creation
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        self._lock_acquired = True

    def _release_lock(self) -> None:
        if self._lock_acquired:
            try:
                os.unlink(self.config.lock_path)
            except FileNotFoundError:
                pass
            self._lock_acquired = False

    def _prepare_socket_path(self) -> None:
        socket_path = self.config.socket_path
        parent = os.path.dirname(socket_path)
        Path(parent).mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
        if os.path.exists(socket_path):
            if self._socket_in_use(socket_path):
                print(
                    f"synapse-server: the socket {socket_path} is already used by another service",
                    file=sys.stderr,
                )
                sys.exit(1)
            os.unlink(socket_path)  # orphan socket

    @staticmethod
    def _socket_in_use(socket_path: str) -> bool:
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(socket_path)
                return True
            finally:
                probe.close()
        except (OSError, ConnectionError):
            return False

    def _remove_socket_file(self) -> None:
        if transport.resolve_transport(self.config) == transport.TRANSPORT_UNIX:
            try:
                os.unlink(self.config.socket_path)
            except FileNotFoundError:
                pass
        transport.remove_token(self.config)
        self._remove_web_token()

    # ------------------------------------------------------------------
    # Local trust token (SPEC-WEB D5 amended)
    # ------------------------------------------------------------------
    def _write_web_token(self) -> None:
        """Generates the web interface trust token, injects it into
        the service and writes it in the run dir (0600). The web reads it at
        startup to authenticate without a password (organization
        selection login)."""
        token = secrets.token_urlsafe(32)
        self.service.set_web_token(token)
        path = os.path.join(transport.run_dir(self.config), "web_token")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode("ascii"))
        finally:
            os.close(fd)
        os.chmod(path, 0o600)

    def _remove_web_token(self) -> None:
        try:
            os.unlink(os.path.join(transport.run_dir(self.config), "web_token"))
        except FileNotFoundError:
            pass


def main() -> None:
    """Console entry point: ``synapse-server [--config path] [--verbose]``."""
    import argparse

    from .logging_setup import setup_logging

    parser = argparse.ArgumentParser(prog="synapse-server", description="Synapse messaging server")
    parser.add_argument("--config", default=None, help="JSON configuration file path")
    parser.add_argument("--verbose", action="store_true", help="Console logging")
    args = parser.parse_args()

    try:
        config = Config.load(args.config)
    except ValueError as exc:
        print(f"synapse-server: {exc}", file=sys.stderr)
        sys.exit(1)
    setup_logging(config, verbose=args.verbose)

    server = SynapseServer(config)

    def _shutdown(_signum, _frame) -> None:  # noqa: ANN001
        threading.Thread(target=server.stop, daemon=True).start()

    platform.install_stop_handlers(_shutdown)
    server.start()
