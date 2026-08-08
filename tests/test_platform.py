"""F21 — Platform: complete end-to-end integration scenario.

A coordinating agent drives an organization: capability discovery,
task delegation, escalation, groups, approval, reputation, observer
supervision, A2A exposure. Every building block of steps 1 to 5 is exercised
in a single scenario, with no sensitive content in the audit.
"""

from __future__ import annotations

import json
import urllib.request

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ORG_NAME, ORG_PASSWORD


def test_full_platform_scenario(fx):
    # --- F2/F3: capability discovery ---------------------------------
    fx.client.set_agent_card(
        ["comptabilite", "rapports"], ALICE, ALICE_PASSWORD,
        domain="finance", model="comptable",
    )
    fx.client.set_agent_card(["support", "escalade"], BOB, BOB_PASSWORD, domain="relation")
    fx.client.set_agent_visibility(ALICE, True, ORG_NAME, ORG_PASSWORD)
    fx.client.set_agent_visibility(BOB, True, ORG_NAME, ORG_PASSWORD)
    found = fx.client.find_agents(ALICE, ALICE_PASSWORD, capability="compta")
    assert any(a["username"] == ALICE for a in found["agents"])

    # --- F5/F7/F9: tasks, transfer, escalation ----------------------------
    fx.client.set_escalation_policy(True, 1, 1, ALICE, ORG_NAME, ORG_PASSWORD)
    tid = fx.client.create_task("Monthly close", BOB, ALICE, ALICE_PASSWORD,
                                priority="high")["task_id"]
    fx.client.update_task_state(tid, "in_progress", BOB, BOB_PASSWORD)
    # transfer to alice (the creator), then completion
    fx.client.transfer_task(tid, ALICE, BOB, BOB_PASSWORD, note="taken over by the lead")
    fx.client.update_task_state(tid, "completed", ALICE, ALICE_PASSWORD,
                                result="close performed")

    # --- F15: coordination group --------------------------------------
    gid = fx.client.create_group("cloture-2026", ALICE, ALICE_PASSWORD)["group_id"]
    fx.client.add_group_member(gid, BOB, ALICE, ALICE_PASSWORD)
    fx.client.send_group_message(gid, "Close delivered", ALICE, ALICE_PASSWORD)

    # --- F8: approval of a result -------------------------------------
    tid2 = fx.client.create_task("Expense report", ALICE, BOB, BOB_PASSWORD)["task_id"]
    fx.client.update_task_state(tid2, "in_progress", ALICE, ALICE_PASSWORD)
    fx.client.request_approval(tid2, BOB, ALICE, ALICE_PASSWORD)
    fx.client.approve_task(tid2, BOB, BOB_PASSWORD)
    assert fx.client.get_task(tid2, ALICE, ALICE_PASSWORD)["state"] == "completed"

    # --- F16: measured reputation -------------------------------------------
    rep = fx.client.get_agent_reputation(ALICE, BOB, BOB_PASSWORD)
    assert rep["qualitative"] in ("excellent", "bon")

    # --- F17: controlled delegation -----------------------------------------
    tid3 = fx.client.create_task("Verification", ALICE, ALICE, ALICE_PASSWORD)["task_id"]
    from synapse.validation import now_utc_offset

    fx.client.create_delegation(tid3, BOB, now_utc_offset(-86400), ALICE, ALICE_PASSWORD)

    # --- F11/F12: audit without content + metrics ----------------------------
    entries = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD)["entries"]
    blob = str(entries)
    assert "Monthly close" not in blob  # never content
    metrics = fx.client.get_org_metrics(ORG_NAME, ORG_PASSWORD)
    assert metrics["tasks_by_state"].get("completed", 0) >= 2

    # --- F18: observer supervision ----------------------------------
    fx.client.create_observer_account("superviseur", "motdepasse-superviseur-1",
                                      "Supervision", ORG_NAME, ORG_PASSWORD)
    snap = fx.client.get_org_snapshot("superviseur", "motdepasse-superviseur-1")
    assert snap["tasks_by_state"].get("completed", 0) >= 2
    assert "SECRET" not in str(snap)

    # --- F20: A2A exposure ------------------------------------------------
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(fx.config, ALICE, ALICE_PASSWORD, port=0,
                       token="jeton-de-test-platform-123456")
    bridge.start()
    try:
        assert bridge._server is not None
        port = bridge._server.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/.well-known/agent.json",
            headers={"X-Synapse-Token": "jeton-de-test-platform-123456"},
        )
        with urllib.request.urlopen(req) as resp:
            card = json.loads(resp.read().decode("utf-8"))
        assert card["name"] == ALICE
    finally:
        bridge.stop()

    # --- F1: the auth cache serves closely-spaced commands -----
    # (covered by test_auth_cache; here we simply check continuity)
    assert fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]


def test_two_agents_minimal_org(fx):
    """ "Small first" principle (SPEC.txt §V.4): two coordinated agents with
    no advanced features — the plain org foundation stays valid."""
    sent = fx.send(ALICE, ALICE_PASSWORD, BOB, "ping", "cmid-min-1")
    got = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert any(m["message_id"] == sent["message_id"] for m in got["messages"])
