"""Unit tests of the storage layer (db, store/accounts, store/messages,
store/queries)."""

from __future__ import annotations

import os
import sqlite3
import stat

import pytest

from synapse import db
from synapse.config import Config
from synapse.db import StorageError
from synapse.errors import MESSAGE_ALREADY_EXISTS, ApiError
from synapse.store import accounts, messages, organizations, queries
from synapse.validation import now_utc


def test_ensure_storage_creates_0700_dir(config):
    if os.name == "nt":
        pytest.skip("POSIX file permissions do not apply on Windows")
    db.ensure_storage(config)
    assert os.path.isdir(config.storage_dir)
    assert stat.S_IMODE(os.stat(config.storage_dir).st_mode) == 0o700
    assert os.path.exists(config.db_path)


def test_ensure_storage_idempotent(config):
    db.ensure_storage(config)
    db.ensure_storage(config)  # must not raise


def test_ensure_storage_error_when_dir_is_file(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    cfg = Config.from_dict({"storage_dir": str(blocker)})
    with pytest.raises(StorageError):
        db.ensure_storage(cfg)


def test_connect_creates_schema(config):
    conn = db.connect(config)
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"accounts", "conversations", "messages", "reply_state", "auth_failures"} <= tables
        # active pragmas
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(config.db_path).st_mode) == 0o600


def test_connect_tolerates_chmod_failure(config, monkeypatch):
    real_chmod = os.chmod

    def selective_chmod(path, mode, *args, **kwargs):
        if str(path).endswith("synapse.db"):
            raise OSError("permission denied")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", selective_chmod)
    conn = db.connect(config)  # must not raise
    conn.close()


def test_schema_missing_detection(config):
    conn = db.connect(config)
    try:
        assert db._schema_missing(conn) is False
        conn.execute("DROP TABLE accounts")
        assert db._schema_missing(conn) is True
    finally:
        conn.close()


def test_begin_immediate_commits(config):
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        with db.begin_immediate(conn):
            accounts.insert(conn, "alice", "hash", "active", "description", "root_org")
        assert accounts.get(conn, "alice") is not None
    finally:
        conn.close()


def test_begin_immediate_rolls_back_on_error(config):
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        with pytest.raises(RuntimeError):
            with db.begin_immediate(conn):
                accounts.insert(conn, "alice", "hash", "active", "description", "root_org")
                raise RuntimeError("boom")
        assert accounts.get(conn, "alice") is None  # rolled back
    finally:
        conn.close()


def test_begin_read_rolls_back_on_error(config):
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        accounts.insert(conn, "alice", "hash", "active", "description", "root_org")
        with pytest.raises(RuntimeError):
            with db.begin_read(conn):
                assert accounts.get(conn, "alice") is not None
                raise RuntimeError("boom")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# store/accounts
# ---------------------------------------------------------------------------


def test_account_to_dict(config):
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        accounts.insert(conn, "alice", "hash", "active", "description", "root_org")
        row = accounts.get(conn, "alice")
        d = accounts.account_to_dict(row)
        assert d["username"] == "alice"
        assert d["password_hash"] == "hash"
        assert d["status"] == "active"
        assert d["description"] == "description"
        assert d["organization_name"] == "root_org"
        assert d["can_see_org_agents"] is False
        assert d["created_at"].endswith("Z")
    finally:
        conn.close()


