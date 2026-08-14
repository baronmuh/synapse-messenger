"""Unit coverage for the ``status`` and ``logs`` CLI groups.

These tests exercise the pure display/state functions directly with
monkeypatched I/O and PID files — no server subprocess needed, so they are
cheap and deterministic. They close real coverage gaps in
``synapse/cli/status.py`` and ``synapse/cli/logs.py`` (branches for the
``stopped``/``running``/``degraded`` states and for the merged-log tail).
"""

from __future__ import annotations

import argparse
import json


from synapse.cli import logs as logs_mod
from synapse.cli import status as status_mod
from synapse.cli.common import read_pid_file, write_pid_file

# ---------------------------------------------------------------------------
# logs.tail_log
# ---------------------------------------------------------------------------


def test_tail_log_single_file(tmp_path, capsys):
    f = tmp_path / "synapse.log"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    assert logs_mod.tail_log(str(f), lines=2) == 0
    out = capsys.readouterr().out
    assert "line1" not in out
    assert "line2" in out and "line3" in out


def test_tail_log_absent_files(tmp_path, capsys):
    assert logs_mod.tail_log([str(tmp_path / "a.log")], lines=10) == 0
    out = capsys.readouterr().out
    assert "log file absent" in out


def test_tail_log_empty_paths_list(tmp_path, capsys):
    # Empty path list -> no absent-file lines, no crash.
    assert logs_mod.tail_log([], lines=10) == 0
    assert capsys.readouterr().out == ""


def test_tail_log_merged_sorted_by_timestamp(tmp_path, capsys):
    web = tmp_path / "web.log"
    srv = tmp_path / "synapse.log"
    web.write_text(
        json.dumps({"timestamp": "2026-08-11T10:00:01Z", "m": "web"}) + "\n",
        encoding="utf-8",
    )
    srv.write_text(
        json.dumps({"timestamp": "2026-08-11T10:00:00Z", "m": "srv"}) + "\n",
        encoding="utf-8",
    )
    assert logs_mod.tail_log([str(web), str(srv)], lines=10) == 0
    out = capsys.readouterr().out
    # Server line (earlier) must come first in the merged sort.
    assert out.index("srv") < out.index("web")


def test_tail_log_lines_zero_returns_all(tmp_path, capsys):
    f = tmp_path / "all.log"
    f.write_text("\n".join(f"x{i}" for i in range(5)) + "\n", encoding="utf-8")
    assert logs_mod.tail_log(str(f), lines=0) == 0
    out = capsys.readouterr().out
    for i in range(5):
        assert f"x{i}" in out


