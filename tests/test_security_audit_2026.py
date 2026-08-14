"""Security audit 2026-08-05 — regression tests for the fixes.

Each test corresponds to a vulnerability demonstrated by a PoC and then
fixed. File name: test_security_audit_2026.py.

* FUITE-1 (HIGH): ``list_department_tasks`` was scoped by department name
  without an organization filter: the manager of a department with the
  same name in another organization could see its tasks (title, assignee,
  state). Fixed: scope limited to one's own organization.
* QUOTA-1 (MEDIUM): the message budget (F9) only applied to direct
  messages; ``send_group_message`` bypassed it. Fixed: direct + group
  messages count toward the same hourly quota.
* GROUPE-1 (LOW): any group member could remove any member (including the
  creator). Fixed: only the creator removes others; self-removal remains
  possible.
* WEB-1 / A2A-1 (MEDIUM): the web interface (F18) and the A2A bridge
  (F20) were accessible to any local process without authentication.
  Fixed: access reserved to the service's system account (SO_PEERCRED,
  fail-closed).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from synapse.client import ApiClientError

from .conftest import (
    ORG_NAME,
    ORG_PASSWORD,
    ORG2_NAME,
    ORG2_PASSWORD,
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    create_organization,
)

ACCESS_DENIED = "ACCESS_DENIED"
GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
POLICY_DENIED = "POLICY_DENIED"
QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


@pytest.fixture()
def two_orgs(fx):
    """root_org (alice, bob) + second_org (dave, erin)."""
    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("dave", "motdepasse-dave-1", "Agent dave", ORG2_NAME, ORG2_PASSWORD)
    fx.client.create_agent("erin", "motdepasse-erin-1", "Agent erin", ORG2_NAME, ORG2_PASSWORD)
    return fx


# ---------------------------------------------------------------------------
# FUITE-1: list_department_tasks must stay within the manager's organization
# ---------------------------------------------------------------------------


def test_department_tasks_scoped_to_own_organization(two_orgs):
    """A department with the same name in another organization does not reveal
    its tasks to the first organization's manager (isolation regression)."""
    # Departments with the same name in both organizations.
    two_orgs.client.create_department("engineering", ORG_NAME, ORG_PASSWORD)
    two_orgs.client.create_department("engineering", ORG2_NAME, ORG2_PASSWORD)
    two_orgs.client.set_agent_department(ALICE, "engineering", "manager", ORG_NAME, ORG_PASSWORD)
    two_orgs.client.set_agent_department(BOB, "engineering", "employee", ORG_NAME, ORG_PASSWORD)
    two_orgs.client.set_agent_department("dave", "engineering", "manager", ORG2_NAME, ORG2_PASSWORD)
    two_orgs.client.set_agent_department("erin", "engineering", "employee", ORG2_NAME, ORG2_PASSWORD)

    # Sensitive task in org 2, assigned to erin.
    two_orgs.client.create_task("projet secret X-42", "erin", "dave", "motdepasse-dave-1",
                                description="confidentiel org2")

    # The org 1 manager must see no org 2 tasks.
    seen = two_orgs.client.list_department_tasks("engineering", ALICE, ALICE_PASSWORD)
    assert all("secret X-42" not in t["title"] for t in seen["tasks"])

    # The org 2 manager does see his department's task.
    seen2 = two_orgs.client.list_department_tasks("engineering", "dave", "motdepasse-dave-1")
    assert any("secret X-42" in t["title"] for t in seen2["tasks"])


# ---------------------------------------------------------------------------
# QUOTA-1: the message budget covers direct and group messages
# ---------------------------------------------------------------------------