def test_account_store_helpers(config):
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        assert accounts.any_account_exists(conn) is False
        accounts.insert(conn, "agent1", "h", "active", "description", "root_org")
        accounts.insert(conn, "agent2", "h", "active", "description", "root_org")
        assert accounts.any_account_exists(conn) is True
        assert accounts.count_by_org(conn, "root_org") == 2
        accounts.set_status(conn, "agent1", "disabled")
        assert accounts.count_by_org(conn, "root_org") == 2  # the account remains
        accounts.set_password_hash(conn, "agent1", "nouveau-hash")
        assert accounts.get(conn, "agent1")["password_hash"] == "nouveau-hash"
        # visibility
        accounts.set_visibility(conn, "agent1", True)
        assert accounts.get(conn, "agent1")["can_see_org_agents"] == 1
        # list_by_org: ascending sort, active filter, bound
        rows = accounts.list_by_org(conn, "root_org", 10, active_only=True)
        assert [r["username"] for r in rows] == ["agent2"]
        rows = accounts.list_by_org(conn, "root_org", 10, after_username="agent1")
        assert [r["username"] for r in rows] == ["agent2"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# store/messages
# ---------------------------------------------------------------------------


def test_conversation_key_lexical_order():
    assert messages.conversation_key("bob", "alice") == "alice:bob"
    assert messages.conversation_key("alice", "bob") == "alice:bob"
    assert messages.conversation_key("a1", "a0") == "a0:a1"


def test_new_uuid_is_v4():
    import uuid as uuid_mod
    value = messages.new_uuid()
    assert uuid_mod.UUID(value).version == 4


def test_fetch_or_create_conversation_race_branch(config):
    """The IntegrityError branch (creation race) re-reads the existing
    conversation."""
    conn = db.connect(config)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # first create the conversation directly
        conv_id = messages.new_uuid()
        conn.execute(
            "INSERT INTO conversations (conversation_id, key, created_at) VALUES (?, ?, ?)",
            (conv_id, "alice:bob", now_utc()),
        )
        # a call with the same key returns the existing one
        result = messages.fetch_or_create_conversation(conn, "alice:bob", now_utc())
        conn.execute("COMMIT")
        assert result == conv_id
    finally:
        conn.close()


def test_raise_message_already_exists():
    with pytest.raises(ApiError) as exc:
        messages.raise_message_already_exists()
    assert exc.value.code == MESSAGE_ALREADY_EXISTS


def test_mark_read_conditional_only_first_time(config):
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        accounts.insert(conn, "alice", "h", "active", "description", "root_org")
        accounts.insert(conn, "bob", "h", "active", "description", "root_org")
        conv = messages.fetch_or_create_conversation(conn, "alice:bob", now_utc())
        messages.insert_message(
            conn, message_id="m1", conversation_id=conv,
            client_message_id="c1", sender_username="alice",
            recipient_username="bob", content="hello", created_at=now_utc(),
        )
        t1 = now_utc()
        messages.mark_read_conditional(conn, "m1", t1)
        t2 = now_utc()
        messages.mark_read_conditional(conn, "m1", t2)
        row = messages.get_message_by_id(conn, "m1")
        assert row["read_at"] == t1  # the first date is kept
    finally:
        conn.close()


def test_reply_state_upsert_and_get(config):
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        accounts.insert(conn, "alice", "h", "active", "description", "root_org")
        accounts.insert(conn, "bob", "h", "active", "description", "root_org")
        conv = messages.fetch_or_create_conversation(conn, "alice:bob", now_utc())
        messages.set_no_reply(conn, conv, "alice", "m1", now_utc())
        row = messages.get_no_reply(conn, conv, "alice")
        assert row["no_reply_for_message_id"] == "m1"
        # upsert: update
        messages.set_no_reply(conn, conv, "alice", "m2", now_utc())
        row = messages.get_no_reply(conn, conv, "alice")
        assert row["no_reply_for_message_id"] == "m2"
        # cancellation (NULL)
        messages.set_no_reply(conn, conv, "alice", None, None)
        row = messages.get_no_reply(conn, conv, "alice")
        assert row["no_reply_for_message_id"] is None
        # other participants unaffected
        assert messages.get_no_reply(conn, conv, "bob") is None
    finally:
        conn.close()


def test_row_to_message_helpers(config):
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        accounts.insert(conn, "alice", "h", "active", "description", "root_org")
        accounts.insert(conn, "bob", "h", "active", "description", "root_org")
        conv = messages.fetch_or_create_conversation(conn, "alice:bob", now_utc())
        messages.insert_message(
            conn, message_id="m1", conversation_id=conv,
            client_message_id="c1", sender_username="alice",
            recipient_username="bob", content="hello", created_at="2026-01-01T00:00:00.000Z",
        )
        row = messages.get_message_by_id(conn, "m1")
        assert messages.row_to_message(row)["status"] == "unread"
        # read after the boundary: shown unread (frozen)
        as_of = messages.row_to_message_as_of(row, "2025-01-01T00:00:00.000Z")
        assert as_of["status"] == "unread"
        # read before the boundary: shown read
        messages.mark_read_conditional(conn, "m1", "2025-06-01T00:00:00.000Z")
        row = messages.get_message_by_id(conn, "m1")
        assert messages.row_to_message_as_of(row, "2025-12-31T00:00:00.000Z")["status"] == "read"
        assert messages.row_to_message_as_of(row, "2025-01-01T00:00:00.000Z")["status"] == "unread"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# store/queries
# ---------------------------------------------------------------------------


def test_reply_status_defensive_sender_equals_username(config):
    """Rule 2 (sent by the other agent) is defensive: a message whose
    sender == the considered agent is never needs_reply."""
    conn = db.connect(config)
    organizations.insert(conn, "root_org", "h")
    try:
        accounts.insert(conn, "alice", "h", "active", "description", "root_org")
        accounts.insert(conn, "bob", "h", "active", "description", "root_org")
        conv = messages.fetch_or_create_conversation(conn, "alice:bob", now_utc())
        # message received by alice but sent by alice (impossible via the API,
        # injected directly to exercise the defensive branch)
        conn.execute(
            "INSERT INTO messages (message_id, conversation_id, client_message_id, "
            "sender_username, recipient_username, content, created_at, read_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("m-self", conv, "c-self", "alice", "alice", "x",
             "2026-01-01T00:00:00.000Z", "2026-01-02T00:00:00.000Z"),
        )
        status, last_id = queries.reply_status(conn, conv, "alice", "2026-12-31T00:00:00.000Z")
        assert status == "no_reply_needed"
        assert last_id == "m-self"
    finally:
        conn.close()


