"""Unit coverage for the ``event`` and ``message`` CLI groups.

Exercises the command handlers directly with a monkeypatched ``_client``
(no server subprocess) to close real coverage gaps in the human-output and
error branches of ``synapse/cli/event.py`` and ``synapse/cli/message.py``.
"""

from __future__ import annotations

import argparse

from synapse.cli import event as event_mod
from synapse.cli import message as message_mod
from synapse.client import ApiClientError, ClientTransportError


# ---------------------------------------------------------------------------
# event
# ---------------------------------------------------------------------------


def test_event_stream_human_table(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(seq=None, my_name="alice", cursor=None,
                              limit=10, json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(event_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        event_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_events": lambda self, *a, **k: {
        "events": [
            {"seq": 1, "event_type": "message.send", "at": "2026-08-11T00:00:00Z",
             "by_username": "alice"},
            {"seq": 2, "event_type": "org.create", "at": "2026-08-11T00:00:01Z",
             "by_username": "bob"},
        ],
        "next_cursor": "abc",
    }})()
    monkeypatch.setattr(event_mod, "_client", lambda config: fake)
    assert event_mod._cmd_stream(args) == 0
    out = capsys.readouterr().out
    assert "message.send" in out
    assert "org.create" in out
    assert "next page: --cursor abc" in out


def test_event_stream_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(seq=None, my_name="alice", cursor="c1",
                              limit=50, json=True, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(event_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        event_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_events": lambda self, *a, **k: {"events": [],
                                                               "next_cursor": None}})()
    monkeypatch.setattr(event_mod, "_client", lambda config: fake)
    assert event_mod._cmd_stream(args) == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["events"] == []