def test_group_message_budget_counts_towards_quota(fx):
    """send_group_message consumes the hourly message quota (F9)."""
    fx.client.set_agent_budget(ALICE, ORG_NAME, ORG_PASSWORD, max_messages_per_hour=2)
    gid = fx.client.create_group("canal", ALICE, ALICE_PASSWORD)["group_id"]
    fx.client.add_group_member(gid, BOB, ALICE, ALICE_PASSWORD)

    fx.client.send_group_message(gid, "m1", ALICE, ALICE_PASSWORD, client_message_id="g1")
    fx.client.send_group_message(gid, "m2", ALICE, ALICE_PASSWORD, client_message_id="g2")

    with pytest.raises(ApiClientError) as exc:
        fx.client.send_group_message(gid, "m3", ALICE, ALICE_PASSWORD, client_message_id="g3")
    assert exc.value.code == QUOTA_EXCEEDED

    # Idempotent retrieval of an already-validated message stays prioritized.
    fx.client.send_group_message(gid, "m1", ALICE, ALICE_PASSWORD, client_message_id="g1")

    # Direct messages consume the same quota.
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message(BOB, "direct", "d1", ALICE, ALICE_PASSWORD)
    assert exc.value.code == QUOTA_EXCEEDED


def test_direct_message_quota_still_blocks(fx):
    """The existing direct quota is not regressed."""
    fx.client.set_agent_budget(ALICE, ORG_NAME, ORG_PASSWORD, max_messages_per_hour=1)
    fx.client.send_message(BOB, "un", "u1", ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.send_message(BOB, "deux", "u2", ALICE, ALICE_PASSWORD)
    assert exc.value.code == QUOTA_EXCEEDED


# ---------------------------------------------------------------------------
# GROUPE-1: only the creator can remove other members
# ---------------------------------------------------------------------------


def test_only_creator_can_remove_other_members(fx):
    gid = fx.client.create_group("g", ALICE, ALICE_PASSWORD)["group_id"]
    fx.client.add_group_member(gid, BOB, ALICE, ALICE_PASSWORD)

    # A non-creator member cannot remove the creator.
    with pytest.raises(ApiClientError) as exc:
        fx.client.remove_group_member(gid, ALICE, BOB, BOB_PASSWORD)
    assert exc.value.code == ACCESS_DENIED

    # The creator removes another member: OK.
    fx.client.remove_group_member(gid, BOB, ALICE, ALICE_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_group_members(gid, BOB, BOB_PASSWORD)
    assert exc.value.code == GROUP_NOT_FOUND


def test_member_can_leave_group_himself(fx):
    gid = fx.client.create_group("g", ALICE, ALICE_PASSWORD)["group_id"]
    fx.client.add_group_member(gid, BOB, ALICE, ALICE_PASSWORD)

    # Self-removal remains possible (F15: "a participant leaves the group").
    fx.client.remove_group_member(gid, BOB, BOB, BOB_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_group_members(gid, BOB, BOB_PASSWORD)
    assert exc.value.code == GROUP_NOT_FOUND
    # The creator, for his part, is still a member.
    members = fx.client.get_group_members(gid, ALICE, ALICE_PASSWORD)["members"]
    assert ALICE in members


# ---------------------------------------------------------------------------
# GROUPE-2: groups do not bypass communication policies
# ---------------------------------------------------------------------------


def test_group_add_external_member_respects_policies(two_orgs):
    """Adding a member of a closed organization is refused (POLICY_DENIED):
    a group does not neutralize policy isolation (SPEC.txt §6.2)."""
    two_orgs.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    # second_org stays closed (allow_incoming_external=false by default).
    gid = two_orgs.client.create_group("g", ALICE, ALICE_PASSWORD)["group_id"]
    with pytest.raises(ApiClientError) as exc:
        two_orgs.client.add_group_member(gid, "dave", ALICE, ALICE_PASSWORD)
    assert exc.value.code == POLICY_DENIED
    # Opening org 2: the addition becomes possible.
    two_orgs.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    two_orgs.client.add_group_member(gid, "dave", ALICE, ALICE_PASSWORD)


def test_group_message_to_external_respects_policies(two_orgs):
    """Group send to an external member follows the policies; closing
    an organization blocks new sends but not the idempotent retrieval of
    already-validated messages."""
    two_orgs.client.set_organization_policy(True, True, ORG_NAME, ORG_PASSWORD)
    two_orgs.client.set_organization_policy(True, True, ORG2_NAME, ORG2_PASSWORD)
    gid = two_orgs.client.create_group("g", ALICE, ALICE_PASSWORD)["group_id"]
    two_orgs.client.add_group_member(gid, "dave", ALICE, ALICE_PASSWORD)
    two_orgs.client.send_group_message(gid, "bonjour", ALICE, ALICE_PASSWORD,
                                       client_message_id="gm1")
    # Closing org 2 (incoming forbidden): new send refused...
    two_orgs.client.set_organization_policy(False, False, ORG2_NAME, ORG2_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        two_orgs.client.send_group_message(gid, "re-bonjour", ALICE, ALICE_PASSWORD,
                                           client_message_id="gm2")
    assert exc.value.code == POLICY_DENIED
    # ... but the already-validated message remains retrievable (idempotency).
    two_orgs.client.send_group_message(gid, "bonjour", ALICE, ALICE_PASSWORD,
                                       client_message_id="gm1")


# ---------------------------------------------------------------------------
# WEB-1 / A2A-1: local interfaces reserved for the service account
# ---------------------------------------------------------------------------


def _start_web(fx):
    """Web interface WITHOUT a secret (SPEC-WEB D6.4): the gate is the login
    by organization + password, never a static token."""
    from synapse.web import SynapseWebUI

    web = SynapseWebUI(fx.config, port=0)
    web.start()
    assert web._server is not None
    return web, web._server.server_address[1]


def test_web_ui_login_allows_data(fx):
    """Positive witness: with a session (org login + password), the
    snapshot is accessible (200) — no token is requested."""
    web, port = _start_web(fx)
    try:
        from .web_helpers import authed, json_get
        opener = authed(port)
        data = json_get(port, "/api/snapshot", opener)
        assert data["organization_name"] == ORG_NAME
        # no X-Synapse-Token header is accepted/required: the session
        # is carried by the HttpOnly cookie.
        assert not any(c.name == "synapse_session" and c.value == ""
                       for c in opener.cookiejar)
    finally:
        web.stop()


def test_web_ui_requires_session(fx):
    """The session is mandatory on data (/api/*), not on the shell.

    The HTML/CSS/JS shell is public — it contains no organization data
    (everything is loaded via /api/*) and the interface must be able to
    load to show the login screen. Without a session: 401 on /api/*,
    200 on the shell; an expired session or a wrong password: 401
    everywhere on data."""
    web, port = _start_web(fx)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=5)
        assert exc.value.code == 401
        # Unknown organization: same refusal (the session is not created).
        from .web_helpers import login
        _, status = login(port, org="org_inexistante")
        assert status == 401
        # The shell loads without a session (login screen).
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
        assert "<!DOCTYPE html>" in html
        # Without a session, no /api/* route responds (the shell exposes nothing).
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/org", timeout=5)
        assert exc.value.code == 401
    finally:
        web.stop()


def test_a2a_bridge_requires_token(fx):
    """The A2A bridge refuses requests without a token (401)."""
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(fx.config, ALICE, ALICE_PASSWORD, port=0,
                       token="jeton-de-test-a2a-123456")
    bridge.start()
    port = bridge._server.server_address[1]
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tasks/list",
                           "params": {}}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/message", data=body,
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 401
    finally:
        bridge.stop()


def test_web_ui_starts_without_secret(fx):
    """SPEC-WEB D5: the interface starts WITHOUT any secret (neither observer
    nor static token) — the gate is the login screen (org + password),
    and without a session the data stays inaccessible (401)."""
    from synapse.web import SynapseWebUI

    web = SynapseWebUI(fx.config, port=0)
    web.start()
    try:
        port = web._server.server_address[1]
        assert web._sessions == {}
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=5)
        assert exc.value.code == 401
    finally:
        web.stop()


# ---------------------------------------------------------------------------
# DB-1: SQLite hardening (trusted_schema=OFF)
# ---------------------------------------------------------------------------


def test_connection_trusted_schema_off(fx):
    """Every SQLite connection of the service disables trusted_schema
    (defense in depth against an altered database file)."""
    from synapse import db

    with db.connect(fx.config) as conn:
        row = conn.execute("PRAGMA trusted_schema").fetchone()
    assert int(row[0]) == 0
