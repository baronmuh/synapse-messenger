"""Exhaustive campaign — final pass: remaining defensive branches and
unusual cases (exception handlers, timeouts, races, console entry
points)."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import threading
import time

import pytest

from synapse.backup import BackupError, backup, restore
from synapse.config import Config
from synapse.server import lock_is_stale

from .conftest import (
    ORG_NAME,
    ORG_PASSWORD,
    ALICE,
    ALICE_PASSWORD,
    make_server,
)


def _config_file(tmp_path, config: Config) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.to_dict()))
    return str(path)


# ---------------------------------------------------------------------------
# Backup: defensive exception handlers
# ---------------------------------------------------------------------------


def test_backup_reraises_backup_error(config, monkeypatch):
    def boom(config, output_path):
        raise BackupError("interne")

    monkeypatch.setattr("synapse.backup._backup", boom)
    with pytest.raises(BackupError):
        backup(config)


def test_backup_tolerates_missing_temp_file(config, monkeypatch):
    """Removing the temporary file may fail (FileNotFoundError) without
    failing the backup."""
    real_unlink = os.unlink

    def flaky_unlink(path):
        if str(path).startswith(config.storage_dir) and "synapse-bak-" in str(path):
            raise FileNotFoundError()
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", flaky_unlink)
    path = backup(config)  # must not raise
    assert os.path.exists(path)


def test_restore_integrity_failure_on_real_corrupt_db(fx, config):
    """A SQLite database whose integrity is broken (missing index) fails the
    check before replacement."""
    fx.send(ALICE, ALICE_PASSWORD, "bob", "x", "cmid-cf-int")
    fx.server.stop()
    # create a real database then break its integrity (index removed via
    # writable_schema: integrity_check returns a non-ok row)
    tmp = os.path.join(config.storage_dir, "real.db")
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE TABLE t (x INTEGER PRIMARY KEY, y TEXT)")
    conn.execute("CREATE INDEX iy ON t(y)")
    conn.execute("INSERT INTO t VALUES (1, 'a')")
    conn.commit()
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute("DELETE FROM sqlite_master WHERE name='iy'")
    conn.commit()
    conn.close()
    broken = open(tmp, "rb").read()
    os.unlink(tmp)

    from synapse.backup import _MAGIC, _NONCE_LENGTH
    from synapse.security import load_or_create_key
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64
    key = load_or_create_key(config.backup_key_path)
    nonce = os.urandom(_NONCE_LENGTH)
    header = json.dumps({"format": 1,
                         "cursor_key": base64.b64encode(b"k" * 32).decode()}).encode()
    ct = AESGCM(key).encrypt(nonce, header + b"\n" + broken, None)
    path = os.path.join(config.backup_dir, "corrupt.synbk")
    os.makedirs(config.backup_dir, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_MAGIC + nonce + ct)
    with pytest.raises(BackupError):
        restore(config, path)
    # the original database is intact (replacement refused)
    assert os.path.exists(config.db_path)


def test_restore_tolerates_missing_wal_files(fx, config, monkeypatch):
    """Removing missing WAL files does not fail the restore."""
    fx.send(ALICE, ALICE_PASSWORD, "bob", "x", "cmid-cf-wal")
    path = backup(config)
    fx.server.stop()
    real_unlink = os.unlink

    def flaky_unlink(p):
        if str(p).endswith("-wal") or str(p).endswith("-shm"):
            raise FileNotFoundError()
        return real_unlink(p)

    monkeypatch.setattr(os, "unlink", flaky_unlink)
    restore(config, path)  # must not raise


def test_restore_lock_release_tolerates_missing(config, monkeypatch):
    """Releasing the lock may fail (file already removed)."""
    from .conftest import make_server as ms
    srv = ms(config, org=True)
    try:
        srv.client.create_agent(ALICE,  ALICE_PASSWORD, "Test agent",  ORG_NAME, ORG_PASSWORD)
        path = backup(config)
    finally:
        srv.stop()
    real_unlink = os.unlink

    def flaky_unlink(p):
        if str(p) == config.lock_path:
            raise FileNotFoundError()
        return real_unlink(p)

    monkeypatch.setattr(os, "unlink", flaky_unlink)
    restore(config, path)  # must not raise
    assert os.path.exists(config.db_path)


def test_acquire_lock_race_raises_backup_error(config, monkeypatch):
    """If the lock appears between the check and the open: BackupError."""
    from synapse.backup import _acquire_service_lock

    def racing_open(path, flags, mode=0o777):
        raise FileExistsError()

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(BackupError):
        _acquire_service_lock(config)


def test_fsync_dir_tolerates_oserror(fx, config, monkeypatch):
    fx.send(ALICE, ALICE_PASSWORD, "bob", "x", "cmid-cf-fs")
    path = backup(config)
    fx.server.stop()
    real_open = os.open

    def denied(path, flags, mode=0o777, *args, **kwargs):
        if flags == os.O_RDONLY:  # opening the directory for fsync
            raise OSError("pas possible")
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    restore(config, path)  # _fsync_dir fails silently


def test_backup_main_error_exits_1(tmp_path, monkeypatch, capsys):
    from synapse.backup import backup_main
    bad = tmp_path / "bad.json"
    bad.write_text("{invalide")
    monkeypatch.setattr(sys, "argv", ["synapse-backup", "--config", str(bad)])
    with pytest.raises(SystemExit) as exc:
        backup_main()
    assert exc.value.code == 1


def test_restore_main_error_exits_1(fx, config, tmp_path, monkeypatch, capsys):
    from synapse.backup import restore_main
    fx.server.stop()
    monkeypatch.setattr(
        sys, "argv",
        ["synapse-restore", "/chemin/absent.synbk",
         "--config", _config_file(tmp_path, config), "--force"],
    )
    with pytest.raises(SystemExit) as exc:
        restore_main()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Server: idle timeout, size at end of stream, locks, startup
# ---------------------------------------------------------------------------


def test_server_idle_timeout_closes_connection(config, monkeypatch):
    """An idle connection is closed by the server after the timeout."""
    import synapse.server as server_mod
    monkeypatch.setattr(server_mod, "CONNECTION_IDLE_TIMEOUT", 1)
    srv = make_server(config, org=True)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(config.socket_path)
        s.settimeout(5)
        start = time.monotonic()
        data = s.recv(1024)  # nothing is sent: the server closes
        elapsed = time.monotonic() - start
        s.close()
        assert data == b""  # clean close
        assert elapsed >= 0.5
        # the service still responds
        srv.client.create_agent(ALICE, ALICE_PASSWORD, "Agent", ORG_NAME, ORG_PASSWORD)
        assert srv.client.get_messages(ALICE, ALICE_PASSWORD) is not None
    finally:
        srv.stop()


def test_server_oversized_partial_line_at_eof(config):
    """An incomplete line over 1 MiB at end of connection: rejected."""
    srv = make_server(config, org=True)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(config.socket_path)
        s.sendall(b"A" * (config.max_request_bytes + 1))
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        s.close()
        resp = json.loads(b"".join(chunks))
        assert resp["error"]["code"] == "INVALID_ARGUMENT"
    finally:
        srv.stop()


def test_lock_is_stale_directory(tmp_path):
    """A lock whose path is a directory: considered not stale."""
    d = tmp_path / "lockdir"
    d.mkdir()
    assert lock_is_stale(d) is False


def test_lock_is_stale_generic_oserror(tmp_path, monkeypatch):
    lock = tmp_path / "l.lock"
    lock.write_text(str(os.getpid()))

    def generic_error(*args, **kwargs):
        raise OSError("quelconque")

    monkeypatch.setattr(os, "kill", generic_error)
    assert lock_is_stale(lock) is False


def test_server_stop_tolerates_shutdown_error(config, monkeypatch):
    srv = make_server(config, org=True)
    try:
        server = srv.server._server
        assert server is not None
        original = server.shutdown

        def broken_shutdown():
            original()  # actually stops serve_forever
            raise OSError("already closed")  # then simulates an error

        monkeypatch.setattr(server, "shutdown", broken_shutdown)
        srv.server.stop()  # must not raise
        # the socket and the lock are cleaned up
        assert not os.path.exists(config.socket_path)
        assert not os.path.exists(config.lock_path)
    finally:
        srv.server.stop()


def test_server_refuses_when_socket_in_use(config, tmp_path):
    """A socket already active at the expected location prevents startup."""
    from synapse.server import SynapseServer
    # releases the lock but keeps an active socket
    os.makedirs(os.path.dirname(config.socket_path), exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(config.socket_path)
    listener.listen(1)
    try:
        second = SynapseServer(config)
        with pytest.raises(SystemExit) as exc:
            second.start()
        assert exc.value.code == 1
    finally:
        listener.close()
        try:
            os.unlink(config.socket_path)
        except FileNotFoundError:
            pass


def test_server_main_starts_with_config(config, tmp_path, monkeypatch):
    """server_main() loads the configuration and starts the server."""
    from synapse.server import main as server_main, SynapseServer
    called = {}

    def fake_start(self):
        called["started"] = True

    monkeypatch.setattr(SynapseServer, "start", fake_start)
    monkeypatch.setattr(
        sys, "argv", ["synapse-server", "--config", _config_file(tmp_path, config)]
    )
    server_main()
    assert called.get("started") is True


def test_server_main_sigterm_triggers_stop(config, tmp_path, monkeypatch):
    """A SIGTERM triggers a clean stop (stop thread)."""
    import signal

    from synapse.server import main as server_main, SynapseServer
    stopped = {}

    def fake_stop(self):
        stopped["stopped"] = True

    monkeypatch.setattr(SynapseServer, "start", lambda self: None)
    monkeypatch.setattr(SynapseServer, "stop", fake_stop)
    monkeypatch.setattr(
        sys, "argv", ["synapse-server", "--config", _config_file(tmp_path, config)]
    )
    server_main()
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(0.3)  # let the signal thread run
    assert stopped.get("stopped") is True


def test_server_connection_reset_tolerated(config):
    """An abruptly closed connection (RST) does not bring the service down."""
    import struct

    srv = make_server(config, org=True)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.connect(config.socket_path)
        s.sendall(b'{"api_version":"v1"')  # incomplete request
        time.sleep(0.3)  # let the handler stay blocked in recv
        s.close()  # immediate RST
        time.sleep(0.2)
        # the service still responds
        srv.client.create_agent(ALICE, ALICE_PASSWORD, "Agent", ORG_NAME, ORG_PASSWORD)
        assert srv.client.get_messages(ALICE, ALICE_PASSWORD) is not None
    finally:
        srv.stop()


def test_handler_tolerates_recv_error():
    """A read error (dropped connection) ends the handler without an
