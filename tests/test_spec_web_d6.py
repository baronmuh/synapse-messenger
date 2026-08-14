"""SPEC-WEB amended D5 tests — Login by organization selection.

The login page shows the dropdown list of ACTIVE organizations
(``list_orgs`` command); the user picks an organization and clicks
"Sign in": the web authenticates with the service using the LOCAL
trust token (0600 file in the run dir, written by the server at startup)
— no more password entry. The token replaces the organization password
for the local web identity (``_web_local``) and for human accounts.

Acknowledged and documented security trade-off: on a single-user machine,
any process of the same user that reads the token file (0600) can
authenticate as the web — that is the price of removing password entry
(amended SPEC-WEB, verified in black and white in the checklist).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.client import ApiClientError
from synapse.service import WEB_TOKEN_FILENAME

from .conftest import ALICE, ALICE_PASSWORD, ORG_NAME, ORG_PASSWORD


def _web_token(fx) -> str:
    """Reads the local trust token written by the fixture's server."""
    path = Path(fx.config.socket_path).parent / WEB_TOKEN_FILENAME
    return path.read_text(encoding="ascii").strip()


def test_d6_server_writes_web_token_file(fx):
    """The server writes the token (0600) to the run dir at startup."""
    path = Path(fx.config.socket_path).parent / WEB_TOKEN_FILENAME
    token = _web_token(fx)
    assert len(token) >= 32
    assert (path.stat().st_mode & 0o777) == 0o600


def test_d6_list_orgs_via_web_local(fx):
    """The local web identity (_web_local + token) lists the active orgs."""
    data = fx.client.list_orgs("_web_local", _web_token(fx))
    assert {"organization_name": ORG_NAME} in data["organizations"]


def test_d6_list_orgs_by_human(fx):
    """A human account can list the active organizations."""
    data = fx.client.list_orgs(f"{ORG_NAME}_humain", ORG_PASSWORD)
    assert {"organization_name": ORG_NAME} in data["organizations"]


def test_d6_list_orgs_denied_to_agent(fx):
    """An agent never lists organizations (ACCESS_DENIED)."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.list_orgs(ALICE, ALICE_PASSWORD)
    assert exc.value.code == "ACCESS_DENIED"


def test_d6_list_orgs_excludes_disabled(fx):
    """Disabled organizations are excluded from the list (only what is
    reachable is offered)."""
    fx.client.create_org("org_gel_d6", "motdepasse-gel-d6-1",
                         f"{ORG_NAME}_humain", ORG_PASSWORD)
    fx.client.disable_org("org_gel_d6", "org_gel_d6_humain", "motdepasse-gel-d6-1")
    data = fx.client.list_orgs(f"{ORG_NAME}_humain", ORG_PASSWORD)
    names = {o["organization_name"] for o in data["organizations"]}
    assert "org_gel_d6" not in names
    assert ORG_NAME in names


def test_d6_web_local_cannot_do_anything_else(fx):
    """The _web_local identity has NO power other than list_orgs."""
    token = _web_token(fx)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_snapshot("_web_local", token)
    assert exc.value.code == "ACCESS_DENIED"


def test_d6_web_local_rejects_wrong_token(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.list_orgs("_web_local", "mauvais-jeton")
    assert exc.value.code == "AUTH_FAILED"


def test_d6_token_authenticates_human(fx):
    """The local token replaces the organization password for
    human accounts (the web acts on behalf of the selected human)."""
    data = fx.client.get_my_organization(f"{ORG_NAME}_humain", _web_token(fx))
    assert data["organization_name"] == ORG_NAME


def test_d6_token_authenticates_org_commands(fx):
    """The local token also replaces the organization password for
    organization commands (management from the web)."""
    token = _web_token(fx)
    result = fx.client.create_agent("agent_d6", "motdepasse-agent-d6-1",
                                    "via web", ORG_NAME, token)
    assert result["status"] == "active"
    data = fx.client.get_organization_policy(ORG_NAME, token)
    assert data["organization_name"] == ORG_NAME


def test_d6_token_never_authenticates_agents(fx):
    """The token NEVER applies to agent accounts (an agent keeps
    its password; the token does not unlock an agent account)."""
    token = _web_token(fx)
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_my_organization(ALICE, token)
    assert exc.value.code == "AUTH_FAILED"


def test_d6_human_cannot_login_without_token_or_password(fx):
    """Without token AND without organization password: refused (AUTH_FAILED).
    The web always provides the token; this test guarantees a bare call
    does not pass."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_my_organization(f"{ORG_NAME}_humain", "")
    assert exc.value.code == "AUTH_FAILED"


