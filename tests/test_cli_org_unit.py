"""Unit coverage for the ``org`` CLI group handlers.

Monkeypatches ``_client`` / ``resolve_*_auth`` / local procs to exercise the
human-output and error branches of ``synapse/cli/org.py`` without a server.
"""

from __future__ import annotations

import argparse


from synapse.cli import org as org_mod
from synapse.client import ApiClientError, ClientTransportError


def _args(**kw):
    base = {"name": None, "all": False, "json": False, "password_stdin": False,
            "organization_name": None, "limit": 50, "cursor": None,
            "actor": None, "since": None, "command_filter": None,
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


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_cmd_init_with_name(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme", password_stdin=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(org_mod.sys, "stdin", _Stdin("motdepasse-acme-1\n"))
    monkeypatch.setattr(org_mod, "create_organization",
                        lambda c, name, pw, confirm: "acme")
    assert org_mod._cmd_init(args) == 0
    out = capsys.readouterr().out
    assert "'acme' created successfully" in out
    assert "Human account created" in out


def test_cmd_init_empty_stdin_password(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme", password_stdin=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(org_mod.sys, "stdin", _Stdin(""))
    assert org_mod._cmd_init(args) == 1
    assert "empty password on stdin" in capsys.readouterr().out


def test_cmd_init_prompt_canceled(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name=None, password_stdin=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr("builtins.input",
                        lambda prompt: (_ for _ in ()).throw(EOFError()))
    assert org_mod._cmd_init(args) == 1
    assert "operation canceled" in capsys.readouterr().out


def test_cmd_init_empty_prompted_name(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name=None, password_stdin=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr("builtins.input", lambda prompt: "   ")
    assert org_mod._cmd_init(args) == 1
    assert "empty organization name" in capsys.readouterr().out


def test_cmd_init_creation_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme", password_stdin=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(org_mod.sys, "stdin", _Stdin("motdepasse-acme-1\n"))
    monkeypatch.setattr(org_mod, "create_organization",
                        lambda c, name, pw, confirm: (_ for _ in ()).throw(
                            ValueError("name taken")))
    assert org_mod._cmd_init(args) == 1
    assert "name taken" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_cmd_list_human_all(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name=None, all=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        org_mod, "resolve_human_auth", lambda c, a, org_name=None: ("h", "p"))
    fake = type("C", (), {"list_orgs": lambda self, *a, **k: {
        "organizations": [{"organization_name": "acme"}],
        "disabled": [{"organization_name": "old"}]}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_list(args) == 0
    out = capsys.readouterr().out
    assert "acme" in out and "old" in out


def test_cmd_list_json_with_token(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name=None, all=False, json=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(org_mod, "read_web_token", lambda c: "tok")
    import synapse.service as _svc
    monkeypatch.setattr(_svc, "_WEB_LOCAL", "web_local")
    fake = type("C", (), {"list_orgs": lambda self, *a, **k: {
        "organizations": [{"organization_name": "acme"}], "disabled": []}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_list(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["organizations"] == \
        [{"organization_name": "acme"}]


def test_cmd_list_no_active_org_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name=None, all=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)

    def boom(c, a, org_name=None):
        from synapse.cli.common import CliError
        raise CliError("no active organization: specify --organization-name")
    monkeypatch.setattr(org_mod, "resolve_human_auth", boom)
    assert org_mod._cmd_list(args) == 1
    assert "no active organization to derive" in capsys.readouterr().out


def test_cmd_list_api_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name=None, all=False)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(org_mod, "read_web_token", lambda c: "tok")
    import synapse.service as _svc
    monkeypatch.setattr(_svc, "_WEB_LOCAL", "web_local")

    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(org_mod, "_client",
                        lambda config: type("C", (), {"list_orgs": boom})())
    assert org_mod._cmd_list(args) == 3


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_cmd_status_active_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme")
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        org_mod, "resolve_human_auth", lambda c, a, org_name=None: ("h", "p"))
    fake = type("C", (), {
        "get_org_snapshot": lambda self, *a, **k: {
            "agents": [{"status": "active"}], "tasks_by_state": {"done": 2},
            "departments": [], "messages_last_hour": 1},
        "get_org_metrics": lambda self, *a, **k: {"total_agents": 3,
                                                  "active_agents": 1}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "ACTIVE" in out and "total agents" in out


def test_cmd_status_disabled_org_full(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="old_org")
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)

    # snapshot raises AUTH_FAILED
    def snapshot(self, *a, **k):
        raise ApiClientError("AUTH_FAILED", "no")
    # metrics not reached
    fake = type("C", (), {
        "get_org_snapshot": snapshot,
        "get_org_metrics": lambda self, *a, **k: {},
        "list_orgs": lambda self, *a, **k: {
            "organizations": [], "disabled": [{"organization_name": "old_org"}]}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    monkeypatch.setattr(
        org_mod, "resolve_human_auth",
        lambda c, a, org_name=None: ("other_humain", "p"))
    assert org_mod._cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "DEACTIVATED" in out


def test_cmd_status_unreachable(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="ghost")
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)

    def snapshot(self, *a, **k):
        raise ApiClientError("AUTH_FAILED", "no")
    monkeypatch.setattr(org_mod, "_client", lambda config: type("C", (), {
        "get_org_snapshot": snapshot,
        "get_org_metrics": lambda self, *a, **k: {}})())
    # First resolve (org_name=ghost) succeeds; the one inside
    # _status_disabled_org (no org_name) raises CliError -> falls to
    # the unreachable error branch.
    def resolve(c, a, org_name=None):
        if org_name == "ghost":
            return ("h", "p")
        from synapse.cli.common import CliError
        raise CliError("no active org")
    monkeypatch.setattr(org_mod, "resolve_human_auth", resolve)
    assert org_mod._cmd_status(args) == 1
    assert "unreachable" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# enable / disable / password
# ---------------------------------------------------------------------------


def test_cmd_enable_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme", password_stdin=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(org_mod, "read_password", lambda a, p: "pw")
    monkeypatch.setattr(org_mod, "enable_organization",
                        lambda c, name, pw: "acme")
    assert org_mod._cmd_enable(args) == 0
    assert "reactivated successfully" in capsys.readouterr().out


def test_cmd_enable_already_active(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme", password_stdin=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(org_mod, "read_password", lambda a, p: "pw")
    monkeypatch.setattr(org_mod, "enable_organization",
                        lambda c, name, pw: (_ for _ in ()).throw(
                            ValueError("acme is not deactivated")))
    assert org_mod._cmd_enable(args) == 0
    assert "already active" in capsys.readouterr().out


def test_cmd_disable_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme")
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        org_mod, "resolve_human_auth", lambda c, a, org_name=None: ("h", "p"))
    fake = type("C", (), {"disable_org": lambda self, *a, **k: {"ok": True}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_disable(args) == 0
    assert "deactivated (absolute freeze)" in capsys.readouterr().out


def test_cmd_password_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme", password_stdin=True)
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(org_mod.sys, "stdin", _Stdin("newpassword-123\n"))
    monkeypatch.setattr(
        org_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "pw"))
    fake = type("C", (), {"change_organization_password": lambda self, *a, **k: {}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_password(args) == 0
    assert "Password of organization 'acme' changed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# agents / structure / metrics / audit
# ---------------------------------------------------------------------------


def test_cmd_agents_human_with_cursor(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme", cursor="c1")
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        org_mod, "resolve_human_auth", lambda c, a, org_name=None: ("h", "p"))
    fake = type("C", (), {"list_org_agents": lambda self, *a, **k: {
        "usernames": ["alice"], "next_cursor": "c2"}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_agents(args) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "next page: --cursor c2" in out


def test_cmd_structure_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme")
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        org_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))
    fake = type("C", (), {"get_org_structure": lambda self, *a, **k: {
        "departments": [{"department_name": "finance",
                         "members": [{"username": "alice", "role": "lead"}]}],
        "unassigned_agents": ["bob"]}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_structure(args) == 0
    out = capsys.readouterr().out
    assert "finance" in out and "alice" in out and "bob" in out


def test_cmd_metrics_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme")
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        org_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))
    fake = type("C", (), {"get_org_metrics": lambda self, *a, **k: {
        "total_agents": 4}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_metrics(args) == 0
    assert "total_agents" in capsys.readouterr().out


def test_cmd_audit_human_with_cursor(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="acme", since="2026-01-01T00:00:00Z", actor="bob",
                 command_filter="send", cursor="c1")
    monkeypatch.setattr(org_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        org_mod, "resolve_org_auth", lambda c, a, org_name=None: ("acme", "p"))
    fake = type("C", (), {"get_org_audit": lambda self, *a, **k: {
        "entries": [{"at": "t", "actor_username": "bob", "command": "send",
                     "outcome": "ok"}],
        "next_cursor": "c2"}})()
    monkeypatch.setattr(org_mod, "_client", lambda config: fake)
    assert org_mod._cmd_audit(args) == 0
    out = capsys.readouterr().out
    assert "send" in out and "next page: --cursor c2" in out


class _Stdin:
    def __init__(self, text):
        self._lines = iter(text.splitlines())

    def readline(self):
        return next(self._lines, "")
