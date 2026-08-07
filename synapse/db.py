"""SQLite access: connection, schema, transactions and permissions.

Le stockage est un fichier SQLite en mode WAL, transactionnel, dans un
``0700`` directory owned by the service system account. All
writes go through ``BEGIN IMMEDIATE`` (writer serialization);
multi-proof reads go through ``BEGIN`` (consistent snapshot).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    organization_name       TEXT PRIMARY KEY,
    password_hash           TEXT NOT NULL,
    allow_incoming_external INTEGER NOT NULL DEFAULT 0,
    allow_outgoing_external INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    enabled                 INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS accounts (
    username            TEXT PRIMARY KEY,
    password_hash       TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    description         TEXT NOT NULL DEFAULT '',
    organization_name   TEXT NOT NULL REFERENCES organizations(organization_name),
    can_see_org_agents  INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    key            TEXT NOT NULL UNIQUE,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id         TEXT PRIMARY KEY,
    conversation_id    TEXT NOT NULL REFERENCES conversations(conversation_id),
    client_message_id  TEXT NOT NULL,
    sender_username    TEXT NOT NULL REFERENCES accounts(username),
    recipient_username TEXT NOT NULL REFERENCES accounts(username),
    content            TEXT NOT NULL,
    business_reference TEXT,
    created_at         TEXT NOT NULL,
    read_at            TEXT,
    UNIQUE (sender_username, client_message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_recipient
    ON messages(recipient_username, created_at DESC, message_id DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at ASC, message_id ASC);
CREATE INDEX IF NOT EXISTS idx_messages_sender
    ON messages(sender_username, created_at DESC, message_id DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conv_recipient
    ON messages(conversation_id, recipient_username, created_at);

CREATE TABLE IF NOT EXISTS reply_state (
    conversation_id        TEXT NOT NULL REFERENCES conversations(conversation_id),
    username               TEXT NOT NULL REFERENCES accounts(username),
    no_reply_for_message_id TEXT,
    no_reply_marked_at     TEXT,
    PRIMARY KEY (conversation_id, username)
);

CREATE TABLE IF NOT EXISTS auth_failures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_failures_user
    ON auth_failures(username, attempted_at);
"""