def test_d6_token_file_removed_on_stop(fx):
    """A clean server stop removes the token (the web can no longer
    connect to a stopped service)."""
    path = Path(fx.config.socket_path).parent / WEB_TOKEN_FILENAME
    fx.server.stop()
    assert not path.exists()


def test_d6_web_unread_marked_read_on_open(fx, web):
    """Unread handling (messaging): a received message (agent -> human)
    is "unread" until the conversation is viewed; opening
    the conversation marks it read — the list counter drops
    immediately back to zero and the state persists after reload."""
    from .web_helpers import authed, json_get

    fx.client.send_message(f"{ORG_NAME}_humain", "New urgent request.",
                           "d6-unread-1", ALICE, ALICE_PASSWORD)
    fx.client.send_message(f"{ORG_NAME}_humain", "And a second message.",
                           "d6-unread-2", ALICE, ALICE_PASSWORD)
    _, port = web
    opener = authed(port)

    # Before viewing: the conversation carries 2 unread for the human.
    listing = json_get(port, "/api/conversations", opener)
    conv = next(c for c in listing["conversations"]
                if ORG_NAME + "_humain" in c["participants"])
    assert conv["unread_count"] == 2, conv

    # Opening the conversation: the messages addressed to the human
    # switch to "read" (read_at filled in the response).
    detail = json_get(port, "/api/conversation?conversation_id=" + conv["conversation_id"], opener)
    read_at = {m["message_id"]: m["read_at"] for m in detail["messages"]
               if m["recipient_username"] == ORG_NAME + "_humain"}
    assert len(read_at) == 2 and all(v is not None for v in read_at.values()), read_at

    # The list counter drops immediately back to zero.
    listing2 = json_get(port, "/api/conversations", opener)
    conv2 = next(c for c in listing2["conversations"]
                 if ORG_NAME + "_humain" in c["participants"])
    assert conv2["unread_count"] == 0, conv2

    # Consistency after reload: the read state persists (read_at).
    detail2 = json_get(port, "/api/conversation?conversation_id=" + conv["conversation_id"], opener)
    still_unread = [m for m in detail2["messages"]
                    if m["recipient_username"] == ORG_NAME + "_humain" and m["read_at"] is None]
    assert still_unread == [], still_unread


def test_d6_web_orgs_route_and_selection(fx, web):
    """End to end: /api/orgs (without a session) exposes the active
    organizations; selection login (organization only, without a
    password) creates a session."""
    from .web_helpers import authed, json_get

    _, port = web
    data = json_get(port, "/api/orgs")
    names = [o["organization_name"] for o in data["organizations"]]
    assert ORG_NAME in names
    opener = authed(port)  # selection login (org only)
    session = json_get(port, "/api/session", opener)
    assert session["organization_name"] == ORG_NAME
    assert session["principal_type"] == "human"
    # the identity does carry the human of the chosen organization
    assert session["human_username"] == f"{ORG_NAME}_humain"


def test_d6_web_orgs_route_excludes_disabled(fx, web):
    """The web list excludes disabled organizations."""
    from .web_helpers import json_get

    fx.client.create_org("org_gel_web6", "motdepasse-gel-web6-1",
                         f"{ORG_NAME}_humain", ORG_PASSWORD)
    fx.client.disable_org("org_gel_web6", "org_gel_web6_humain",
                          "motdepasse-gel-web6-1")
    _, port = web
    data = json_get(port, "/api/orgs")
    names = [o["organization_name"] for o in data["organizations"]]
    assert "org_gel_web6" not in names
