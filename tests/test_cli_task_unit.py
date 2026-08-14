"""Unit coverage for the ``task`` CLI group handlers.

Monkeypatches ``_client`` / ``resolve_identity`` to exercise the
human-output and error branches of ``synapse/cli/task.py`` without a server.
"""

from __future__ import annotations

import argparse

from synapse.cli import task as task_mod
from synapse.client import ApiClientError, ClientTransportError


def _args(**kw):
    base = {"task_id": None, "state": None, "assignee": None, "department": None,
            "title": None, "priority": None, "due": None, "description": None,
            "creator": None, "result": None, "reason": None, "approver": None,
            "note": None, "my_name": None, "limit": 50, "cursor": None,
            "json": False, "config": "/tmp/x/c.json"}
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


def test_state_and_priority_mapping():
    assert task_mod._state("en_cours") == "in_progress"
    assert task_mod._state("in_progress") == "in_progress"
    assert task_mod._state("unknown") == "unknown"
    assert task_mod._priority("haute") == "high"
    assert task_mod._priority("high") == "high"


def test_cmd_list_plain_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(state="en_cours", assignee="alice", cursor="c1")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    calls = {}

    def list_tasks(self, my, pw, **kw):
        calls.update(kw)
        return {"tasks": [{"task_id": "t1", "title": "X", "state": "in_progress",
                           "assignee_username": "alice"}],
                "next_cursor": "c2"}

    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"list_tasks": list_tasks})())
    assert task_mod._cmd_list(args) == 0
    out = capsys.readouterr().out
    assert "X" in out and "next page: --cursor c2" in out
    assert calls["state"] == "in_progress"  # FR state translated


def test_cmd_list_department(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(department="finance")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    seen = {}

    def list_dept(self, *a, **k):
        seen.update(k={"department": a[0]})
        return {"tasks": [], "next_cursor": None}

    fake = type("C", (), {"list_department_tasks": list_dept})()
    monkeypatch.setattr(task_mod, "_client", lambda config: fake)
    assert task_mod._cmd_list(args) == 0
    assert seen["k"]["department"] == "finance"


def test_cmd_list_json_and_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(json=True)
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"list_tasks": lambda self, *a, **k: {
        "tasks": [], "next_cursor": None}})()
    monkeypatch.setattr(task_mod, "_client", lambda config: fake)
    assert task_mod._cmd_list(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["tasks"] == []

    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"list_tasks": boom})())
    assert task_mod._cmd_list(args) == 3


def test_cmd_create_creator_refused(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(title="X", assignee="bob", creator="evil")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    assert task_mod._cmd_create(args) == 1
    assert "--creator" in capsys.readouterr().out


def test_cmd_create_department_refused(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(title="X", assignee="bob", department="finance")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    assert task_mod._cmd_create(args) == 1
    assert "--department" in capsys.readouterr().out


def test_cmd_create_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(title="Report", assignee="bob", priority="haute",
                 due="2026-08-11T10:00:00Z", description="d")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    seen = {}

    def create(self, title, assignee, my, pw, **kw):
        seen.update(kw)
        return {"task_id": "t-1", "state": "submitted"}

    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"create_task": create})())
    assert task_mod._cmd_create(args) == 0
    out = capsys.readouterr().out
    assert "Task created: t-1" in out
    assert seen["priority"] == "high"        # FR priority translated
    assert seen["due_at"] == "2026-08-11T10:00:00.000Z"  # normalized


def test_cmd_create_api_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(title="X", assignee="bob")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))

    def boom(self, *a, **k):
        raise ApiClientError("DENIED", "no")
    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"create_task": boom})())
    assert task_mod._cmd_create(args) == 1
    assert "no" in capsys.readouterr().out


def test_cmd_status_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(task_id="t-1")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"get_task": lambda self, *a, **k: {
        "task_id": "t-1", "state": "in_progress"}})()
    monkeypatch.setattr(task_mod, "_client", lambda config: fake)
    assert task_mod._cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "t-1" in out and "in_progress" in out


def test_cmd_update_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(task_id="t-1", state="en_cours", result="done")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    seen = {}

    def update(self, tid, state, my, pw, **kw):
        seen.update({"state": state, **kw})
        return {"state": "in_progress"}

    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"update_task_state": update})())
    assert task_mod._cmd_update(args) == 0
    assert seen["state"] == "in_progress"
    assert "Task t-1: state in_progress" in capsys.readouterr().out


def test_cmd_approve_reject(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    a1 = _args(task_id="t-1")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"approve_task": lambda self, *a, **k: {"state": "completed"},
                          "reject_task": lambda self, *a, **k: {"state": "failed"}})()
    monkeypatch.setattr(task_mod, "_client", lambda config: fake)
    assert task_mod._cmd_approve(a1) == 0
    assert "Task t-1 approved" in capsys.readouterr().out
    a2 = _args(task_id="t-1", reason="bad")
    assert task_mod._cmd_reject(a2) == 0
    assert "Task t-1 rejected" in capsys.readouterr().out

    # error branches
    def boom(self, *a, **k):
        raise ApiClientError("DENIED", "no")
    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"approve_task": boom})())
    assert task_mod._cmd_approve(a1) == 1
    assert "no" in capsys.readouterr().out


def test_cmd_request_approval_and_transfer(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    a1 = _args(task_id="t-1", approver="directeur")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {
        "request_approval": lambda self, *a, **k: {},
        "transfer_task": lambda self, *a, **k: {}})()
    monkeypatch.setattr(task_mod, "_client", lambda config: fake)
    assert task_mod._cmd_request_approval(a1) == 0
    assert "Approval requested for t-1" in capsys.readouterr().out
    a2 = _args(task_id="t-1", assignee="support", note="here")
    assert task_mod._cmd_transfer(a2) == 0
    assert "Task t-1 transferred to support" in capsys.readouterr().out

    # error branches
    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"request_approval": boom})())
    assert task_mod._cmd_request_approval(a1) == 3
    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"transfer_task": boom})())
    assert task_mod._cmd_transfer(a2) == 3


def test_cmd_my_work_human_and_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(cursor="c1")
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"get_my_work": lambda self, *a, **k: {
        "work_items": [{"task_id": "t9", "title": "Z", "state": "submitted",
                        "due_at": "d"}],
        "next_cursor": "c2"}})()
    monkeypatch.setattr(task_mod, "_client", lambda config: fake)
    assert task_mod._cmd_my_work(args) == 0
    out = capsys.readouterr().out
    assert "Z" in out and "next page: --cursor c2" in out

    def boom(self, *a, **k):
        raise ClientTransportError("down")
    monkeypatch.setattr(task_mod, "_client",
                        lambda config: type("C", (), {"get_my_work": boom})())
    assert task_mod._cmd_my_work(args) == 3


def test_cmd_my_work_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _args(json=True)
    monkeypatch.setattr(task_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        task_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "p"))
    fake = type("C", (), {"get_my_work": lambda self, *a, **k: {
        "work_items": [], "next_cursor": None}})()
    monkeypatch.setattr(task_mod, "_client", lambda config: fake)
    assert task_mod._cmd_my_work(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["work_items"] == []