def test_tail_log_follow_handles_keyboard_interrupt(tmp_path, monkeypatch, capsys):
    f = tmp_path / "f.log"
    f.write_text("first\n", encoding="utf-8")

    # sleep runs only inside the follow try block -> raising there is caught.
    def fake_sleep(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(logs_mod.time, "sleep", fake_sleep)
    assert logs_mod.tail_log(str(f), lines=10, follow=True) == 0
    assert "first" in capsys.readouterr().out


def test_tail_log_follow_appends_new_lines(tmp_path, monkeypatch, capsys):
    f = tmp_path / "g.log"
    f.write_text("a\nb\n", encoding="utf-8")

    # First sleep returns (letting one loop read a growing file),
    # the second raises KeyboardInterrupt inside the try block.
    sleeps = {"n": 0}

    def fake_sleep(_s):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(logs_mod.time, "sleep", fake_sleep)
    assert logs_mod.tail_log(str(f), lines=10, follow=True) == 0
    out = capsys.readouterr().out
    assert "a\n" in out and "b\n" in out


def test_line_key_json_and_plain():
    assert logs_mod._line_key(json.dumps({"timestamp": "2026-08-11T01:02:03Z"})) == \
        "2026-08-11T01:02:03Z"
    # Non-JSON / malformed falls back to the raw line.
    assert logs_mod._line_key("plain text") == "plain text"
    assert logs_mod._line_key("{broken json") == "{broken json"


def test_read_tail_oserror_returns_empty(tmp_path):
    assert logs_mod._read_tail(str(tmp_path / "missing.log"), 5) == []


def test_cmd_logs_service_branches(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "conf.json"
    conf.write_text(
        json.dumps({"storage_dir": str(tmp_path / "d"),
                    "socket_path": str(tmp_path / "s.sock"),
                    "log_dir": str(tmp_path / "logs")}),
        encoding="utf-8",
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "synapse.log").write_text("srv\n", encoding="utf-8")
    (tmp_path / "logs" / "web.log").write_text("webx\n", encoding="utf-8")


    args_server = argparse.Namespace(service="server", lines=5, follow=False,
                                     config=str(conf))
    args_web = argparse.Namespace(service="web", lines=5, follow=False,
                                  config=str(conf))
    args_merged = argparse.Namespace(service=None, lines=5, follow=False,
                                     config=str(conf))
    assert logs_mod._cmd_logs(args_server) == 0
    assert logs_mod._cmd_logs(args_web) == 0
    capsys.readouterr()
    assert logs_mod._cmd_logs(args_merged) == 0
    out = capsys.readouterr().out
    assert "srv" in out and "webx" in out


# ---------------------------------------------------------------------------
# status._web_state / _a2a_state
# ---------------------------------------------------------------------------


def _status_args(json_mode=False):
    return argparse.Namespace(json=json_mode)


def test_web_state_stopped_no_pid(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(status_mod, "read_pid_file", lambda c, n: None)
    monkeypatch.setattr(status_mod, "http_get", lambda *a, **k: (0, ""))
    s = status_mod._web_state(config)
    assert s["state"] == "stopped"
    assert s["pid"] is None


def test_web_state_running_alive_http_ok(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(
        status_mod, "read_pid_file",
        lambda c, n: {"pid": 424242, "port": 9999})
    _patch_pid_alive(monkeypatch, True)
    monkeypatch.setattr(status_mod, "http_get", lambda *a, **k: (200, "ok"))
    s = status_mod._web_state(config)
    assert s["state"] == "running"
    assert s["http"] == 200


def test_web_state_degraded_alive_http_fails(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(
        status_mod, "read_pid_file",
        lambda c, n: {"pid": 424243, "port": 9998})
    _patch_pid_alive(monkeypatch, True)
    monkeypatch.setattr(status_mod, "http_get", lambda *a, **k: (503, "err"))
    s = status_mod._web_state(config)
    assert s["state"] == "degraded"


def test_a2a_state_stopped(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(status_mod, "read_pid_file", lambda c, n: None)
    monkeypatch.setattr(status_mod, "http_get", lambda *a, **k: (0, ""))
    s = status_mod._a2a_state(config)
    assert s["state"] == "stopped"


def test_a2a_state_running_and_degraded(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(
        status_mod, "read_pid_file",
        lambda c, n: {"pid": 424244, "port": 9997, "agent_name": "alice"})
    _patch_pid_alive(monkeypatch, True)
    monkeypatch.setattr(status_mod, "http_get",
                        lambda *a, **k: (200, "ok"))
    s = status_mod._a2a_state(config)
    assert s["state"] == "running"
    assert s["agent_name"] == "alice"

    monkeypatch.setattr(status_mod, "http_get",
                        lambda *a, **k: (503, "err"))
    s = status_mod._a2a_state(config)
    assert s["state"] == "degraded"


def _patch_pid_alive(monkeypatch, value):
    # status.py imports pid_alive locally from .common inside the state
    # helpers, so patch the source module.
    from synapse.cli import common as _common
    monkeypatch.setattr(_common, "pid_alive", lambda pid: value)


def _import_backup():
    # status.py does `from .backup import _header_date` inside _cmd_status,
    # so patch the backup module (single source of the name).
    from synapse.cli import backup as _backup
    return _backup


def test_cmd_status_json_aggregate(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    # Config with a backup_dir to exercise the backup listing branch.
    args = _status_args(json_mode=True)
    monkeypatch.setattr(status_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        status_mod, "service_state",
        lambda c, n: {"state": "running", "pid": 1, "degraded": False,
                      "pid_file": None, "socket_ok": True})
    monkeypatch.setattr(
        status_mod, "_web_state",
        lambda c: {"state": "running", "pid": 2, "pid_file": {"port": 8080},
                   "http": 200})
    monkeypatch.setattr(
        status_mod, "_a2a_state",
        lambda c: {"state": "stopped", "pid": None, "pid_file": None,
                   "http": 0, "agent_name": None})
    monkeypatch.setattr(status_mod, "read_web_token", lambda c: "tok")
    monkeypatch.setattr(
        status_mod.Client, "from_config",
        classmethod(lambda cls, config: type("C", (), {"list_orgs": lambda self, a, b: {
            "organizations": [{"organization_name": "root_org"}]}})()))

    (tmp_path / "backups").mkdir(exist_ok=True)
    (tmp_path / "backups" / "a.synbk").write_bytes(b"x" * 10)

    monkeypatch.setattr(_import_backup(),
                        "_header_date", lambda c, p: "2026-08-11T00:00:00Z")

    assert status_mod._cmd_status(args) == 0
    payload = json.loads(capsys.readouterr().out)["data"]
    assert payload["server"]["state"] == "running"
    assert payload["organizations"] == [{"organization_name": "root_org"}]
    assert payload["backups"] and payload["backups"][0]["name"] == "a.synbk"
    assert payload["backups"][0]["size"] == 10


def test_cmd_status_human_with_backups(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    (tmp_path / "backups").mkdir(exist_ok=True)
    (tmp_path / "backups" / "b.synbk").write_bytes(b"z" * 5)

    args = _status_args(json_mode=False)
    monkeypatch.setattr(status_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        status_mod, "service_state",
        lambda c, n: {"state": "running", "pid": 1, "degraded": False,
                      "pid_file": None, "socket_ok": True})
    monkeypatch.setattr(
        status_mod, "_web_state",
        lambda c: {"state": "running", "pid": 2, "pid_file": {"port": 8080},
                   "http": 200})
    monkeypatch.setattr(
        status_mod, "_a2a_state",
        lambda c: {"state": "running", "pid": 3, "pid_file": {"port": 8090},
                   "http": 200, "agent_name": "alice"})
    monkeypatch.setattr(status_mod, "read_web_token", lambda c: "tok")
    monkeypatch.setattr(
        status_mod.Client, "from_config",
        classmethod(lambda cls, config: type("C", (), {"list_orgs": lambda self, a, b: {
            "organizations": [{"organization_name": "root_org"}]}})()))
    monkeypatch.setattr(_import_backup(), "_header_date",
                        lambda c, p: "2026-08-11T00:00:00Z")

    assert status_mod._cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "=== server ===" in out
    assert "running (PID 2" in out  # web human branch
    assert "A2A gateway" in out
    assert "b.synbk" in out


def test_cmd_status_org_error_when_api_fails(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _status_args(json_mode=True)
    monkeypatch.setattr(status_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        status_mod, "service_state",
        lambda c, n: {"state": "running", "pid": 1, "degraded": False,
                      "pid_file": None, "socket_ok": True})
    monkeypatch.setattr(
        status_mod, "_web_state",
        lambda c: {"state": "stopped", "pid": None, "pid_file": None,
                   "http": 0})
    monkeypatch.setattr(
        status_mod, "_a2a_state",
        lambda c: {"state": "stopped", "pid": None, "pid_file": None,
                   "http": 0, "agent_name": None})
    monkeypatch.setattr(status_mod, "read_web_token", lambda c: "tok")

    from synapse.client import ApiClientError

    def boom(self, a, b):
        raise ApiClientError("SERVER_ERROR", "down")

    monkeypatch.setattr(
        status_mod.Client, "from_config",
        classmethod(lambda cls, config: type("C", (), {"list_orgs": boom})()))
    monkeypatch.setattr(_import_backup(), "_header_date",
                        lambda c, p: "2026-08-11T00:00:00Z")

    assert status_mod._cmd_status(args) == 0
    payload = json.loads(capsys.readouterr().out)["data"]
    assert payload["organizations"] == {"error": "service unreachable"}


def test_cmd_status_human_degraded_stopped_states(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _status_args(json_mode=False)
    monkeypatch.setattr(status_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        status_mod, "service_state",
        lambda c, n: {"state": "degraded", "pid": 1, "degraded": True,
                      "pid_file": None, "socket_ok": False})
    monkeypatch.setattr(
        status_mod, "_web_state",
        lambda c: {"state": "degraded", "pid": 2, "pid_file": {"port": 8080},
                   "http": 503})
    monkeypatch.setattr(
        status_mod, "_a2a_state",
        lambda c: {"state": "degraded", "pid": 3, "pid_file": {"port": 8090},
                   "http": 503, "agent_name": "alice"})
    monkeypatch.setattr(status_mod, "read_web_token", lambda c: None)
    monkeypatch.setattr(_import_backup(), "_header_date",
                        lambda c, p: "2026-08-11T00:00:00Z")

    assert status_mod._cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "DEGRADED (PID 1" in out       # server degraded
    assert "DEGRADED (PID 2" in out       # web degraded
    assert "DEGRADED (PID 3" in out       # a2a degraded
    # token missing -> orgs unavailable
    assert "unavailable (server stopped or token missing)" in out
    assert "recent backups" not in out


def test_cmd_status_human_all_stopped(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = _status_args(json_mode=False)
    monkeypatch.setattr(status_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        status_mod, "service_state",
        lambda c, n: {"state": "stopped", "pid": None, "degraded": False,
                      "pid_file": None, "socket_ok": False})
    monkeypatch.setattr(
        status_mod, "_web_state",
        lambda c: {"state": "stopped", "pid": None, "pid_file": None,
                   "http": 0})
    monkeypatch.setattr(
        status_mod, "_a2a_state",
        lambda c: {"state": "stopped", "pid": None, "pid_file": None,
                   "http": 0, "agent_name": None})
    monkeypatch.setattr(status_mod, "read_web_token", lambda c: None)

    assert status_mod._cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "stopped" in out
    # Optional a2a explanation branch.
    assert "stopped (optional" in out


# ---------------------------------------------------------------------------
# common helpers (cheap pure coverage)
# ---------------------------------------------------------------------------


def test_common_pid_file_roundtrip(tmp_path):
    config = _config(tmp_path)
    write_pid_file(config, "web", {"port": 7777})
    info = read_pid_file(config, "web")
    assert info is not None
    assert info["port"] == 7777
    assert info["version"]
    # Missing / corrupt file -> None.
    assert read_pid_file(config, "does_not_exist") is None


def test_common_normalize_datetime():
    from synapse.cli.common import normalize_datetime
    assert normalize_datetime(None) is None
    assert normalize_datetime("2026-08-11T10:00:00Z") == "2026-08-11T10:00:00.000Z"
    assert normalize_datetime("2026-08-11T10:00:00.123Z") == "2026-08-11T10:00:00.123Z"


def _config(tmp_path):
    from synapse.config import Config
    conf = {
        "storage_dir": str(tmp_path / "d"),
        "socket_path": str(tmp_path / "s.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    return Config.from_dict(conf)
