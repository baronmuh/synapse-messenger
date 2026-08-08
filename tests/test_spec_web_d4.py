"""SPEC-WEB D4 tests — Human account.

The human account is created automatically with its organization (install.py
and create_org) or by backfill at migration; its password is delegated
to the organization's (never copied — valid argon2 sentinel hash);
an organization password rotation applies immediately to the human.
The human is a directory account (snapshot) but does not appear
in list_org_agents (the agents directory).
"""

from __future__ import annotations

import sqlite3

import pytest

from synapse.client import ApiClientError
from synapse.install import create_organization
from tests.conftest import (
    ALICE,
    ALICE_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
    config,
    make_server,
)

HUMAN = f"{ORG_NAME}_humain"


# ---------------------------------------------------------------------------
# Auto-creation (install.py + backfill)
# ---------------------------------------------------------------------------


def test_d4_install_creates_human(config):
    """create_organization (install) creates the org AND its human account."""
    org = create_organization(config, "org_init", "motdepasse-org-init-1",
                              "motdepasse-org-init-1")
    assert org == "org_init"
    fx = make_server(config, org=False)  # server on the same database
    try:
        info = fx.client.get_my_organization("org_init_humain", "motdepasse-org-init-1")
        assert info["organization_name"] == "org_init"
    finally:
        fx.stop()


def test_d4_backfill_existing_orgs():
    """An organization created before the feature (without a human) gets
    its human account at the next startup (idempotent backfill). The
    backfill runs at the first ensure_storage of each process:
    a server restart is simulated with a second process."""
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        conf_dict = {
            "storage_dir": str(Path(tmp) / "data"),
            "socket_path": str(Path(tmp) / "run" / "s.sock"),
            "log_dir": str(Path(tmp) / "logs"),
            "backup_dir": str(Path(tmp) / "bk"),
        }
        conf_json = json.dumps(conf_dict)
        repo = str(Path(__file__).resolve().parents[1])
        # Process 1: "legacy" database — organization without a human account
        subprocess.run([
            sys.executable, "-c",
            f"""import json
from synapse import db
from synapse.config import Config
conf = Config.from_dict(json.loads('{conf_json}'))
with db.connect(conf) as conn:
    with db.begin_immediate(conn):
        conn.execute("INSERT INTO organizations (organization_name, "
                     "password_hash, allow_incoming_external, "
                     "allow_outgoing_external, created_at, enabled) "
                     "VALUES ('vieille_org', 'x', 0, 0, "
                     "'2026-01-01T00:00:00.000Z', 1)")
""",
        ], check=True, cwd=repo)
        # Process 2 (restart): ensure_storage -> backfill -> human
        subprocess.run([
            sys.executable, "-c",
            f"""import json
from synapse import db
from synapse.config import Config
conf = Config.from_dict(json.loads('{conf_json}'))
with db.connect(conf) as conn:
    rows = conn.execute("SELECT username, principal_type FROM accounts "
                        "WHERE organization_name = 'vieille_org'").fetchall()
    humans = [r for r in rows if r[1] == 'human']
    assert len(humans) == 1, rows
    assert humans[0][0] == 'vieille_org_humain'
""",
        ], check=True, cwd=repo)
        # Process 3: idempotency — nothing new
        subprocess.run([
            sys.executable, "-c",
            f"""import json
from synapse import db
from synapse.config import Config
conf = Config.from_dict(json.loads('{conf_json}'))
with db.connect(conf) as conn:
    rows = conn.execute("SELECT username, principal_type FROM accounts "
                        "WHERE organization_name = 'vieille_org'").fetchall()
    assert len(rows) == 1, rows
""",
        ], check=True, cwd=repo)


# ---------------------------------------------------------------------------
# Password delegation
# ---------------------------------------------------------------------------


def test_d4_stored_hash_is_sentinel(fx):
    """The hash stored on the human account is a valid argon2 sentinel,
    never a copy of the organization's hash."""
    conn = sqlite3.connect(fx.config.db_path)
    try:
        human_hash = conn.execute(
            "SELECT password_hash FROM accounts WHERE username = ?", (HUMAN,)
        ).fetchone()[0]
        org_hash = conn.execute(
            "SELECT password_hash FROM organizations WHERE organization_name = ?",
            (ORG_NAME,)).fetchone()[0]
    finally:
        conn.close()
    assert human_hash.startswith("$argon2id$")
    assert human_hash != org_hash  # not a copy


def test_d4_password_rotation_follows_org(fx):
    """An organization password rotation applies immediately
    to the human (live delegation, nothing to synchronize)."""
    new_password = "nouveau-motdepasse-org-1"
    fx.client.change_organization_password(new_password, ORG_NAME, ORG_PASSWORD)
    # the old password no longer works for the human
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_my_organization(HUMAN, ORG_PASSWORD)
    assert exc.value.code == "AUTH_FAILED"
    # the new one works (for the human as well as the org)
    assert fx.client.get_my_organization(HUMAN, new_password)["organization_name"] == ORG_NAME
    assert fx.client.get_organization_policy(ORG_NAME, new_password)["organization_name"] == ORG_NAME


def test_d4_human_visible_in_snapshot(fx):
    """The human is a directory account (snapshot) with principal_type
    'human' — the UI shows it with its badge."""
    snap = fx.client.get_org_snapshot(HUMAN, ORG_PASSWORD)
    humans = [a for a in snap["agents"] if a["principal_type"] == "human"]
    assert [a["username"] for a in humans] == [HUMAN]


def test_d4_human_excluded_from_agent_directory(fx):
    """list_org_agents is the AGENTS directory: the human is not in it
    (it remains reachable by name)."""
    fx.create_agent("can_see", ORG_PASSWORD + "x", "visibility", ORG_NAME,
                    ORG_PASSWORD, can_see_org_agents=True)
    data = fx.client.list_org_agents("can_see", ORG_PASSWORD + "x")
    assert HUMAN not in data["usernames"]
    assert ALICE in data["usernames"]


def test_d4_human_receives_messages(fx):
    """An agent can write to the human (the account is like any other
    on the messaging side)."""
    fx.send(ALICE, ALICE_PASSWORD, HUMAN, "pour l'humain", "cmid-d4-1")
    convs = fx.client.list_org_conversations(HUMAN, ORG_PASSWORD, limit=100)
    conv = next(c for c in convs["conversations"] if HUMAN in c["participants"])
    data = fx.client.get_org_conversation(conv["conversation_id"], HUMAN, ORG_PASSWORD)
    assert [m["content"] for m in data["messages"]] == ["pour l'humain"]
