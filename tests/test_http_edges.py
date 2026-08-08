"""Coverage blind spots: HTTP branches (404), rare validators and
edge cases of the new modules.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from synapse.client import ApiClientError

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ORG_NAME, ORG_PASSWORD

OBSERVER = "observateur"
OBS_PASSWORD = "motdepasse-observateur-1"
TOKEN = "jeton-de-test-http-123456"


def _get(port, path):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"X-Synapse-Token": TOKEN},
    )
    return urllib.request.urlopen(req)


def test_web_ui_unknown_path_404(fx):
    from synapse.web import SynapseWebUI

    web = SynapseWebUI(fx.config, port=0)
    web.start()
    try:
        assert web._server is not None
        port = web._server.server_address[1]
        from .web_helpers import authed
        opener = authed(port)
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(f"http://127.0.0.1:{port}/chemin/inconnu")
        assert exc.value.code == 404
    finally:
        web.stop()


def test_a2a_unknown_path_404(fx):
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(fx.config, ALICE, ALICE_PASSWORD, port=0, token=TOKEN)
    bridge.start()
    try:
        assert bridge._server is not None
        port = bridge._server.server_address[1]
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(port, "/autre")
        assert exc.value.code == 404
    finally:
        bridge.stop()


def test_event_types_validation(fx):
    fx.client.create_task("T", BOB, ALICE, ALICE_PASSWORD)
    # unknown event types -> INVALID_ARGUMENT
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_events(ALICE, ALICE_PASSWORD, types=["fantome"])
    assert exc.value.code == "INVALID_ARGUMENT"
    # valid types -> filtered
    events = fx.client.get_events(ALICE, ALICE_PASSWORD, types=["task.created"])
    assert all(e["event_type"] == "task.created" for e in events["events"])


def test_group_name_validation(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_group("x" * 65, ALICE, ALICE_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_group("\x00", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_department_name_validation(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.create_department("X" * 65, ORG_NAME, ORG_PASSWORD)
    assert exc.value.code == "INVALID_ARGUMENT"
    # normalized to lowercase
    dept = fx.client.create_department("SUPPORT", ORG_NAME, ORG_PASSWORD)
    assert dept["department_name"] == "support"


def test_delegation_requires_active_delegatee(fx):
    tid = fx.client.create_task("T", ALICE, ALICE, ALICE_PASSWORD)["task_id"]
    fx.client.update_task_state(tid, "in_progress", ALICE, ALICE_PASSWORD)
    from synapse.validation import now_utc_offset

    with pytest.raises(ApiClientError) as exc:
        fx.client.create_delegation(tid, "agent-inconnu", now_utc_offset(-86400),
                                    ALICE, ALICE_PASSWORD)
    assert exc.value.code == "USER_NOT_FOUND"


def test_reputation_of_unknown_account(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_agent_reputation("nobody", ALICE, ALICE_PASSWORD)
    assert exc.value.code == "USER_NOT_FOUND"


def test_revoke_observer_foreign_org(fx):
    from synapse.install import create_organization

    create_organization(fx.config, "second_org", "motdepasse-second-org-1",
                        "motdepasse-second-org-1")
    fx.client.create_observer_account(OBSERVER, OBS_PASSWORD, "obs",
                                      ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.revoke_observer_account(OBSERVER, "second_org",
                                          "motdepasse-second-org-1")
    assert exc.value.code == "USER_NOT_FOUND"


def test_work_pagination(fx):
    """Paged work queue (has_more on the 2nd page)."""
    for i in range(3):
        fx.client.create_task(f"T{i}", BOB, ALICE, ALICE_PASSWORD)
    page1 = fx.client.get_my_work(BOB, BOB_PASSWORD, limit=2)
    assert len(page1["work_items"]) == 2
    assert page1["next_cursor"] is not None
    page2 = fx.client.get_my_work(BOB, BOB_PASSWORD, cursor=page1["next_cursor"])
    assert len(page2["work_items"]) == 1
    ids = {t["task_id"] for t in page1["work_items"]}
    assert page2["work_items"][0]["task_id"] not in ids


def test_list_tasks_pagination(fx):
    for i in range(3):
        fx.client.create_task(f"T{i}", BOB, ALICE, ALICE_PASSWORD)
    page1 = fx.client.list_tasks(ALICE, ALICE_PASSWORD, limit=2)
    assert len(page1["tasks"]) == 2
    assert page1["next_cursor"] is not None
    page2 = fx.client.list_tasks(ALICE, ALICE_PASSWORD, cursor=page1["next_cursor"])
    assert len(page2["tasks"]) == 1
    ids = {t["task_id"] for t in page1["tasks"]}
    assert page2["tasks"][0]["task_id"] not in ids


def test_request_approval_twice_invalid(fx):
    tid = fx.client.create_task("T", BOB, ALICE, ALICE_PASSWORD)["task_id"]
    fx.client.update_task_state(tid, "in_progress", BOB, BOB_PASSWORD)
    fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.request_approval(tid, ALICE, BOB, BOB_PASSWORD)
    assert exc.value.code == "TASK_STATE_INVALID"


def test_reputation_mid_rates(fx):
    """The intermediate qualitative ratings (good, average)."""
    for i, result in enumerate(("ok", "ko", "ok", "ok", "ok")):
        tid = fx.client.create_task(f"T{i}", BOB, ALICE, ALICE_PASSWORD)["task_id"]
        fx.client.update_task_state(tid, "in_progress", BOB, BOB_PASSWORD)
        state = "failed" if result == "ko" else "completed"
        fx.client.update_task_state(tid, state, BOB, BOB_PASSWORD, result=result)
    rep = fx.client.get_agent_reputation(BOB, ALICE, ALICE_PASSWORD)
    assert rep["qualitative"] == "good"  # 4/5 = 0.8
    tid = fx.client.create_task("T5", BOB, ALICE, ALICE_PASSWORD)["task_id"]
    fx.client.update_task_state(tid, "in_progress", BOB, BOB_PASSWORD)
    fx.client.update_task_state(tid, "failed", BOB, BOB_PASSWORD, result="ko")
    rep = fx.client.get_agent_reputation(BOB, ALICE, ALICE_PASSWORD)
    assert rep["qualitative"] == "average"  # 4/6 ≈ 0.67


def test_delegations_pagination(fx):
    tid = fx.client.create_task("T", ALICE, ALICE, ALICE_PASSWORD)["task_id"]
    fx.client.update_task_state(tid, "in_progress", ALICE, ALICE_PASSWORD)
    from synapse.validation import now_utc_offset

    for i in range(3):
        fx.client.create_delegation(tid, BOB, now_utc_offset(-86400), ALICE, ALICE_PASSWORD)
    page1 = fx.client.get_my_delegations(BOB, BOB_PASSWORD, limit=2)
    assert len(page1["delegations"]) == 2
    assert page1["next_cursor"] is not None
    page2 = fx.client.get_my_delegations(BOB, BOB_PASSWORD, cursor=page1["next_cursor"])
    assert len(page2["delegations"]) == 1
    ids = {d["id"] for d in page1["delegations"]}
    assert page2["delegations"][0]["id"] not in ids


def test_auth_cache_prune_expired(fx):
    """The authentication cache purge removes expired entries."""
    service = fx.server.service
    cache = service._auth_cache
    cache.clear()
    import time

    now = time.monotonic()
    # expired and valid entries (shape (hash, digest, expiration))
    cache["expiree"] = ("hash", "digest", now - 1.0)
    cache["valide"] = ("hash", "digest", now + 60.0)
    service._prune_auth_cache()
    assert "expiree" not in cache
    assert "valide" in cache