def test_event_stream_seq_unsupported(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(seq="5", my_name="alice", cursor=None,
                              limit=10, json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(event_mod, "resolve_config", lambda a: config)
    assert event_mod._cmd_stream(args) == 1
    out = capsys.readouterr().out
    assert "--seq" in out


def test_event_stream_api_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(seq=None, my_name="alice", cursor=None,
                              limit=10, json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(event_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        event_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))

    def boom(self, *a, **k):
        raise ApiClientError("SERVER_ERROR", "nope")

    monkeypatch.setattr(event_mod, "_client", lambda config: type("C", (), {"get_events": boom})())
    assert event_mod._cmd_stream(args) == 1
    out = capsys.readouterr().out
    assert "nope" in out


def test_event_retention_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(days=90, json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(event_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        event_mod, "resolve_org_auth", lambda c, a: ("root_org", "pw"))
    fake = type("C", (), {"set_event_retention_days": lambda self, *a, **k: {
        "retention_days": 90}})()
    monkeypatch.setattr(event_mod, "_client", lambda config: fake)
    assert event_mod._cmd_retention(args) == 0
    assert "Event retention set to 90 days" in capsys.readouterr().out


def test_event_retention_transport_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(days=90, json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(event_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        event_mod, "resolve_org_auth", lambda c, a: ("root_org", "pw"))

    def boom(self, *a, **k):
        raise ClientTransportError("down")

    monkeypatch.setattr(event_mod, "_client", lambda config: type("C", (), {"set_event_retention_days": boom})())
    assert event_mod._cmd_retention(args) == 3


# ---------------------------------------------------------------------------
# message
# ---------------------------------------------------------------------------


def test_cmd_send_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(recipient="bob", text="hi", my_name="alice",
                              client_message_id="m-1", json=False,
                              config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"send_message": lambda self, *a, **k: {
        "recipient_username": "bob", "message_id": "m-1"}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_send(args) == 0
    assert "Message sent to bob (m-1)" in capsys.readouterr().out


def test_cmd_send_generates_client_id(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(recipient="bob", text="hi", my_name="alice",
                              client_message_id=None, json=True,
                              config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    seen = {}

    def send(self, recipient, text, client_message_id, my, pw):
        seen["cid"] = client_message_id
        return {"recipient_username": "bob", "message_id": client_message_id}

    monkeypatch.setattr(message_mod, "_client", lambda config: type("C", (), {"send_message": send})())
    assert message_mod._cmd_send(args) == 0
    assert seen["cid"]  # a client id was generated
    assert len(seen["cid"]) == 36  # uuid4 hex with dashes


def test_cmd_inbox_human_with_cursor(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(my_name="alice", limit=10, cursor="c",
                              unread=True, sender="bob", json=False,
                              config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_messages": lambda self, *a, **k: {
        "messages": [{"created_at": "2026-08-11T00:00:00Z", "sender_username": "bob",
                      "content": "hi"}],
        "next_cursor": "c2"}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_inbox(args) == 0
    out = capsys.readouterr().out
    assert "bob" in out and "hi" in out
    assert "next page: --cursor c2" in out


def test_cmd_inbox_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(my_name="alice", limit=50, cursor=None,
                              unread=False, sender=None, json=True,
                              config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_messages": lambda self, *a, **k: {
        "messages": [], "next_cursor": None}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_inbox(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["messages"] == []


def test_cmd_conversation_human(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(other="bob", my_name="alice", limit=10,
                              cursor=None, json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_conversation": lambda self, *a, **k: {
        "reply_status": "awaiting", "messages": [
            {"created_at": "t", "sender_username": "alice", "content": "a"},
        ], "next_cursor": "nx2"}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_conversation(args) == 0
    out = capsys.readouterr().out
    assert "Conversation with 'bob'" in out
    assert "awaiting" in out
    assert "next page: --cursor nx2" in out


def test_cmd_read_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(message_id="m-9", my_name="alice",
                              json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"read_message": lambda self, *a, **k: {"ok": True}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_read(args) == 0
    assert "Message m-9 marked as read" in capsys.readouterr().out


def test_cmd_mark_no_reply_success_and_empty(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(other="bob", my_name="alice",
                              json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))

    calls = {"n": 0}

    class Fake:
        def get_conversation(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"messages": [{"conversation_id": "c9"}]}
            return {"messages": []}

        def mark_conversation_no_reply(self, *a, **k):
            return {"ok": True}

    monkeypatch.setattr(message_mod, "_client", lambda config: Fake())
    # With a conversation present.
    assert message_mod._cmd_mark_no_reply(args) == 0
    assert "marked as no-reply" in capsys.readouterr().out
    # Second call: no conversation -> error path.
    assert message_mod._cmd_mark_no_reply(args) == 1
    assert "no conversation with 'bob'" in capsys.readouterr().out


def test_cmd_notifications_all_branches(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(my_name="alice", limit=50,
                              json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_notifications": lambda self, *a, **k: {
        "needs_reply": [{"other_username": "bob", "unread_count": 3}],
        "unread_by_sender": {"carol": 1}}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_notifications(args) == 0
    out = capsys.readouterr().out
    assert "bob" in out and "carol" in out


def test_cmd_notifications_empty(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(my_name="alice", limit=50,
                              json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_notifications": lambda self, *a, **k: {
        "needs_reply": [], "unread_by_sender": {}}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_notifications(args) == 0
    assert "no notifications" in capsys.readouterr().out


def test_cmd_notifications_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(my_name="alice", limit=50,
                              json=True, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_notifications": lambda self, *a, **k: {
        "needs_reply": [], "unread_by_sender": {}}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_notifications(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"] == {"needs_reply": [],
                                                           "unread_by_sender": {}}


def test_cmd_message_api_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(recipient="bob", text="hi", my_name="alice",
                              client_message_id="m-1", json=False,
                              config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))

    def boom(self, *a, **k):
        raise ClientTransportError("down")

    monkeypatch.setattr(message_mod, "_client", lambda config: type("C", (), {"send_message": boom})())
    assert message_mod._cmd_send(args) == 3


def test_cmd_inbox_api_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(my_name="alice", limit=10, cursor=None,
                              unread=False, sender=None, json=False,
                              config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))

    def boom(self, *a, **k):
        raise ApiClientError("DENIED", "no")

    monkeypatch.setattr(message_mod, "_client", lambda config: type("C", (), {"get_messages": boom})())
    assert message_mod._cmd_inbox(args) == 1
    assert "no" in capsys.readouterr().out


def test_cmd_conversation_json_and_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    # JSON branch
    args = argparse.Namespace(other="bob", my_name="alice", limit=10,
                              cursor=None, json=True, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))
    fake = type("C", (), {"get_conversation": lambda self, *a, **k: {
        "reply_status": "awaiting", "messages": [], "next_cursor": "nx"}})()
    monkeypatch.setattr(message_mod, "_client", lambda config: fake)
    assert message_mod._cmd_conversation(args) == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["next_cursor"] == "nx"

    # Error branch
    args2 = argparse.Namespace(other="bob", my_name="alice", limit=10,
                               cursor=None, json=False, config=str(_conf_file(tmp_path)))

    def boom(self, *a, **k):
        raise ApiClientError("DENIED", "no conv")

    monkeypatch.setattr(message_mod, "_client", lambda config: type("C", (), {"get_conversation": boom})())
    assert message_mod._cmd_conversation(args2) == 1
    assert "no conv" in capsys.readouterr().out


def test_cmd_read_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(message_id="m-9", my_name="alice",
                              json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))

    def boom(self, *a, **k):
        raise ApiClientError("DENIED", "no read")

    monkeypatch.setattr(message_mod, "_client", lambda config: type("C", (), {"read_message": boom})())
    assert message_mod._cmd_read(args) == 1
    assert "no read" in capsys.readouterr().out


def test_cmd_mark_no_reply_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(other="bob", my_name="alice",
                              json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))

    class Fake:
        def get_conversation(self, *a, **k):
            return {"messages": [{"conversation_id": "c9"}]}

        def mark_conversation_no_reply(self, *a, **k):
            raise ApiClientError("DENIED", "no mark")

    monkeypatch.setattr(message_mod, "_client", lambda config: Fake())
    assert message_mod._cmd_mark_no_reply(args) == 1
    assert "no mark" in capsys.readouterr().out


def test_cmd_notifications_error(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(my_name="alice", limit=50,
                              json=False, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(message_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        message_mod, "resolve_identity", lambda c, a, my_name=None: ("alice", "pw"))

    def boom(self, *a, **k):
        raise ClientTransportError("down")

    monkeypatch.setattr(message_mod, "_client", lambda config: type("C", (), {"get_notifications": boom})())
    assert message_mod._cmd_notifications(args) == 3


def test_event_stream_client_helpers_and_retention_json(tmp_path, monkeypatch, capsys):
    # _client() helper and retention JSON branch.
    config = _config(tmp_path)

    args = argparse.Namespace(days=90, json=True, config=str(_conf_file(tmp_path)))
    monkeypatch.setattr(event_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        event_mod, "resolve_org_auth", lambda c, a: ("root_org", "pw"))
    fake = type("C", (), {"set_event_retention_days": lambda self, *a, **k: {
        "retention_days": 90}})()
    monkeypatch.setattr(event_mod, "_client", lambda config: fake)
    assert event_mod._cmd_retention(args) == 0
    import json
    assert json.loads(capsys.readouterr().out)["data"]["retention_days"] == 90


def _conf_file(tmp_path):
    return tmp_path / "conf.json"


def _config(tmp_path):
    from synapse.config import Config
    conf = {
        "storage_dir": str(tmp_path / "d"),
        "socket_path": str(tmp_path / "s.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    return Config.from_dict(conf)
