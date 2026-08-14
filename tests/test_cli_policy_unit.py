"""Unit coverage for the ``policy`` CLI group handlers.

Monkeypatches ``_client`` / ``resolve_*_auth`` to exercise the human-output
and error branches of ``synapse/cli/policy.py`` without a server.
"""

from __future__ import annotations

import argparse

from synapse.cli import policy as pol_mod
from synapse.client import ApiClientError, ClientTransportError


def _args(**kw):
    base = {"org": None, "json": False, "allow_incoming_external": False,
            "deny_incoming_external": False, "allow_outgoing_external": False,
            "deny_outgoing_external": False, "set": False, "max_hours": None,
            "targets": None, "agent": None, "task": None, "expires": None,
            "my_name": None, "limit": 50, "cursor": None,
            "config": "/tmp/x/c.json"}
    base.update(kw)
    return argparse.Namespace(**base)


def _config(tmp_path):
    from synapse.config import Config
    conf = {
        "storage_dir": str(tmp_path / "d"),
        "socket_path": str(tmp_path / "s.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    return Config.from_dict(conf)


def test_cmd_show_human_and_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(org="acme")
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))
    fake = type("C", (), {"get_organization_policy": lambda self, *a, **k: {
        "allow_incoming_external": True}})()
    monkeypatch.setattr(pol_mod, "_client", lambda config: fake)
    assert pol_mod._cmd_show(args) == 0
    assert "allow_incoming_external" in capsys.readouterr().out

    args2 = _args(org="acme", json=True)
    assert pol_mod._cmd_show(args2) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["allow_incoming_external"] is True


def test_cmd_show_api_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(org="acme")
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))

    def boom(self, *a, **k):
        raise ApiClientError("DENIED", "no")
    monkeypatch.setattr(pol_mod, "_client",
                        lambda config: type("C", (), {"get_organization_policy": boom})())
    assert pol_mod._cmd_show(args) == 1
    assert "no" in capsys.readouterr().out


def test_cmd_set_all_flags(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(org="acme", allow_incoming_external=True,
                 deny_outgoing_external=True)
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))
    seen = {}

    def get_policy(self, *a, **k):
        return {"allow_incoming_external": False, "allow_outgoing_external": True}

    def set_policy(self, incoming, outgoing, org, pw):
        seen.update({"incoming": incoming, "outgoing": outgoing})
        return {}

    fake = type("C", (), {"get_organization_policy": get_policy,
                          "set_organization_policy": set_policy})()
    monkeypatch.setattr(pol_mod, "_client", lambda config: fake)
    assert pol_mod._cmd_set(args) == 0
    assert seen["incoming"] is True    # allow flag set
    assert seen["outgoing"] is False   # deny flag set
    out = capsys.readouterr().out
    assert "incoming external allowed" in out
    assert "outgoing external denied" in out


def test_cmd_set_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(org="acme")
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))

    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(pol_mod, "_client",
                        lambda config: type("C", (), {"get_organization_policy": boom})())
    assert pol_mod._cmd_set(args) == 3


def test_cmd_escalation_read_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(org="acme")
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))
    fake = type("C", (), {"get_escalation_policy": lambda self, *a, **k: {
        "enabled": True}})()
    monkeypatch.setattr(pol_mod, "_client", lambda config: fake)
    assert pol_mod._cmd_escalation(args) == 0
    assert "enabled" in capsys.readouterr().out


def test_cmd_escalation_set_with_targets(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(org="acme", set=True, max_hours=24, targets="support")
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))
    seen = {}

    def set_policy(self, enabled, due, failed, targets, org, pw):
        seen.update({"due": due, "targets": targets})
        return {}

    fake = type("C", (), {
        "get_escalation_policy": lambda self, *a, **k: {"due_after_seconds": 3600},
        "set_escalation_policy": set_policy})()
    monkeypatch.setattr(pol_mod, "_client", lambda config: fake)
    assert pol_mod._cmd_escalation(args) == 0
    assert seen["due"] == 24 * 3600
    assert seen["targets"] == "support"
    assert "max delay: 24 h" in capsys.readouterr().out


def test_cmd_escalation_set_no_targets_refused(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(org="acme", set=True)
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))
    fake = type("C", (), {"get_escalation_policy": lambda self, *a, **k: {
        "due_after_seconds": 3600, "escalate_to_username": None}})()
    monkeypatch.setattr(pol_mod, "_client", lambda config: fake)
    assert pol_mod._cmd_escalation(args) == 1
    assert "--targets" in capsys.readouterr().out


def test_cmd_delegate_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(agent="data", task="t-42", expires="2026-09-01T00:00:00Z")
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    seen = {}

    def create(self, task, agent, expires, my, pw):
        seen.update({"expires": expires})
        return {}

    monkeypatch.setattr(pol_mod, "_client",
                        lambda config: type("C", (), {"create_delegation": create})())
    assert pol_mod._cmd_delegate(args) == 0
    assert seen["expires"] == "2026-09-01T00:00:00.000Z"  # normalized
    assert "Task t-42 delegated to data" in capsys.readouterr().out


def test_cmd_revoke_success_and_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(agent="data", task="t-42")
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"revoke_delegation": lambda self, *a, **k: {}})()
    monkeypatch.setattr(pol_mod, "_client", lambda config: fake)
    assert pol_mod._cmd_revoke(args) == 0
    assert "Delegation of task t-42 to data revoked" in capsys.readouterr().out

    def boom(self, *a, **k):
        raise ApiClientError("DENIED", "no")
    monkeypatch.setattr(pol_mod, "_client",
                        lambda config: type("C", (), {"revoke_delegation": boom})())
    assert pol_mod._cmd_revoke(args) == 1


def test_cmd_delegations_human_and_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(cursor="c1")
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"get_my_delegations": lambda self, *a, **k: {
        "delegations": [{"delegator_username": "bob", "task_id": "t-9",
                         "expires_at": "d"}],
        "next_cursor": "c2"}})()
    monkeypatch.setattr(pol_mod, "_client", lambda config: fake)
    assert pol_mod._cmd_delegations(args) == 0
    out = capsys.readouterr().out
    assert "bob" in out and "next page: --cursor c2" in out

    args2 = _args(json=True)
    assert pol_mod._cmd_delegations(args2) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["delegations"][0]["task_id"] == "t-9"


def test_cmd_delegations_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args()
    monkeypatch.setattr(pol_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        pol_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))

    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(pol_mod, "_client",
                        lambda config: type("C", (), {"get_my_delegations": boom})())
    assert pol_mod._cmd_delegations(args) == 3
