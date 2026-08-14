"""Backup and restore tests (section 15): encryption, no plaintext
passwords, restore without identifier regeneration, protection against
corrupted files."""

from __future__ import annotations

import os

import pytest

from synapse.backup import BackupError, backup, restore

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD


def _seed_state(fx):
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "message important", "cmid-bk-1")
    m2 = fx.send(BOB, BOB_PASSWORD, ALICE, "reply", "cmid-bk-2")
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    fx.client.mark_conversation_no_reply(m1["conversation_id"], BOB, BOB_PASSWORD)
    return m1, m2


def test_backup_restore_roundtrip(fx, config):
    m1, m2 = _seed_state(fx)
    path = backup(config)

    # modify the state after the backup
    fx.send(ALICE, ALICE_PASSWORD, BOB, "after backup", "cmid-bk-3")

    fx.server.stop()
    restore(config, path)
    # restart on the restored storage
    from .conftest import make_server
    server2 = make_server(config, org=False)
    try:
        c2 = server2.client
        # restored accounts
        assert c2.get_messages(ALICE, ALICE_PASSWORD) is not None
        # the message added after the backup is gone
        inbox = c2.get_messages(BOB, BOB_PASSWORD)
        assert [m["content"] for m in inbox["messages"]] == ["message important"]
        # identifiers, dates and statuses preserved identically
        conv = c2.get_conversation(ALICE, BOB, BOB_PASSWORD)
        restored = conv["messages"]
        assert restored[0]["message_id"] == m1["message_id"]
        assert restored[0]["created_at"] == m1["created_at"]
        assert restored[0]["status"] == "read"
        assert restored[1]["message_id"] == m2["message_id"]
        # reply states restored
        assert conv["reply_status"] == "no_reply_needed"
        # restored idempotence
        again = c2.send_message(BOB, "message important", "cmid-bk-1", ALICE, ALICE_PASSWORD)
        assert again["message_id"] == m1["message_id"]
    finally:
        server2.stop()


def test_backup_encrypted_no_plaintext(fx, config):
    _seed_state(fx)
    path = backup(config)
    raw = open(path, "rb").read()
    # no plaintext password
    assert ALICE_PASSWORD.encode() not in raw
    assert ORG_PASSWORD.encode() not in raw
    # no message content in plaintext (the DB is encrypted)
    assert b"message important" not in raw
    assert "reply".encode("utf-8") not in raw


def test_backup_key_outside_backup(fx, config):
    """The encryption key lives outside the backups."""
    _seed_state(fx)
    path = backup(config)
    raw = open(path, "rb").read()
    key = open(config.backup_key_path, "rb").read()
    assert key not in raw


def test_backup_permissions(fx, config):
    path = backup(config)
    assert os.stat(path).st_mode & 0o077 == 0
    assert os.stat(config.backup_key_path).st_mode & 0o077 == 0