# Tables of the v3 evolutions (SPEC.txt). Created by ``ensure_storage``
# (run once per process, idempotent via IF NOT EXISTS): an existing
# database receives the new tables at its first opening by a recent
# version. Columns added to existing tables stay
# managed by ``_migrate``.
EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_cards (
    username          TEXT PRIMARY KEY REFERENCES accounts(username),
    capabilities      TEXT NOT NULL,
    domain            TEXT,
    model             TEXT,
    tools             TEXT,
    sla               TEXT,
    limits            TEXT,
    estimated_cost    TEXT,
    validation_state  TEXT NOT NULL DEFAULT 'pending'
                      CHECK (validation_state IN ('pending', 'approved')),
    approved_by       TEXT,
    approved_at       TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    client_task_id     TEXT,
    title              TEXT NOT NULL,
    description        TEXT,
    creator_username   TEXT NOT NULL REFERENCES accounts(username),
    assignee_username  TEXT NOT NULL REFERENCES accounts(username),
    state              TEXT NOT NULL DEFAULT 'submitted'
                       CHECK (state IN ('submitted', 'in_progress', 'completed',
                                        'failed', 'canceled', 'pending_approval')),
    priority           TEXT NOT NULL DEFAULT 'normal'
                       CHECK (priority IN ('low', 'normal', 'high')),
    due_at             TEXT,
    business_reference TEXT,
    result             TEXT,
    approver_username  TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (creator_username, client_task_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee_state
    ON tasks(assignee_username, state, due_at, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_creator
    ON tasks(creator_username, created_at);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id            TEXT NOT NULL REFERENCES tasks(task_id),
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(task_id),
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS task_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL REFERENCES tasks(task_id),
    event        TEXT NOT NULL,
    by_username  TEXT NOT NULL,
    note         TEXT,
    at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, id);

CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    principal    TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    ref_id       TEXT,
    by_username  TEXT,
    at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_principal ON events(principal, seq);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);

CREATE TABLE IF NOT EXISTS org_settings (
    organization_name     TEXT PRIMARY KEY REFERENCES organizations(organization_name),
    event_retention_days  INTEGER NOT NULL DEFAULT 90
);

CREATE TABLE IF NOT EXISTS org_escalation_policy (
    organization_name        TEXT PRIMARY KEY REFERENCES organizations(organization_name),
    enabled                  INTEGER NOT NULL DEFAULT 0,
    due_after_seconds        INTEGER NOT NULL DEFAULT 3600,
    failed_after_seconds     INTEGER NOT NULL DEFAULT 3600,
    escalate_to_username     TEXT NOT NULL REFERENCES accounts(username)
);

CREATE TABLE IF NOT EXISTS agent_budgets (
    username             TEXT PRIMARY KEY REFERENCES accounts(username),
    max_active_tasks     INTEGER,
    max_messages_per_hour INTEGER
);

CREATE TABLE IF NOT EXISTS audit_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_name TEXT NOT NULL,
    at                TEXT NOT NULL,
    actor_username    TEXT NOT NULL,
    command           TEXT NOT NULL,
    target_type       TEXT,
    target_username   TEXT,
    outcome           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(organization_name, id);

CREATE TABLE IF NOT EXISTS departments (
    organization_name TEXT NOT NULL REFERENCES organizations(organization_name),
    department_name   TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (organization_name, department_name)
);

CREATE TABLE IF NOT EXISTS memberships (
    username          TEXT PRIMARY KEY REFERENCES accounts(username),
    organization_name TEXT NOT NULL REFERENCES organizations(organization_name),
    department_name   TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN ('manager', 'employee', 'rh')),
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    group_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL REFERENCES accounts(username),
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_groups_created ON groups(created_at, group_id);

CREATE TABLE IF NOT EXISTS group_members (
    group_id   TEXT NOT NULL REFERENCES groups(group_id),
    username   TEXT NOT NULL REFERENCES accounts(username),
    added_by   TEXT NOT NULL REFERENCES accounts(username),
    added_at   TEXT NOT NULL,
    PRIMARY KEY (group_id, username)
);
CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(username, group_id);

CREATE TABLE IF NOT EXISTS group_messages (
    message_id        TEXT PRIMARY KEY,
    group_id          TEXT NOT NULL REFERENCES groups(group_id),
    client_message_id TEXT,
    sender_username   TEXT NOT NULL REFERENCES accounts(username),
    content           TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE (sender_username, client_message_id)
);
CREATE INDEX IF NOT EXISTS idx_group_messages_group
    ON group_messages(group_id, created_at DESC, message_id DESC);

CREATE TABLE IF NOT EXISTS delegations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    delegator_username  TEXT NOT NULL REFERENCES accounts(username),
    delegatee_username  TEXT NOT NULL REFERENCES accounts(username),
    task_id             TEXT NOT NULL REFERENCES tasks(task_id),
    expires_at          TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delegations_delegatee
    ON delegations(delegatee_username, task_id, expires_at);
"""


class StorageError(Exception):
    """Storage-level error (inaccessible file, corrupted schema...)."""


# Database paths whose schema has already been verified and migrated in this
# process. Verification (``PRAGMA table_info``, ``sqlite_master``)
# and migration cost ~1 ms per call and only matter at
# first open: the schema is stable afterwards. In production, the
# service lock guarantees a single writer process;
# offline tools (backup, restore, installation) open the database
# first and perform the full check at that moment.
_verified_schema: set[str] = set()


def ensure_storage(config: Config) -> None:
    """Creates the storage directory (0700) and the schema if needed.

    The verification/migration work is only done once
    per process and per database (memorized path): each service
    request opens a connection, and repeating these checks every time
    would be a cost without benefit.
    """
    db_path = str(config.db_path)
    if db_path in _verified_schema:
        return
    storage = Path(config.storage_dir)
    try:
        storage.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(storage, 0o700)
    except OSError as exc:
        raise StorageError(f"Storage directory inaccessible: {exc}") from exc
    with _open_connection(config) as conn:
        if _schema_missing(conn):
            conn.executescript(SCHEMA)
        conn.executescript(EXTRA_SCHEMA)  # idempotent (IF NOT EXISTS)
        _migrate(conn)
        # Mode WAL (persistant dans le fichier) et permissions du fichier :
        # applied only once, at the first opening of the process.
        # Subsequent connections do not need to re-declare them
        # (measured: ~200 µs saved per connection).
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
    _verified_schema.add(db_path)


def _migrate(conn: sqlite3.Connection) -> None:
    """Migrates a database created by an earlier schema version.

    Deux niveaux de migration, idempotents :

    * v1 -> v2: an API v1 database (admin role, accounts without
      organization) is converted: the ``organizations`` table is created,
      the first ``admin`` account becomes an organization (same hash, name
      = its username), and all accounts are attached to this
      organisation.
    * ajout de colonnes manquantes (``description``, ``organization_name``,
      ``can_see_org_agents``) without touching existing data.
    """
    # colonnes manquantes
    columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    if "description" not in columns:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN description TEXT NOT NULL DEFAULT ''"
        )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    if "organization_name" not in columns:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN organization_name TEXT NOT NULL DEFAULT ''"
        )
    if "can_see_org_agents" not in columns:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN can_see_org_agents INTEGER NOT NULL DEFAULT 0"
        )
    if "principal_type" not in columns:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN principal_type TEXT NOT NULL DEFAULT 'agent'"
        )
    if "is_observer" not in columns:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN is_observer INTEGER NOT NULL DEFAULT 0"
        )
    # business_reference column (message metadata, v3)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "messages" in tables:
        msg_columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "business_reference" not in msg_columns:
            conn.execute("ALTER TABLE messages ADD COLUMN business_reference TEXT")
    # v1 role column (no DEFAULT, NOT NULL): the v2 model no longer knows
    # roles; the column would block any v2 insert -> drop it,
    # after the organization conversion (which still reads `role`).
    columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    has_role = "role" in columns
    # table organizations absente -> conversion v1 vers v2
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "organizations" not in tables:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS organizations (\n"
            "    organization_name       TEXT PRIMARY KEY,\n"
            "    password_hash           TEXT NOT NULL,\n"
            "    allow_incoming_external INTEGER NOT NULL DEFAULT 0,\n"
            "    allow_outgoing_external INTEGER NOT NULL DEFAULT 0,\n"
            "    created_at              TEXT NOT NULL\n"
            ");"
        )
        # the first v1 admin account becomes an organization (same hash,
        # same password: administrative access is preserved)
        conn.execute(
            "INSERT INTO organizations (organization_name, password_hash, created_at) "
            "SELECT username, password_hash, created_at FROM accounts "
            "WHERE role = 'admin' ORDER BY created_at LIMIT 1"
        )
        # v1 database without admin account: synthetic organization (FK consistency)
        if conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO organizations (organization_name, password_hash, created_at) "
                "SELECT 'synapse', '', '1970-01-01T00:00:00.000Z' "
                "WHERE EXISTS (SELECT 1 FROM accounts)"
            )
        # rattachement : tous les comptes sans organisation rejoignent
        # the only created organization (an organization now exists
        # as soon as an account exists)
        conn.execute(
            "UPDATE accounts SET organization_name = "
            "(SELECT organization_name FROM organizations ORDER BY created_at LIMIT 1) "
            "WHERE organization_name = ''"
        )
    if has_role:
        conn.execute("ALTER TABLE accounts DROP COLUMN role")
    # enabled column (SPEC-WEB: reversible deactivation of organizations)
    org_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(organizations)")
    }
    if "enabled" not in org_columns:
        conn.execute(
            "ALTER TABLE organizations ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
        )
    # missing human account (SPEC-WEB §5): each organization has an
    # auto-created human (web access). The password is delegated to the
    # organization's (never copied): the stored hash is an empty marker.
    _backfill_humans(conn)


def _backfill_humans(conn: sqlite3.Connection) -> None:
    """Creates the missing human account of each organization (idempotent)."""
    from .security import human_password_sentinel
    from .validation import human_username_for, now_utc

    orgs = conn.execute("SELECT organization_name FROM organizations").fetchall()
    for row in orgs:
        org_name = row["organization_name"]
        human = human_username_for(org_name)
        existing = conn.execute(
            "SELECT 1 FROM accounts WHERE username = ?", (human,)
        ).fetchone()
        if existing is None:
            try:
                conn.execute(
                    "INSERT INTO accounts (username, password_hash, status, "
                    "description, organization_name, can_see_org_agents, "
                    "created_at, principal_type) "
                    "VALUES (?, ?, 'active', ?, ?, 1, ?, 'human')",
                    (
                        human,
                        human_password_sentinel(),
                        f"Human account of the organization {org_name} (web access)",
                        org_name,
                        now_utc(),
                    ),
                )
            except sqlite3.IntegrityError:
                # residual name collision (historical case): the org has
                # no human, the web will refuse it at login — without
                # ever overwriting an existing account.
                continue


def _schema_missing(conn: sqlite3.Connection) -> bool:
    """True if the schema is not created yet (avoids replaying the DDL at
    chaque connexion)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'accounts'"
    ).fetchone()
    return row is None