def test_migration_adds_description_column(config):
    """A database created before the description column is migrated automatically:
    the column is added, existing accounts are kept with an empty
    description, and new inserts require a description."""
    import synapse.db as db_mod

    os.makedirs(config.storage_dir, exist_ok=True)
    legacy = sqlite3.connect(config.db_path)
    legacy.execute(
        "CREATE TABLE accounts ("
        "username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, "
        "role TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO accounts VALUES ('legacy', 'h', 'agent', 'active', "
        "'2026-01-01T00:00:00.000Z')"
    )
    legacy.commit()
    legacy.close()

    # db.connect triggers the migration (incomplete schema detected)
    conn = db_mod.connect(config)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
        assert "description" in columns
        assert "organization_name" in columns
        row = conn.execute(
            "SELECT username, description, organization_name FROM accounts WHERE username='legacy'"
        ).fetchone()
        assert row["description"] == ""  # earlier account: empty description
        # no v1 admin account: the synthetic 'synapse' organization was
        # created to preserve the referential-integrity constraint
        assert row["organization_name"] == "synapse"
        org = conn.execute(
            "SELECT organization_name FROM organizations WHERE organization_name='synapse'"
        ).fetchone()
        assert org is not None
        # new inserts provide the description and the org
        accounts.insert(conn, "newbie", "hash", "active", "Description du nouveau", "synapse")
        row = conn.execute(
            "SELECT description FROM accounts WHERE username='newbie'"
        ).fetchone()
        assert row["description"] == "Description du nouveau"
        # the migration is idempotent: a second pass breaks nothing
        db_mod._migrate(conn)
    finally:
        conn.close()
