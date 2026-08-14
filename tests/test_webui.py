"""Human web interface (SPEC-WEB D5/D2) — sessions, login, cookie,
non-disclosure, read and management routes.

The web server starts WITHOUT any secret (neither observer nor token):
organization + password login creates a per-user session (HttpOnly
SameSite=Strict cookie). The snapshot never contains content;
conversations and management go through the session identity.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ORG2_NAME, ORG2_PASSWORD, ORG_NAME, ORG_PASSWORD
from .web_helpers import (HUMAN, authed, get, json_get, login, opener_factory,
                          post, session_token)

SECRET = "CONTENU-TRES-SECRET-42"


def _seed_activity(fx):
    """Messages with secret content + task with a secret title + agent card."""
    fx.send(ALICE, ALICE_PASSWORD, BOB, SECRET, "cmid-1")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "second message", "cmid-2")
    fx.send(ALICE, ALICE_PASSWORD, BOB, "read reply", "cmid-3")
    fx.client.create_task(SECRET, BOB, ALICE, ALICE_PASSWORD,
                          description=f"desc {SECRET}", priority="high")
    fx.client.set_agent_card(["analyse", "rapport"], ALICE, ALICE_PASSWORD,
                             domain="finance", model="llm-x")


# ---------------------------------------------------------------------------
# D5 — Sessions: login, cookie, session, logout, lockout
# ---------------------------------------------------------------------------

def test_webui_login_sets_http_only_cookie(web):
    """Login (org + password) sets an HttpOnly
    SameSite=Strict cookie — the static key is gone."""
    _, port = web
    opener = opener_factory()
    _, status = login(port, opener=opener)
    assert status == 200
    cookies = [c for c in opener.cookiejar if c.name == "synapse_session"]
    assert len(cookies) == 1
    assert cookies[0].secure is False  # localhost HTTP
    # HttpOnly + SameSite are Set-Cookie attributes (never read by JS)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/login",
        data=json.dumps({"organization_name": ORG_NAME,
                         "organization_password": ORG_PASSWORD}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        set_cookie = resp.headers["Set-Cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert "Path=/" in set_cookie


def test_webui_session_info(web):
    _, port = web
    opener = authed(port)
    data = json_get(port, "/api/session", opener)
    assert data["organization_name"] == ORG_NAME
    assert data["human_username"] == HUMAN
    assert data["principal_type"] == "human"
    assert data["expires_at"] > time.time()


def test_webui_requires_session(web):
    """No /api/* route without a session: 401 + cookie cleared."""
    _, port = web
    for path in ("/api/snapshot", "/api/org", "/api/conversations",
                 "/api/agents/alice"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(port, path)
        assert exc.value.code == 401, path
        assert "session required" in exc.value.read().decode("utf-8")


def test_webui_bad_login_401(web):
    """SPEC-WEB D5 amended: no more password — an unknown (or disabled)
    organization is refused (generic 401)."""
    _, port = web
    _, status = login(port, org="org_inexistante")
    assert status == 401


def test_webui_login_unknown_org_401(web):
    _, port = web
    _, status = login(port, org="org_inexistante")
    assert status == 401


def test_webui_login_rate_limit_lockout(web, fx):
    """SPEC-WEB D5 amended: selection login no longer has a password — a
    login fails when the organization is unknown or disabled. Five
    failures on one organization → 429 lockout (per org); other
    organizations remain reachable."""
    _, port = web
    fx.client.create_org("org_autre", ORG2_PASSWORD, HUMAN, ORG_PASSWORD)
    for _ in range(5):
        _, status = login(port, org="org_inexistante")
        assert status == 401
    _, status = login(port, org="org_inexistante")  # org locked
    assert status == 429
    # the real organization is not affected
    _, status = login(port)
    assert status == 200
    _, status = login(port, org="org_autre")
    assert status == 200


def test_webui_logout_destroys_session(web):
    """Logout: the session is destroyed, the next route returns 401."""
    _, port = web
    opener = authed(port)
    with post(port, "/api/logout", {}, opener) as resp:
        assert resp.status == 200
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/api/session", opener)
    assert exc.value.code == 401


def test_webui_session_ttl_expiry(web, fx):
    """Session TTL: after expiry, access returns 401 and the session is
    forgotten (the org's session count decreases)."""
    ui, port = web
    opener = authed(port)
    assert len([s for s in ui._sessions.values() if s.org_name == ORG_NAME]) == 1
    # force expiry by aging the session
    with ui._sessions_lock:
        for token, s in list(ui._sessions.items()):
            ui._sessions[token] = type(s)(
                org_name=s.org_name, human_username=s.human_username,
                org_password=s.org_password, created_at=s.created_at,
                last_used_at=s.last_used_at - ui._config.web_session_ttl_seconds - 1)
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/api/session", opener)
    assert exc.value.code == 401


def test_webui_max_sessions_per_org(web, fx):
    """At most web_max_sessions simultaneous sessions per organization: a
    new login evicts the oldest one."""
    ui, port = web
    max_sessions = ui._config.web_max_sessions
    openers = []
    for _ in range(max_sessions + 2):
        opener = authed(port)
        openers.append(opener)
    active = [s for s in ui._sessions.values() if s.org_name == ORG_NAME]
    assert len(active) == max_sessions
    # the oldest is evicted (401); the newest works
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/api/session", openers[0])
    assert exc.value.code == 401
    assert json_get(port, "/api/session", openers[-1])["organization_name"] == ORG_NAME


def test_webui_session_survives_org_password_rotation(web, fx):
    """SPEC-WEB D5 amended: the web session is carried by the local trust
    token, no longer by the organization password — rotating the org
    password (socket API) does not cut current sessions. Disabling the
    organization, on the other hand, invalidates them (covered by
    test_webui_session_invalidated_by_org_disable)."""
    _, port = web
    opener = authed(port)
    fx.client.change_organization_password(ORG_PASSWORD + "n", ORG_NAME, ORG_PASSWORD)
    assert json_get(port, "/api/session", opener)["organization_name"] == ORG_NAME
    assert json_get(port, "/api/snapshot", opener)["organization_name"] == ORG_NAME
    # the socket API, for its part, follows the rotation (delegation kept)
    fx.client.get_org_snapshot(HUMAN, ORG_PASSWORD + "n")


# ---------------------------------------------------------------------------
# D2 — Organization switch (re-login)
# ---------------------------------------------------------------------------

def test_webui_switch_organization(fx, web):
    """Sign out then log into another organization: the session carries
    the new identity, the data follows."""
    fx.client.create_org(ORG2_NAME, ORG2_PASSWORD, HUMAN, ORG_PASSWORD)
    _, port = web
    opener = authed(port)
    assert json_get(port, "/api/session", opener)["organization_name"] == ORG_NAME
    with post(port, "/api/logout", {}, opener) as resp:
        assert resp.status == 200
    _, status = login(port, org=ORG2_NAME, password=ORG2_PASSWORD, opener=opener)
    assert status == 200
    session = json_get(port, "/api/session", opener)
    assert session["organization_name"] == ORG2_NAME
    assert session["human_username"] == f"{ORG2_NAME}_humain"


# ---------------------------------------------------------------------------
# App shell and assets (public, no data)
# ---------------------------------------------------------------------------

def test_webui_serves_app_shell(web):
    _, port = web
    with get(port, "/") as resp:
        html = resp.read().decode("utf-8")
    assert "Synapse — Supervision" in html
    assert "/assets/css/tokens.css" in html
    assert "login-root" in html  # login screen mounted by the app
    assert "app" in html


def test_webui_serves_static_assets(web):
    _, port = web
    for path, ctype in (
        ("/assets/css/tokens.css", "text/css"),
        ("/assets/css/base.css", "text/css"),
        ("/assets/js/app.js", "application/javascript"),
        ("/assets/js/views/dashboard.js", "application/javascript"),
        ("/assets/js/views/conversations.js", "application/javascript"),
        ("/assets/js/views/login.js", "application/javascript"),
    ):
        with get(port, path) as resp:
            body = resp.read()
        assert resp.headers["Content-Type"].startswith(ctype), path
        assert len(body) > 200, path


def test_webui_path_traversal_blocked(web):
    _, port = web
    for path in ("/assets/../service.py", "/assets/..%2Fservice.py",
                 "/assets/%2e%2e/service.py", "/assets/../../etc/passwd"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(port, path)
        assert exc.value.code == 404, path


def test_webui_unknown_api_404(web):
    _, port = web
    opener = authed(port)
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/api/inexistant", opener)
    assert exc.value.code == 404


# ---------------------------------------------------------------------------
# Snapshot: metadata yes, content never
# ---------------------------------------------------------------------------

def test_webui_snapshot_exposes_metadata_only(web, fx):
    _, port = web
    opener = authed(port)
    _seed_activity(fx)
    body = json_get(port, "/api/snapshot", opener)

    assert SECRET not in json.dumps(body)

    pairs = {(c["a"], c["b"]): c for c in body["conversations"]}
    conv = pairs.get((ALICE, BOB)) or pairs.get((BOB, ALICE))
    assert conv is not None
    assert conv["message_count"] == 3
    assert "unread_count" in conv

    assert body["tasks"], "at least one task visible"
    for t in body["tasks"]:
        assert "title" not in t
        assert "description" not in t
        assert "result" not in t

    by_name = {a["username"]: a for a in body["agents"]}
    assert by_name[HUMAN]["principal_type"] == "human"
    assert by_name[ALICE]["principal_type"] == "agent"


def test_webui_snapshot_unread_tracking(fx):
    """snapshot_ttl=0 (cache short-circuited): a read message decrements
    the unread counter, with the session's human identity."""
    from synapse.web import SynapseWebUI

    ui = SynapseWebUI(fx.config, port=0, snapshot_ttl=0.0)
    ui.start()
    try:
        port = ui._server.server_address[1]
        opener = authed(port)
        fx.send(ALICE, ALICE_PASSWORD, BOB, "to read", "cmid-1")
        fx.send(ALICE, ALICE_PASSWORD, BOB, "also to read", "cmid-2")
        body = json_get(port, "/api/snapshot", opener)
        conv = body["conversations"][0]
        assert conv["message_count"] == 2
        assert conv["unread_count"] == 2
        fx.client.read_message(
            fx.client.get_messages(BOB, BOB_PASSWORD)["messages"][0]["message_id"],
            BOB, BOB_PASSWORD)
        body = json_get(port, "/api/snapshot", opener)
        conv = next(c for c in body["conversations"] if c["a"] == ALICE and c["b"] == BOB)
        assert conv["unread_count"] == 1
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Agent detail, search, organization, conversations
# ---------------------------------------------------------------------------

def test_webui_agent_detail(web, fx):
    _, port = web
    opener = authed(port)
    _seed_activity(fx)
    data = json_get(port, f"/api/agents/{ALICE}", opener)
    assert data["username"] == ALICE
    assert data["organization_name"] == ORG_NAME
    assert "description" in data
    assert data["card"]["capabilities"] == ["analyse", "rapport"]
    assert data["card"]["domain"] == "finance"
    assert "reputation" in data


def test_webui_agent_not_found_404(web):
    _, port = web
    opener = authed(port)
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/api/agents/agent-inconnu", opener)
    assert exc.value.code == 404


def test_webui_search_finds_agent(web, fx):
    _, port = web
    opener = authed(port)
    fx.client.set_agent_card(["analyse"], ALICE, ALICE_PASSWORD)
    data = json_get(port, "/api/search?q=ali", opener)
    names = [a["username"] for a in data["agents"]]
    assert ALICE in names
    data = json_get(port, "/api/search?capability=analyse", opener)
    assert any("analyse" in (a.get("capabilities") or []) for a in data["agents"])


def test_webui_search_requires_query(web):
    _, port = web
    opener = authed(port)
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/api/search", opener)
    assert exc.value.code == 400


def test_webui_org_info(web):
    _, port = web
    opener = authed(port)
    data = json_get(port, "/api/org", opener)
    assert data["organization_name"] == ORG_NAME
    assert data["human_username"] == HUMAN
    assert data["principal_type"] == "human"
    assert data["read_only"] is False  # the human writes (management)
    assert "allow_incoming_external" in data


def test_webui_conversations_list_and_content(web, fx):
    """D1 via the web: conversations list then content read (option B)
    with the session's human identity."""
    _, port = web
    opener = authed(port)
    fx.send(HUMAN, ORG_PASSWORD, ALICE, "question web", "cmid-w-1")
    fx.send(ALICE, ALICE_PASSWORD, HUMAN, "secret reply", "cmid-w-2")
    body = json_get(port, "/api/conversations", opener)
    conv = next(c for c in body["conversations"] if HUMAN in c["participants"])
    assert conv["message_count"] == 2
    assert "content" not in conv
    detail = json_get(port, f"/api/conversation?conversation_id={conv['conversation_id']}", opener)
    contents = [m["content"] for m in detail["messages"]]
    assert contents == ["question web", "secret reply"]


def test_webui_send_message(web, fx):
    """The human sends a message from the interface."""
    _, port = web
    opener = authed(port)
    with post(port, "/api/send", {"recipient_username": ALICE,
                                  "message": "hello from the web"}, opener) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assert data["sender_username"] == HUMAN
    assert data["recipient_username"] == ALICE
    msg = fx.client.get_messages(ALICE, ALICE_PASSWORD)["messages"][0]
    assert msg["content"] == "hello from the web"


# ---------------------------------------------------------------------------
# D3 via the web: agents and organizations management
# ---------------------------------------------------------------------------

def test_webui_create_and_disable_agent(web, fx):
    _, port = web
    opener = authed(port)
    with post(port, "/api/agents", {
            "username": "agent_web", "password": "motdepasse-agent-web-1",
            "description": "Created from the web"}, opener) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assert data["status"] == "active"
    with post(port, "/api/agents/agent_web/deactivate", {}, opener) as resp:
        assert json.loads(resp.read().decode("utf-8"))["status"] == "disabled"
    with post(port, "/api/agents/agent_web/reactivate", {}, opener) as resp:
        assert json.loads(resp.read().decode("utf-8"))["status"] == "active"
    with post(port, "/api/agents/agent_web/description",
              {"description": "Web update"}, opener) as resp:
        assert json.loads(resp.read().decode("utf-8"))["description"] == "Web update"


def test_webui_create_org(web):
    """Organization creation FROM THE LOGIN PAGE (SPEC-WEB D5 amended): no
    session required — the local web (trust token) creates the
    organization and its human account."""
    _, port = web
    # Without a session (no cookie): creation is publicly accessible.
    with post(port, "/api/orgs", {"organization_name": "org_web",
                                  "organization_password": "motdepasse-org-web-1"},
              None) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assert data["human_username"] == "org_web_humain"
    # The new organization appears in the login page list.
    with get(port, "/api/orgs") as resp:
        orgs = json.loads(resp.read().decode("utf-8"))["organizations"]
    assert "org_web" in [o["organization_name"] for o in orgs]
    # And selection login works on the new organization.
    with post(port, "/api/login", {"organization_name": "org_web"}, None) as resp:
        assert resp.status == 200


def test_webui_create_org_errors(web):
    """Creation errors relayed with their message (400) — name already
    used, invalid password; the service refuses cleanly."""
    _, port = web
    body = {"organization_name": "org_web_err",
            "organization_password": "motdepasse-org-err-1"}
    with post(port, "/api/orgs", body, None) as resp:
        assert resp.status == 200
    # Name already used -> 400 with the business message.
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(port, "/api/orgs", body, None)
    assert exc.value.code == 400
    # Password too short -> 400.
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(port, "/api/orgs",
             {"organization_name": "org_web_err2", "organization_password": "court"},
             None)
    assert exc.value.code == 400


def test_webui_agent_create_refuses_human_suffix(web):
    _, port = web
    opener = authed(port)
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(port, "/api/agents", {"username": "autre_humain",
                                   "password": "motdepasse-xxx-123",
                                   "description": "interdit"}, opener)
    # business errors are relayed as 400 with the API code and message
    # (previously a generic 500 hid the refusal)
    assert exc.value.code == 400
    body = json.loads(exc.value.read().decode())
    assert body["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# Cache ETag
# ---------------------------------------------------------------------------

def test_webui_snapshot_etag_304(web):
    _, port = web
    opener = authed(port)
    with get(port, "/api/snapshot", opener) as resp:
        resp.read()
        etag = resp.headers["ETag"]
    assert etag
    token = session_token(opener)
    assert token
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/snapshot",
                                 headers={"If-None-Match": etag,
                                          "Cookie": f"synapse_session={token}"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 304


def test_webui_server_side_cache_ttl(web):
    """The server-side cache (per organization) avoids hammering auth."""
    ui, port = web
    opener = authed(port)
    json_get(port, "/api/snapshot", opener)
    assert f"snapshot:{ORG_NAME}" in ui._cache
    json_get(port, f"/api/agents/{ALICE}", opener)
    assert any(k.startswith(f"agent:{ORG_NAME}:") for k in ui._cache)


def test_webui_snapshot_stable_shape(web):
    _, port = web
    opener = authed(port)
    body = json_get(port, "/api/snapshot", opener)
    for key in ("organization_name", "agents", "tasks_by_state", "departments",
                "recent_audit", "messages_last_hour", "conversations", "tasks"):
        assert key in body


def test_webui_org_chart_assets_served(web):
    """The live org chart (Organization view) is served by the server:
    view JS + styles, with the Cards/OrgChart toggle."""
    _, port = web
    js = get(port, "/assets/js/views/org.js").read().decode("utf-8")
    assert "renderOrgChart" in js
    assert "org-chart" in js
    assert "segmented" in js
    css = get(port, "/assets/css/views.css").read().decode("utf-8")
    assert ".org-chart" in css
    assert ".seg-btn" in css
    # the conversations view and the agents manager are served
    js_conv = get(port, "/assets/js/views/conversations.js").read().decode("utf-8")
    assert "conv-thread" in js_conv
    js_agents = get(port, "/assets/js/views/agents.js").read().decode("utf-8")
    assert "managementPanel" in js_agents


def test_webui_oversized_body_413(web):
    """Bounded request body (1 MiB): 413 before processing."""
    _, port = web
    opener = authed(port)
    big = {"pad": "x" * (1024 * 1024 + 10)}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/send",
        data=json.dumps(big).encode(),
        headers={"Content-Type": "application/json",
                 "Cookie": f"synapse_session={session_token(opener)}"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 413


def test_webui_session_invalidated_by_org_disable(fx, web):
    """C6.3/C4.1 — The organization is disabled during a session: the
    session is invalidated on the next request (401)."""
    _, port = web
    opener = authed(port)
    assert json_get(port, "/api/session", opener)["organization_name"] == ORG_NAME
    fx.client.disable_org(ORG_NAME, HUMAN, ORG_PASSWORD)
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(port, "/api/snapshot", opener)
    assert exc.value.code == 401
    assert "session expired" in exc.value.read().decode("utf-8")
    # local reactivation restores the login
    from synapse.install import enable_organization
    enable_organization(fx.config, ORG_NAME, ORG_PASSWORD)
    opener2 = authed(port)
    assert json_get(port, "/api/session", opener2)["organization_name"] == ORG_NAME