def connect(config: Config) -> sqlite3.Connection:
    """Returns a SQLite connection, reused by the calling thread.

    Opening a connection (first file access + PRAGMA per
    connection) costs a measured ~1.2-1.7 ms, about 60% of the service
    time of a read. The server handles each request in a thread
    of a fixed pool: each thread keeps its connection (never shared
    between threads, hence no synchronization needed) and resets it
    before each reuse. Offline tools (installation,
    backup, restore) and tests use the main thread:
    their connection is also reused in that thread.

    The semantics stay as before: a usable, closable connection
    explicitly (``conn.close()`` is honored; a closed connection is
    replaced at the next request), transactional via
    ``begin_immediate`` / ``begin_read``.
    """
    ensure_storage(config)
    return _thread_connections.get(config)


def _open_connection(config: Config) -> sqlite3.Connection:
    conn = sqlite3.connect(
        config.db_path,
        timeout=config.db_busy_timeout_ms / 1000.0,
        isolation_level=None,  # transactions managed explicitly
    )
    conn.row_factory = sqlite3.Row
    # journal_mode=WAL and file chmod are applied only once per
    # processus dans ensure_storage (le mode WAL est persistant dans le
    # fichier) ; les pragmas suivants sont par connexion.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    # Memory-mapped reads and in-memory temp tables: measured read
    # optimizations (docs/PERFORMANCE.md §13) — without any semantic
    # change (mmap_size bounds addressing, not residency; temp_store
    # ne concerne que les objets temporaires de tri).
    conn.execute("PRAGMA mmap_size=67108864")
    conn.execute("PRAGMA temp_store=MEMORY")
    # trusted_schema=OFF: the schema (views, triggers, column
    # expressions) is no longer executed unchecked — defense in
    # depth against an altered database file (the 0600 permissions
    # remain the main barrier). The Synapse schema uses neither
    # view nor trigger: no functional impact.
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.execute(f"PRAGMA busy_timeout={int(config.db_busy_timeout_ms)}")
    return conn