def test_restore_tampered_backup_rejected(fx, config):
    _seed_state(fx)
    fx.server.stop()  # restore requires a stopped service
    path = backup(config)
    raw = bytearray(open(path, "rb").read())
    raw[len(raw) // 2] ^= 0xFF  # corruption
    tampered = path + ".corrupt"
    open(tampered, "wb").write(bytes(raw))
    with pytest.raises(BackupError):
        restore(config, tampered)


def test_restore_wrong_key_rejected(fx, config):
    _seed_state(fx)
    fx.server.stop()
    path = backup(config)
    # replace the key with another one: decryption must fail
    other_key = os.urandom(32)
    open(config.backup_key_path, "wb").write(other_key)
    with pytest.raises(BackupError):
        restore(config, path)


def test_restore_refused_while_service_running(fx, config):
    _seed_state(fx)
    path = backup(config)
    # the service lock is present: restore refused
    with pytest.raises(BackupError):
        restore(config, path)


def test_restore_requires_force_flag_via_cli(fx, config):
    from synapse.backup import restore_main
    import sys
    _seed_state(fx)
    path = backup(config)
    old_argv = sys.argv
    sys.argv = ["synapse-restore", path, "--config", str(config)]
    try:
        with pytest.raises(SystemExit) as exc:
            restore_main()
        assert exc.value.code == 1
    finally:
        sys.argv = old_argv


def test_backup_fresh_storage_roundtrip(config):
    """A backup of a virgin storage is valid and restorable."""
    path = backup(config)
    assert os.path.exists(path)
    restore(config, path)
    from .conftest import make_server
    server = make_server(config, org=True)
    try:
        assert server.client.create_agent(
            ALICE, ALICE_PASSWORD, "Agent de test", ORG_NAME, ORG_PASSWORD
        )["username"] == ALICE
    finally:
        server.stop()


def test_restore_missing_backup(fx, config):
    fx.server.stop()
    with pytest.raises(BackupError):
        restore(config, "/chemin/inexistant.synbk")


def test_restore_not_a_backup_file(fx, config):
    fx.server.stop()
    bogus = os.path.join(config.backup_dir, "bogus.synbk")
    os.makedirs(config.backup_dir, exist_ok=True)
    with open(bogus, "wb") as fh:
        fh.write(b"pas une sauvegarde")
    with pytest.raises(BackupError):
        restore(config, bogus)


def test_backup_includes_cursor_key(fx, config):
    """The cursor signing key is restored: cursors issued before the backup
    remain valid after restore."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-bk-4")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "deux", "cmid-bk-5")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "trois", "cmid-bk-6")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "quatre", "cmid-bk-7")
    page1 = fx.client.get_messages(BOB, BOB_PASSWORD, limit=2)
    cursor = page1["next_cursor"]
    path = backup(config)
    fx.server.stop()
    restore(config, path)
    from .conftest import make_server
    server2 = make_server(config, org=False)
    try:
        page2 = server2.client.get_messages(BOB, BOB_PASSWORD, limit=2, cursor=cursor)
        assert len(page2["messages"]) == 2
    finally:
        server2.stop()


def test_verify_detects_missing_table(fx, config, tmp_path):
    """M12: verify must check the SCHEMA, not only PRAGMA
    integrity_check — a backup whose structure is not a working Synapse
    installation (a table missing) used to pass as 'ok'."""
    from synapse.backup import _check_schema, _EXPECTED_TABLES
    import sqlite3

    # Build a SQLite file with the full schema minus one table
    # (delegations), matching the _EXPECTED_TABLES contract.
    db_path = tmp_path / "broken.db"
    conn = sqlite3.connect(db_path)
    try:
        for name in sorted(_EXPECTED_TABLES - {"delegations"}):
            conn.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(BackupError) as exc:
        _check_schema(str(db_path))
    assert "missing table(s)" in str(exc.value)
    assert "delegations" in str(exc.value)

    # A full-schema database passes.
    db_full = tmp_path / "full.db"
    conn = sqlite3.connect(db_full)
    try:
        for name in sorted(_EXPECTED_TABLES):
            conn.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    _check_schema(str(db_full))


def test_header_does_not_create_key_on_read(fx, config, tmp_path):
    """m9: reading a backup header (backup list / status) must never
    CREATE the backup key — load_or_create_key provisioned the key
    vault as a side effect of a simple read."""
    from synapse.cli.backup import _header
    from synapse.backup import _MAGIC
    from synapse.config import Config
    from pathlib import Path

    # an isolated config whose key has never been provisioned
    base = tmp_path / "isolated"
    (base / "data").mkdir(parents=True)
    (base / "backups").mkdir()
    iso_config = Config.from_dict({
        "storage_dir": str(base / "data"),
        "socket_path": str(base / "run.sock"),
        "log_dir": str(base / "logs"),
        "backup_dir": str(base / "backups"),
    })
    key_path = Path(iso_config.backup_key_path)
    archive = Path(iso_config.backup_dir) / "sample.synbk"
    # valid magic (passes the format check) but truncated ciphertext
    # (fails at decryption, AFTER the key lookup on the old code)
    archive.write_bytes(_MAGIC + b"\x00" * 16)

    header = _header(iso_config, archive)
    assert header is None
    assert not key_path.exists(), "reading a header must not create the key"
