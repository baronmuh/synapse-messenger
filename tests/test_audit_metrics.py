"""F11/F12 — Organizational audit (append-only, no content) and metrics
(organization + server).
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import ACCESS_DENIED

from .conftest import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    ORG2_NAME,
    ORG2_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
)


def test_audit_records_write_commands(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "Bonjour", "cmid-aud-1")
    fx.client.create_task("Audited task", BOB, ALICE, ALICE_PASSWORD)
    entries = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD)["entries"]
    commands = [e["command"] for e in entries]
    assert "send_message" in commands
    assert "create_task" in commands
    # each entry has actor, target, outcome, timestamp
    send = next(e for e in entries if e["command"] == "send_message")
    assert send["actor_username"] == ALICE
    assert send["target_username"] == BOB
    assert send["outcome"] == "ok"
    assert send["at"].endswith("Z")


def test_audit_isolated_between_organizations(fx):
    from synapse.install import create_organization

    create_organization(fx.config, ORG2_NAME, ORG2_PASSWORD, ORG2_PASSWORD)
    fx.client.create_agent("carol", "motdepasse-carol-1", "desc", ORG2_NAME, ORG2_PASSWORD)
    fx.client.create_agent("dana", "motdepasse-dana-1", "desc", ORG2_NAME, ORG2_PASSWORD)
    fx.client.send_message("dana", "pour dana", "cmid-aud-2", "carol", "motdepasse-carol-1")
    # the root org only sees its own entries
    entries = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD)["entries"]
    assert all("carol" not in (e["actor_username"] or "") for e in entries)
    org2_entries = fx.client.get_org_audit(ORG2_NAME, ORG2_PASSWORD)["entries"]
    assert any(e["actor_username"] == "carol" for e in org2_entries)


def test_audit_filters(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-aud-f1")
    fx.client.create_task("T", BOB, ALICE, ALICE_PASSWORD)
    by_actor = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD, actor_username=ALICE)
    assert all(e["actor_username"] == ALICE for e in by_actor["entries"])
    by_command = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD, command="create_task")
    assert len(by_command["entries"]) == 1
    assert by_command["entries"][0]["command"] == "create_task"
    # "since date" filter (since branch)
    from synapse.validation import now_utc_offset

    future = now_utc_offset(-86400)  # tomorrow: no entries
    past = now_utc_offset(86400)  # yesterday: all
    assert fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD, since=future)["entries"] == []
    assert len(fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD, since=past)["entries"]) >= 2


def test_audit_contains_no_content(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "SECRET-INTERNE-42", "cmid-aud-secret")
    entries = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD)["entries"]
    blob = str(entries)
    assert "SECRET-INTERNE-42" not in blob


def test_audit_pagination(fx):
    for i in range(3):
        fx.send(ALICE, ALICE_PASSWORD, BOB, f"m{i}", f"cmid-aud-p{i}")
    page1 = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD, limit=2)
    assert len(page1["entries"]) == 2
    assert page1["next_cursor"] is not None
    page2 = fx.client.get_org_audit(ORG_NAME, ORG_PASSWORD, limit=2,
                                    cursor=page1["next_cursor"])
    assert len(page2["entries"]) >= 1
    ids1 = {e["id"] for e in page1["entries"]}
    assert all(e["id"] not in ids1 for e in page2["entries"])


def test_audit_requires_org_auth(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.get_org_audit(ALICE, ALICE_PASSWORD)
    assert exc.value.code == ACCESS_DENIED


def test_metrics(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "un", "cmid-met-1")
    fx.client.create_task("T", BOB, ALICE, ALICE_PASSWORD)
    metrics = fx.client.get_org_metrics(ORG_NAME, ORG_PASSWORD)
    # alice + bob + the auto-created human account (SPEC-WEB §5)
    assert metrics["total_agents"] == 3
    assert metrics["active_agents"] == 3
    assert metrics["tasks_by_state"].get("submitted") == 1
    assert metrics["messages_last_hour"] == 1


def test_server_status(fx):
    status = fx.client.get_server_status(ORG_NAME, ORG_PASSWORD)
    assert status["api_version"] == "v2"
    assert status["commands_count"] >= 40
    assert status["requests_total"] >= 1
    assert status["uptime_seconds"] >= 0
    assert status["max_concurrent_connections"] == 64


def test_metrics_reflect_disabled_agent(fx):
    fx.client.deactivate_agent(BOB, ORG_NAME, ORG_PASSWORD)
    metrics = fx.client.get_org_metrics(ORG_NAME, ORG_PASSWORD)
    # alice + the human account remain active; bob disabled
    assert metrics["active_agents"] == 2