class _ConnectionState:
    """Pool state for a thread: the current connection and its database."""

    __slots__ = ("path", "conn")

    def __init__(self) -> None:
        self.path: str | None = None
        self.conn: sqlite3.Connection | None = None


class _ThreadLocalConnections:
    """SQLite connection reused by worker thread.

    Each thread keeps at most ONE connection (the one of the last
    used database). The server handles each request in a pool thread
    fixe et n'only usesune base par processus : 64 connexions ouvertes au
    maximum, regardless of throughput. A thread switching databases (tests,
    offline tools) closes the previous connection before opening the
    new one: the descriptor count stays bounded, no connection
    leaks from one test to the next.

    Before reuse, the connection is reset: no
    residual transaction may survive from one request to the next. A
    connection explicitly closed by a caller (``conn.close()``) is
    detected at reset (accessing ``in_transaction`` raises
    ``ProgrammingError``) and replaced.
    """

    def __init__(self) -> None:
        self._local: threading.local = threading.local()

    def get(self, config: Config) -> sqlite3.Connection:
        state = getattr(self._local, "state", None)
        if state is None:
            state = self._local.state = _ConnectionState()
        if state.path != config.db_path:
            old = state.conn
            if old is not None:
                try:
                    old.close()
                except sqlite3.Error:  # pragma: no cover - close est idempotent
                    pass
            conn = _open_connection(config)
            state.path = config.db_path
            state.conn = conn
            return conn
        conn = state.conn
        if conn is None:  # pragma: no cover - invariant : path et conn vont de pair
            conn = _open_connection(config)
            state.conn = conn
            return conn
        try:
            if conn.in_transaction:
                # Defensive reset: a transaction left open
                # by a previous request (unexpected error path)
                # ne doit pas polluer la suivante.
                conn.execute("ROLLBACK")
        except sqlite3.ProgrammingError:
            # Connection closed by a caller: replace it.
            conn = _open_connection(config)
            state.conn = conn
        return conn


_thread_connections = _ThreadLocalConnections()

# Application-level write serialization: a single writer at a time, to
# structurally eliminate the SQLite WAL-reset race (see
# ``begin_immediate``). RLock: reentrant, nesting-insensitive.
_WRITE_LOCK = threading.RLock()


@contextmanager
def begin_immediate(conn: sqlite3.Connection):
    """Serialized write transaction (BEGIN IMMEDIATE).

    A global application lock guarantees a single writer at a
    fois : la course WAL-reset de SQLite (deux connexions sur des threads
    separate processes writing or checkpointing at the same instant, on
    uncorrected < 3.51.3 versions) becomes structurally impossible.
    Writes were already serialized by SQLite itself (BEGIN IMMEDIATE
    blocks other writers): the lock only moves the wait to the
    applicatif, sans impact sur les lecteurs (qui n'utilisent pas ce chemin
    and never trigger a checkpoint). RLock (reentrant): insensitive
    to any future transaction nesting.
    """
    with _WRITE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise


@contextmanager
def begin_read(conn: sqlite3.Connection):
    """Read transaction with a consistent snapshot (BEGIN)."""
    conn.execute("BEGIN")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
