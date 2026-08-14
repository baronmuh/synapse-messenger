"""Tests atomiques du client (chemins d'erreur de transport) et de la
sauvegarde (helpers)."""

from __future__ import annotations

import json
import os
import socketserver
import threading

import pytest

from synapse.backup import BackupError, _temp_path, backup
from synapse.client import Client, ClientTransportError

from .conftest import ALICE, ALICE_PASSWORD


# ---------------------------------------------------------------------------
# Client : erreurs de transport
# ---------------------------------------------------------------------------


def test_client_request_too_large():
    client = Client("/chemin/inexistant.sock")
    with pytest.raises(ClientTransportError):
        client.request("send_message", {"message": "x" * (1024 * 1024)})


def test_client_connection_refused(tmp_path):
    client = Client(str(tmp_path / "absent.sock"))
    with pytest.raises(ClientTransportError) as exc:
        client.request("get_notifications", {})
    assert "Cannot reach" in str(exc.value)


class _DummyServer(socketserver.ThreadingUnixStreamServer):
    """Test server that responds according to a predefined script."""

    daemon_threads = True

    def __init__(self, path, script):
        self.script = script
        super().__init__(path, _DummyHandler)

    def next_response(self):
        return self.script.pop(0) if self.script else b""


class _DummyHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.recv(65536)  # consumes the request
        data = self.server.next_response()  # type: ignore[attr-defined]
        if data is not None:
            self.wfile.write(data)
            self.wfile.flush()


@pytest.fixture()
def dummy(tmp_path):
    path = str(tmp_path / "dummy.sock")
    server = _DummyServer(path, [])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, path
    server.shutdown()
    server.server_close()


def test_client_empty_response(dummy):
    server, path = dummy
    server.script = [b""]  # connection closed without a response
    client = Client(path)
    with pytest.raises(ClientTransportError) as exc:
        client.request("get_notifications", {})
    assert "closed the connection" in str(exc.value)


def test_client_invalid_json_response(dummy):
    server, path = dummy
    server.script = [b"pas du json\n"]
    client = Client(path)
    with pytest.raises(ClientTransportError):
        client.request("get_notifications", {})


def test_client_non_dict_response(dummy):
    server, path = dummy
    server.script = [b"42\n"]
    client = Client(path)
    with pytest.raises(ClientTransportError):
        client.request("get_notifications", {})


def test_client_response_too_large(dummy):
    server, path = dummy
    server.script = [b"x" * (16 * 1024 * 1024 + 100)]
    client = Client(path)
    with pytest.raises(ClientTransportError) as exc:
        client.request("get_notifications", {})
    assert "Response too large" in str(exc.value)


def test_client_error_envelope_raises_api_error(fx):
    client = Client(fx.config.socket_path)
    from synapse.client import ApiClientError
    with pytest.raises(ApiClientError) as exc:
        client.request("get_messages", {
            "my_name_auth": ALICE, "my_password_auth": "mauvais",
            "status": None, "sender_username": None, "conversation_id": None,
            "limit": 50, "cursor": None,
        })
    assert exc.value.code == "AUTH_FAILED"
    assert exc.value.message  # message informatif


def test_client_success_envelope(fx):
    data = fx.client.request("get_notifications", {
        "my_name_auth": ALICE, "my_password_auth": ALICE_PASSWORD,
        "limit": 50, "cursor": None,
    })
    assert set(data) == {"unread_by_sender", "needs_reply", "next_cursor"}


# ---------------------------------------------------------------------------
# Backup : helpers
# ---------------------------------------------------------------------------


def test_temp_path_inside_storage(config):
    path = _temp_path(config, "synapse-t-", ".tmp")
    try:
        assert os.path.dirname(path) == config.storage_dir
        assert os.path.basename(path).startswith("synapse-t-")
        assert os.path.basename(path).endswith(".tmp")
        assert os.stat(path).st_mode & 0o077 == 0
    finally:
        os.unlink(path)


def test_backup_custom_output_path(fx, config):
    fx.send(ALICE, ALICE_PASSWORD, "bob", "hello", "cmid-bkout-1")
    os.makedirs(config.backup_dir, exist_ok=True)
    custom = os.path.join(config.backup_dir, "personnalise.synbk")
    path = backup(config, output_path=custom)
    assert path == custom
    assert os.path.exists(custom)
    # the file starts with the magic
    with open(custom, "rb") as fh:
        assert fh.read(7) == b"SYNBK\x01\n"


def test_backup_output_missing_db(config):
    """backup on a storage without a database: creates a fresh database and backs it up
    (behavior: backing up an empty storage is valid)."""
    path = backup(config)
    assert os.path.exists(path)


def test_restore_rejects_unknown_format(fx, config):
    from synapse.backup import _MAGIC, _NONCE_LENGTH, restore
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from synapse.security import load_or_create_key
    fx.send(ALICE, ALICE_PASSWORD, "bob", "x", "cmid-bkfmt-1")
    fx.server.stop()
    key = load_or_create_key(config.backup_key_path)
    header = json.dumps({"format": 99, "cursor_key": "AAAA"}).encode()
    nonce = os.urandom(_NONCE_LENGTH)
    ct = AESGCM(key).encrypt(nonce, header + b"\n" + b"db", None)
    path = os.path.join(config.backup_dir, "format99.synbk")
    os.makedirs(config.backup_dir, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_MAGIC + nonce + ct)
    with pytest.raises(BackupError):
        restore(config, path)


def test_restore_rejects_wrong_cursor_key_length(fx, config):
    """A cursor key that is valid base64 but of wrong length is
    rejected."""
    from synapse.backup import _MAGIC, _NONCE_LENGTH, restore
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from synapse.security import load_or_create_key
    import base64
    fx.server.stop()
    key = load_or_create_key(config.backup_key_path)
    # valid base64 of 16 bytes (the expected key is 32)
    header = json.dumps({
        "format": 1,
        "cursor_key": base64.b64encode(b"k" * 16).decode(),
    }).encode()
    nonce = os.urandom(_NONCE_LENGTH)
    ct = AESGCM(key).encrypt(nonce, header + b"\n" + b"db", None)
    path = os.path.join(config.backup_dir, "badkey.synbk")
    os.makedirs(config.backup_dir, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_MAGIC + nonce + ct)
    with pytest.raises(BackupError):
        restore(config, path)
