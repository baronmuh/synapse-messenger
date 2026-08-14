"""Unit coverage for the ``agent`` CLI group handlers.

Monkeypatches ``_client`` / ``resolve_*_auth`` to exercise the human-output,
validation and error branches of ``synapse/cli/agent.py`` without a server.
"""

from __future__ import annotations

import argparse

from synapse.cli import agent as agent_mod
from synapse.client import ApiClientError, ClientTransportError


def _args(**kw):
    base = {"name": None, "text": None, "description": "", "department": None,
            "role": None, "capability": None, "domain": None, "visible": False,
            "set": False, "model": None, "sla": None, "estimated_cost": None,
            "limits": None, "my_name": None, "password_stdin": False,
            "montant": None, "max_active_tasks": None,
            "max_messages_per_hour": None, "clear": False, "motif": None,
            "limit": 50, "cursor": None, "json": False, "config": "/tmp/x/c.json"}
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


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_cmd_create_humain_suffix_refused(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="commercial_humain", password_stdin=True)
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    assert agent_mod._cmd_create(args) == 1
    assert "_humain" in capsys.readouterr().out


def test_cmd_create_empty_password(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", password_stdin=True)
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(agent_mod.sys, "stdin", _Stdin(""))
    assert agent_mod._cmd_create(args) == 1
    assert "empty password on stdin" in capsys.readouterr().out


def test_cmd_create_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", password_stdin=True, visible=True)
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(agent_mod.sys, "stdin", _Stdin("secretpw-123\n"))
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    seen = {}

    def create(self, name, pw, desc, org, passwd, **kw):
        seen.update(kw)
        return {"status": "active"}

    monkeypatch.setattr(agent_mod, "_client",
                        lambda config: type("C", (), {"create_agent": create})())
    assert agent_mod._cmd_create(args) == 0
    assert seen["can_see_org_agents"] is True
    assert "Agent 'support' created" in capsys.readouterr().out


def test_cmd_create_department_and_retry(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", password_stdin=True, department="ops",
                 role="manager")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(agent_mod.sys, "stdin", _Stdin("secretpw-123\n"))
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    calls = []

    class Fake:
        def create_agent(self, *a, **k):
            return {"status": "active"}
        def set_agent_department(self, *a, **k):
            calls.append("set")
            return {}
        def create_department(self, *a, **k):
            calls.append("create_dept")
            return {}
    monkeypatch.setattr(agent_mod, "_client", lambda config: Fake())
    assert agent_mod._cmd_create(args) == 0
    assert calls.count("set") == 1


def test_cmd_create_department_creates_then_retries(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", password_stdin=True, department="ops",
                 role="manager")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(agent_mod.sys, "stdin", _Stdin("secretpw-123\n"))
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    calls = []

    class Fake:
        def create_agent(self, *a, **k):
            return {"status": "active"}
        def set_agent_department(self, *a, **k):
            if not calls:
                calls.append("first")
                raise ApiClientError("USER_NOT_FOUND", "Department ops not found")
            calls.append("retry")
            return {}
        def create_department(self, *a, **k):
            calls.append("create_dept")
            return {}
    monkeypatch.setattr(agent_mod, "_client", lambda config: Fake())
    assert agent_mod._cmd_create(args) == 0
    assert calls.count("retry") == 1


def test_cmd_create_with_capability_card(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", password_stdin=True, capability=["audit"],
                 domain="finance")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(agent_mod.sys, "stdin", _Stdin("secretpw-123\n"))
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    seen = {}

    class Fake:
        def create_agent(self, *a, **k):
            return {"status": "active"}
        def set_agent_card(self, caps, name, pw, **kw):
            seen.update({"caps": caps, "name": name, "pw": pw, **kw})
            return {}
    monkeypatch.setattr(agent_mod, "_client", lambda config: Fake())
    assert agent_mod._cmd_create(args) == 0
    assert seen["caps"] == ["audit"]
    assert seen["domain"] == "finance"
    assert seen["name"] == "support"
    assert seen["pw"] == "secretpw-123"  # the AGENT's password, not the org's


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_cmd_status_human_with_dict_card(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {
        "get_agent_description": lambda self, *a, **k: {"description": "desc"},
        "get_agent_card": lambda self, *a, **k: {"card": {"validation_state": "approved",
                                                           "capabilities": ["audit"]}},
        "get_agent_reputation": lambda self, *a, **k: {"qualitative": "excellent"}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "desc" in out and "approved" in out and "excellent" in out


def test_cmd_status_reputation_completion(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {
        "get_agent_description": lambda self, *a, **k: {"description": "d"},
        "get_agent_card": lambda self, *a, **k: {"card": {}},
        "get_agent_reputation": lambda self, *a, **k: {"completion_rate": 0.9,
                                                        "completed": 3,
                                                        "failed": 1}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_status(args) == 0
    assert "completion" in capsys.readouterr().out


def test_cmd_status_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="ghost")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))

    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(agent_mod, "_client",
                        lambda config: type("C", (), {"get_agent_description": boom})())
    assert agent_mod._cmd_status(args) == 3


# ---------------------------------------------------------------------------
# description / card / department / visibility / budget / password
# ---------------------------------------------------------------------------


def test_cmd_description_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", text="new desc")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    fake = type("C", (), {"change_agent_description": lambda self, *a, **k: {}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_description(args) == 0
    assert "Description of agent 'support' replaced" in capsys.readouterr().out


def test_cmd_card_set_requires_value(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", set=True)
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    assert agent_mod._cmd_card(args) == 1
    assert "--set requires at least one value" in capsys.readouterr().out


def test_cmd_card_set_requires_my_name(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", set=True, model="m1")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    assert agent_mod._cmd_card(args) == 1
    assert "--my-name" in capsys.readouterr().out


def test_cmd_card_set_my_name_mismatch(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", set=True, model="m1", my_name="other")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    assert agent_mod._cmd_card(args) == 1
    assert "must designate" in capsys.readouterr().out


def test_cmd_card_set_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", set=True, model="m1", my_name="support")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_agent_auth_or",
        lambda a, c, my_name: ("support", "pw"))
    fake = type("C", (), {"set_agent_card": lambda self, *a, **k: {
        "card": {"validation_state": "pending"}}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_card(args) == 0
    assert "submitted (validation: pending)" in capsys.readouterr().out


def test_cmd_card_read_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"get_agent_card": lambda self, *a, **k: {
        "card": {"model": "m1", "validation_state": "approved"}}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_card(args) == 0
    out = capsys.readouterr().out
    assert "m1" in out and "approved" in out


def test_cmd_department_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", department="ops", role="manager")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    fake = type("C", (), {"set_agent_department": lambda self, *a, **k: {}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_department(args) == 0
    assert "assigned to department 'ops'" in capsys.readouterr().out


def test_cmd_visibility_visible_and_hidden(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", value="visible")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    seen = {}

    def setvis(self, name, visible, org, pw):
        seen["visible"] = visible
        return {}
    monkeypatch.setattr(agent_mod, "_client",
                        lambda config: type("C", (), {"set_agent_visibility": setvis})())
    assert agent_mod._cmd_visibility(args) == 0
    assert seen["visible"] is True
    assert "visible in the directory" in capsys.readouterr().out


def test_cmd_budget_refusals_and_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    # montant refused
    args = _args(name="support", montant=100)
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    assert agent_mod._cmd_budget(args) == 1
    assert "no monetary budget" in capsys.readouterr().out
    # nothing provided
    args2 = _args(name="support")
    assert agent_mod._cmd_budget(args2) == 1
    assert "no budget provided" in capsys.readouterr().out
    # success with max tasks
    args3 = _args(name="support", max_active_tasks=5)
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    fake = type("C", (), {"set_agent_budget": lambda self, *a, **k: {}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_budget(args3) == 0
    assert "Budgets of agent 'support' updated" in capsys.readouterr().out


def test_cmd_password_success_and_empty(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="support", password_stdin=True)
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(agent_mod.sys, "stdin", _Stdin(""))
    assert agent_mod._cmd_password(args) == 1
    assert "empty password on stdin" in capsys.readouterr().out
    args2 = _args(name="support", password_stdin=True)
    monkeypatch.setattr(agent_mod.sys, "stdin", _Stdin("newpw-123\n"))
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    fake = type("C", (), {"change_agent_password": lambda self, *a, **k: {}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_password(args2) == 0
    assert "Password of agent 'support' changed" in capsys.readouterr().out


def test_cmd_deactivate_reactivate(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    a1 = _args(name="support")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    fake = type("C", (), {"deactivate_agent": lambda self, *a, **k: {},
                          "reactivate_agent": lambda self, *a, **k: {}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_deactivate(a1) == 0
    assert "deactivated" in capsys.readouterr().out
    assert agent_mod._cmd_reactivate(a1) == 0
    assert "reactivated" in capsys.readouterr().out


def test_cmd_find_human_dict_and_plain(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(motif="comp", cursor="c1")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"find_agents": lambda self, *a, **k: {
        "agents": [{"username": "comptable", "description": "accounts"}],
        "next_cursor": "c2"}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_find(args) == 0
    out = capsys.readouterr().out
    assert "comptable" in out and "next page: --cursor c2" in out

    # plain list branch
    args2 = _args()
    fake2 = type("C", (), {"find_agents": lambda self, *a, **k: {
        "usernames": ["alice"], "next_cursor": None}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake2)
    assert agent_mod._cmd_find(args2) == 0
    assert "alice" in capsys.readouterr().out


def test_cmd_find_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(json=True)
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        agent_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"find_agents": lambda self, *a, **k: {
        "agents": [], "next_cursor": None}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_find(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["agents"] == []


def test_cmd_create_observer_revoke_list(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="obs", password_stdin=True, description="d")
    monkeypatch.setattr(agent_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(agent_mod.sys, "stdin", _Stdin("secretpw-123\n"))
    monkeypatch.setattr(
        agent_mod, "resolve_org_auth", lambda c, a: ("acme", "p"))
    fake = type("C", (), {
        "create_observer_account": lambda self, *a, **k: {},
        "revoke_observer_account": lambda self, *a, **k: {},
        "list_observers": lambda self, *a, **k: {
            "observers": [{"observer_name": "obs", "description": "d"}]}})()
    monkeypatch.setattr(agent_mod, "_client", lambda config: fake)
    assert agent_mod._cmd_create_observer(args) == 0
    assert "Observer 'obs' created" in capsys.readouterr().out
    assert agent_mod._cmd_revoke_observer(args) == 0
    assert "Observer 'obs' revoked" in capsys.readouterr().out
    assert agent_mod._cmd_observers(args) == 0
    assert "obs" in capsys.readouterr().out


class _Stdin:
    def __init__(self, text):
        self._lines = iter(text.splitlines())

    def readline(self):
        return next(self._lines, "")