exception."""
    from synapse.server import _ConnectionHandler

    handler = _ConnectionHandler.__new__(_ConnectionHandler)

    class FakeService:
        config = Config.from_dict({"max_request_bytes": 1024 * 1024})

    class FakeServer:
        service = FakeService()

    class DeadRequest:
        def recv(self, n):
            raise OSError("connection dropped")

    class Writer:
        def write(self, payload):
            raise AssertionError("must not write")

        def flush(self):
            pass

    handler.server = FakeServer()
    handler.request = DeadRequest()
    handler.wfile = Writer()
    handler.handle()  # must not raise


def test_server_send_error_response_tolerates_dead_client():
    """Writing an error response on a dead connection does not raise."""
    from synapse.server import _ConnectionHandler

    handler = _ConnectionHandler.__new__(_ConnectionHandler)

    class DeadWriter:
        def write(self, payload):
            raise ConnectionResetError("client parti")

        def flush(self):
            pass

    handler.wfile = DeadWriter()
    handler._send_error_response("INVALID_ARGUMENT", "test")  # must not raise


def test_server_write_response_tolerates_dead_client():
    """Writing a command response on a dead connection does not raise
    (BrokenPipeError/ConnectionResetError absorbed: the handler thread
    survives, the loop closes cleanly at the next EOF)."""
    from synapse.server import _ConnectionHandler

    handler = _ConnectionHandler.__new__(_ConnectionHandler)

    class DeadWriter:
        def write(self, payload):
            raise BrokenPipeError("client parti")

        def flush(self):
            raise ConnectionResetError("client parti")

    handler.wfile = DeadWriter()
    handler._write_response({"success": True, "data": {}, "error": None})  # must not raise


def test_server_client_abort_mid_response_no_traceback(config, capfd):
    """A client that abruptly closes (RST) while the response is being written
    produces no traceback and the service keeps responding."""
    import struct

    srv = make_server(config, org=True)
    try:
        srv.client.create_agent(ALICE, ALICE_PASSWORD, "Agent", ORG_NAME, ORG_PASSWORD)
        payload = json.dumps({
            "api_version": "v2",
            "command": "help",
            "parameters": {"my_name_auth": ALICE,
                           "my_password_auth": ALICE_PASSWORD,
                           "command_name": None},
        }).encode("utf-8") + b"\n"
        for _ in range(10):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            s.connect(config.socket_path)
            s.sendall(payload)
            s.close()  # immediate RST: the server's response lands on a
            # dead connection (BrokenPipeError on the server side)
        time.sleep(0.3)
        # the service still responds correctly
        assert srv.client.help(ALICE, ALICE_PASSWORD)["documentation"]
        _, err = capfd.readouterr()
        assert "Traceback" not in err
    finally:
        srv.stop()


def test_server_process_request_tolerates_close_error(monkeypatch):
    """Closing a surplus connection may fail without side effects."""
    import threading as th

    from synapse.server import _ThreadingUnixServer

    server = _ThreadingUnixServer.__new__(_ThreadingUnixServer)
    server._connection_slots = th.BoundedSemaphore(0)  # always full

    class StubbornSocket:
        def close(self):
            raise OSError("fermeture impossible")

    server.process_request(StubbornSocket(), None)  # must not raise


def test_server_stale_lock_unlink_failure_exits(config, tmp_path, monkeypatch):
    """If removing a stale lock fails, startup is refused."""
    from synapse.server import SynapseServer
    fresh = Config.from_dict({
        "storage_dir": str(tmp_path / "data"),
        "socket_path": str(tmp_path / "run" / "synapse.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "bk"),
    })
    os.makedirs(fresh.storage_dir, exist_ok=True)
    with open(fresh.lock_path, "w") as fh:
        fh.write("99999999")  # dead PID -> stale

    def stuck_unlink(path):
        raise FileNotFoundError()

    monkeypatch.setattr(os, "unlink", stuck_unlink)
    with pytest.raises(SystemExit) as exc:
        SynapseServer(fresh).start()
    assert exc.value.code == 1


def test_restore_integrity_failure_cleanup_missing(fx, config, monkeypatch):
    """Temporary file cleanup tolerates its disappearance (race)."""
    fx.server.stop()
    # deliberately corrupted database (non-ok integrity)
    tmp = os.path.join(config.storage_dir, "real.db")
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE TABLE t (x INTEGER PRIMARY KEY, y TEXT)")
    conn.execute("CREATE INDEX iy ON t(y)")
    conn.execute("INSERT INTO t VALUES (1, 'a')")
    conn.commit()
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute("DELETE FROM sqlite_master WHERE name='iy'")
    conn.commit()
    conn.close()
    broken = open(tmp, "rb").read()
    os.unlink(tmp)

    from synapse.backup import _MAGIC, _NONCE_LENGTH
    from synapse.security import load_or_create_key
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64
    key = load_or_create_key(config.backup_key_path)
    nonce = os.urandom(_NONCE_LENGTH)
    header = json.dumps({"format": 1,
                         "cursor_key": base64.b64encode(b"k" * 32).decode()}).encode()
    ct = AESGCM(key).encrypt(nonce, header + b"\n" + broken, None)
    path = os.path.join(config.backup_dir, "corrupt2.synbk")
    os.makedirs(config.backup_dir, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_MAGIC + nonce + ct)

    real_unlink = os.unlink

    def flaky_unlink(p):
        if "synapse-res-" in str(p):
            raise FileNotFoundError()
        return real_unlink(p)

    monkeypatch.setattr(os, "unlink", flaky_unlink)
    with pytest.raises(BackupError):
        restore(config, path)


def test_restore_stale_lock_unlink_failure(fx, config, monkeypatch):
    """If removing a stale lock fails during restore, the restore refuses
    (lock still present)."""
    fx.send(ALICE, ALICE_PASSWORD, "bob", "x", "cmid-cf-slk")
    path = backup(config)
    fx.server.stop()
    with open(config.lock_path, "w") as fh:
        fh.write("99999999")  # stale lock

    def stuck_unlink(p):
        if str(p) == config.lock_path:
            raise FileNotFoundError()
        return os.unlink(p)

    monkeypatch.setattr(os, "unlink", stuck_unlink)
    with pytest.raises(BackupError):
        restore(config, path)


# ---------------------------------------------------------------------------
# Installation: storage error
# ---------------------------------------------------------------------------


def test_org_init_main_storage_error(tmp_path, monkeypatch, capsys):
    from synapse.install import org_init_main
    blocker = tmp_path / "bloque"
    blocker.write_text("x")
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps({
        "storage_dir": str(blocker),
        "socket_path": str(tmp_path / "s.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "bk"),
    }))
    monkeypatch.setattr(sys, "argv", ["synapse-init-org", "--config", str(conf)])
    monkeypatch.setattr("builtins.input", lambda prompt="": "admin1")
    monkeypatch.setattr("synapse.install.getpass.getpass", lambda prompt="": "motdepasse-123")
    with pytest.raises(SystemExit) as exc:
        org_init_main()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Security: key race with invalid size
# ---------------------------------------------------------------------------


def test_key_race_with_wrong_size(tmp_path, monkeypatch):
    from synapse import security
    path = str(tmp_path / "race3.bin")
    real_open = os.open

    def racing_open(file, flags, mode=0o777):
        if not os.path.exists(file):
            fd = real_open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, b"trop court")
            os.close(fd)
        raise FileExistsError()

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(ValueError):
        security.load_or_create_key(path)


# ---------------------------------------------------------------------------
# Store: conversation race recovery (defensive branch)
# ---------------------------------------------------------------------------


def test_fetch_or_create_conversation_recovers_existing(config, monkeypatch):
    """After a uniqueness collision, the re-read returns the winning
    conversation (recovery branch)."""
    from synapse.store import messages
    conn = db_connect(config)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO conversations (conversation_id, key, created_at) VALUES (?, ?, ?)",
            ("c-1", "alice:bob", "2026-01-01T00:00:00.000Z"),
        )
        real_get = messages.get_conversation_by_key
        calls = {"n": 0}

        def blind_first(conn_, key):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # the INSERT will therefore collide
            return real_get(conn_, key)

        monkeypatch.setattr(messages, "get_conversation_by_key", blind_first)
        conv_id = messages.fetch_or_create_conversation(conn, "alice:bob", "2026-01-01T00:00:00.000Z")
        assert conv_id == "c-1"
        assert calls["n"] == 2  # re-read after collision
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_fetch_or_create_conversation_recovers_nothing(config, monkeypatch):
    """If the re-read fails after a uniqueness collision, the original error
    is propagated (defensive branch)."""
    from synapse.store import messages
    conn = db_connect(config)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO conversations (conversation_id, key, created_at) VALUES (?, ?, ?)",
            ("c-1", "alice:bob", "2026-01-01T00:00:00.000Z"),
        )
        monkeypatch.setattr(messages, "get_conversation_by_key", lambda c, k: None)
        with pytest.raises(sqlite3.IntegrityError):
            messages.fetch_or_create_conversation(conn, "alice:bob", "2026-01-01T00:00:00.000Z")
    finally:
        conn.close()


def db_connect(config):
    from synapse import db
    return db.connect(config)
