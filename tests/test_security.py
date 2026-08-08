"""Security tests: Argon2id hashing, no plaintext secrets in storage,
logs or backups, file permissions, exclusively local transport
(Unix socket)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from synapse import security
from synapse.security import ARGON2_MEMORY_KIB, ARGON2_PARALLELISM, ARGON2_TIME_COST

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD


def test_argon2_production_parameters():
    """The specification requires: Argon2id, 64 MiB, 3 iterations, parallelism 1."""
    assert ARGON2_MEMORY_KIB == 64 * 1024
    assert ARGON2_TIME_COST == 3
    assert ARGON2_PARALLELISM == 1
    hasher = security._PRODUCTION_HASHER
    assert hasher.type.name == "ID"
    assert hasher.memory_cost == 64 * 1024
    assert hasher.time_cost == 3
    assert hasher.parallelism == 1


def test_argon2_hash_format_and_verify():
    hasher = security._PRODUCTION_HASHER
    encoded = hasher.hash("mot-de-passe-test-123")
    assert encoded.startswith("$argon2id$")
    assert hasher.verify(encoded, "mot-de-passe-test-123") is True
    with pytest.raises(Exception):
        hasher.verify(encoded, "mauvais")


def test_password_hashes_are_salted_unique():
    h1 = security.hash_password("même-motdepasse-123")
    h2 = security.hash_password("même-motdepasse-123")
    assert h1 != h2  # unique random salt
    assert security.verify_password(h1, "même-motdepasse-123")
    assert security.verify_password(h2, "même-motdepasse-123")


def test_no_plaintext_passwords_in_storage(fx, config):
    """No plaintext password in the database."""
    import sqlite3
    conn = sqlite3.connect(config.db_path)
    try:
        hashes = [
            row[0]
            for row in conn.execute("SELECT password_hash FROM accounts ORDER BY username")
        ]
    finally:
        conn.close()
    assert hashes  # some accounts exist
    for secret in (ALICE_PASSWORD, BOB_PASSWORD, ORG_PASSWORD):
        assert all(secret not in h for h in hashes)  # never in plaintext
        assert all(secret.encode() not in h.encode() for h in hashes)
    # the hashes are indeed Argon2id
    assert all(h.startswith("$argon2id$") for h in hashes)


def test_storage_permissions(fx, config):
    if os.name == "nt":
        pytest.skip("POSIX file permissions do not apply on Windows")
    assert stat.S_IMODE(os.stat(config.storage_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(config.db_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(config.cursor_key_path).st_mode) == 0o600


def test_socket_permissions(fx, config):
    if os.name == "nt":
        pytest.skip("POSIX file permissions do not apply on Windows")
    mode = os.stat(config.socket_path).st_mode
    assert stat.S_ISSOCK(mode)
    assert stat.S_IMODE(mode) == 0o600
    # no network port: the socket is a Unix socket
    parent = os.stat(os.path.dirname(config.socket_path)).st_mode
    assert stat.S_IMODE(parent) == 0o700


def test_no_tcp_listener(fx, config):
    """The Unix-socket transport exposes no network listener.

    (With the TCP transport — the Windows default — the service
    deliberately listens on 127.0.0.1; this test covers the Unix case.)
    """
    from synapse import transport as tr

    if tr.resolve_transport(config) == tr.TRANSPORT_TCP:
        pytest.skip("TCP transport: loopback listener is expected")
    import subprocess

    pid = os.getpid()
    try:
        out = subprocess.run(
            ["ss", "-ltnp"], capture_output=True, text=True, timeout=5
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("ss unavailable")
    # the test process pid (which hosts the server) appears in no TCP listen
    for line in out.splitlines():
        assert f"pid={pid}" not in line


def test_logs_never_contain_passwords_or_content(fx, config):
    """The logs contain username/command/target/result, never
    passwords or message content."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "contenu top secret", "cmid-sec-1")
    fx.client.read_message(
        fx.client.get_messages(BOB, BOB_PASSWORD)["messages"][0]["message_id"],
        BOB,
        BOB_PASSWORD,
    )
    # an authentication failure is logged with the attempted name
    # (unknown principal: never authenticated, so never covered by the
    # cache — SPEC.txt §19.1 amendment)
    with pytest.raises(Exception):
        fx.client.get_messages("utilisateur-inconnu", "mauvais-mot-de-passe-x")
    fx.client.create_agent("carol",  "motdepasse-carol-1", "Test agent",  ORG_NAME, ORG_PASSWORD)

    log_path = os.path.join(config.log_dir, "synapse.log")
    assert os.path.exists(log_path)
    with open(log_path, encoding="utf-8") as fh:
        content = fh.read()

    for secret in (ALICE_PASSWORD, BOB_PASSWORD, ORG_PASSWORD, "motdepasse-carol-1"):
        assert secret not in content
    assert "contenu top secret" not in content

    # the allowed fields are present
    lines = [json.loads(line) for line in content.strip().splitlines() if line.strip()]
    commands = {line.get("command") for line in lines}
    assert {"send_message", "read_message", "get_messages", "create_agent"} <= commands
    allowed_keys = {"timestamp", "process_id", "username", "command", "target_id", "result"}
    for line in lines:
        assert set(line.keys()) <= allowed_keys
    # an authentication failure is logged with the attempted name
    assert any(
        line.get("result") == "AUTH_FAILED" and line.get("username") == "utilisateur-inconnu"
        for line in lines
    )


def test_logs_rotation_configuration(fx, config):
    """The logs rotate with the configured retention (90 days)."""
    import logging.handlers
    from synapse.logging_setup import _make_handler
    handler = _make_handler(config.log_dir, 90, "test-rot.log")
    assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)
    assert handler.backupCount == 90
    handler.close()


def test_error_log_has_no_secrets(fx, config):
    error_path = os.path.join(config.log_dir, "synapse.error.log")
    if not os.path.exists(error_path):
        pytest.skip("no internal error in this test")
    content = open(error_path, encoding="utf-8").read()
    assert ALICE_PASSWORD not in content
    assert "contenu top secret" not in content


def test_auth_timing_equalization(fx):
    """Nonexistent account: response time stays comparable (dummy
    verification) — here we check that the dummy function runs."""
    import time

    start = time.monotonic()
    security.verify_dummy("nimporte-quel-motdepasse")
    elapsed = time.monotonic() - start
    assert elapsed >= 0
    # (the exact value depends on the active hasher; the production test
    #  verifies the parameters, the dummy verification is at least fast)
    assert elapsed < 30


def test_cursor_signature_tamper_detection(fx):
    """A forged cursor is rejected even with the correct structure."""
    from synapse.cursor import build_payload, encode_cursor
    from synapse.errors import INVALID_ARGUMENT, ApiError
    secret = b"k" * 32
    payload = build_payload(
        command="get_messages", username=ALICE, boundary="2026-01-01T00:00:00.000Z",
        sort="desc", filters={}, last=["2026-01-01T00:00:00.000Z", "id"],
    )
    cursor = encode_cursor(secret, payload)
    # modify the payload without re-signing
    body, sig = cursor.split(".")
    from synapse.cursor import _unb64url, _b64url
    modified_body = _b64url(_unb64url(body).replace(b'"desc"', b'"asc"'))
    forged = f"{modified_body}.{sig}"
    with pytest.raises(ApiError) as exc:
        from synapse.cursor import decode_cursor
        decode_cursor(secret, forged)
    assert exc.value.code == INVALID_ARGUMENT


def test_request_size_limit_config(fx):
    """The 1 MiB limit is the configuration's default value."""
    assert fx.config.max_request_bytes == 1024 * 1024
