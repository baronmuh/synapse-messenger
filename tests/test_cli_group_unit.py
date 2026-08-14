"""Unit coverage for the ``group`` CLI group handlers.

Monkeypatches ``_client`` / ``_resolve_group`` to exercise the human-output
and error branches of ``synapse/cli/group.py`` without a server.
"""

from __future__ import annotations

import argparse

from synapse.cli import group as group_mod
from synapse.client import ApiClientError, ClientTransportError


def _args(**kw):
    base = {"name": None, "member": None, "text": None, "description": None,
            "my_name": None, "limit": 50, "cursor": None,
            "client_message_id": None, "json": False,
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


def test_cmd_create_with_description_refused(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", description="desc")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    assert group_mod._cmd_create(args) == 1
    assert "does not support a description" in capsys.readouterr().out


def test_cmd_create_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", description=None)
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"create_group": lambda self, *a, **k: {
        "group_id": "g1"}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_create(args) == 0
    assert "Group 'direction' created (g1)" in capsys.readouterr().out


def test_cmd_create_api_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", description=None)
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))

    def boom(self, *a, **k):
        raise ApiClientError("DENIED", "no")
    monkeypatch.setattr(group_mod, "_client",
                        lambda config: type("C", (), {"create_group": boom})())
    assert group_mod._cmd_create(args) == 1
    assert "no" in capsys.readouterr().out


def test_resolve_group_not_found(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="ghost", my_name="alice")
    monkeypatch.setattr(
        group_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"list_my_groups": lambda self, *a, **k: {
        "groups": [{"name": "other", "group_id": "g9"}], "next_cursor": None}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    with __import__("pytest").raises(group_mod.CliGroupError):
        group_mod._resolve_group(config, args, "ghost")


def test_resolve_group_paginates_and_finds(tmp_path, monkeypatch):
    config = _config(tmp_path)
    args = _args(name="target", my_name="alice")
    monkeypatch.setattr(
        group_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    calls = {"n": 0}

    def list_groups(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"groups": [{"name": "other", "group_id": "g1"}],
                    "next_cursor": "c2"}
        return {"groups": [{"name": "target", "group_id": "g2"}],
                "next_cursor": None}

    fake = type("C", (), {"list_my_groups": list_groups})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    gid, my, pw = group_mod._resolve_group(config, args, "target")
    assert gid == "g2"
    assert calls["n"] == 2


def test_cmd_members_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))
    fake = type("C", (), {"get_group_members": lambda self, *a, **k: {
        "members": [{"username": "bob"}, {"username": "carol"}]}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_members(args) == 0
    out = capsys.readouterr().out
    assert "bob" in out and "carol" in out


def test_cmd_members_string_list(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))
    fake = type("C", (), {"get_group_members": lambda self, *a, **k: {
        "members": ["dave", "erin"]}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_members(args) == 0
    out = capsys.readouterr().out
    assert "dave" in out and "erin" in out


def test_cmd_members_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", json=True)
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))
    fake = type("C", (), {"get_group_members": lambda self, *a, **k: {
        "members": ["dave"]}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_members(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["members"] == ["dave"]


def test_cmd_add_member_success_and_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", member="bob")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))
    fake = type("C", (), {"add_group_member": lambda self, *a, **k: {}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_add_member(args) == 0
    assert "bob added to group 'direction'" in capsys.readouterr().out

    # error branch: group not found
    def resolve_group(c, a, name):
        raise group_mod.CliGroupError("group 'direction' not found")
    monkeypatch.setattr(group_mod, "_resolve_group", resolve_group)
    assert group_mod._cmd_add_member(args) == 1
    assert "not found" in capsys.readouterr().out


def test_cmd_remove_member_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", member="bob")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))
    fake = type("C", (), {"remove_group_member": lambda self, *a, **k: {}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_remove_member(args) == 0
    assert "bob removed from group 'direction'" in capsys.readouterr().out

    # error branch: not found
    def resolve_group(c, a, name):
        raise group_mod.CliGroupError("group 'direction' not found")
    monkeypatch.setattr(group_mod, "_resolve_group", resolve_group)
    assert group_mod._cmd_remove_member(args) == 1
    assert "not found" in capsys.readouterr().out


def test_cmd_messages_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", json=True)
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))
    fake = type("C", (), {"get_group_messages": lambda self, *a, **k: {
        "messages": [], "next_cursor": None}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_messages(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["messages"] == []


def test_cmd_messages_human_with_cursor(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", cursor="c1")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))
    fake = type("C", (), {"get_group_messages": lambda self, *a, **k: {
        "messages": [{"created_at": "t", "sender_username": "bob",
                      "content": "hi"}],
        "next_cursor": "c2"}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_messages(args) == 0
    out = capsys.readouterr().out
    assert "hi" in out and "next page: --cursor c2" in out


def test_cmd_messages_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="ghost")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: (_ for _ in ()).throw(
            group_mod.CliGroupError("group 'ghost' not found")))
    assert group_mod._cmd_messages(args) == 1
    assert "not found" in capsys.readouterr().out


def test_cmd_send_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", text="hello", client_message_id="cm-1")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))
    fake = type("C", (), {"send_group_message": lambda self, *a, **k: {
        "message_id": "m-1"}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_send(args) == 0
    assert "Message sent to group 'direction' (m-1)" in capsys.readouterr().out


def test_cmd_send_api_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name="direction", text="hello")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "_resolve_group",
        lambda c, a, name: ("g1", "alice", "p"))

    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(group_mod, "_client",
                        lambda config: type("C", (), {"send_group_message": boom})())
    assert group_mod._cmd_send(args) == 3


def test_cmd_list_human_with_cursor(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name=None, cursor="c1")
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"list_my_groups": lambda self, *a, **k: {
        "groups": [{"group_id": "g1", "name": "direction"}],
        "next_cursor": "c2"}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_list(args) == 0
    out = capsys.readouterr().out
    assert "direction" in out and "next page: --cursor c2" in out


def test_cmd_list_json_and_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(name=None, json=True)
    monkeypatch.setattr(group_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        group_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"list_my_groups": lambda self, *a, **k: {
        "groups": [], "next_cursor": None}})()
    monkeypatch.setattr(group_mod, "_client", lambda config: fake)
    assert group_mod._cmd_list(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["groups"] == []

    # transport error
    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(group_mod, "_client",
                        lambda config: type("C", (), {"list_my_groups": boom})())
    assert group_mod._cmd_list(args) == 3
