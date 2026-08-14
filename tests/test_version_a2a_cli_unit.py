"""Unit coverage for ``synapse/version.py`` and the ``a2a`` CLI status group.

``version.py``: single source of truth for the project version — tests the
installed-metadata path and the pyproject fallback.
``cli/a2a.py``: tests the port resolution and the status command's
stopped/running/degraded branches with a monkeypatched transport probe.
"""

from __future__ import annotations

import argparse

from synapse import version as ver_mod
from synapse.cli import a2a as a2a_mod


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def test_project_version_via_pyproject(monkeypatch):
    from importlib.metadata import PackageNotFoundError

    def boom(*a, **k):
        raise PackageNotFoundError("x")
    monkeypatch.setattr(ver_mod.importlib.metadata, "version", boom)
    v = ver_mod.project_version()
    assert isinstance(v, str)
    assert len(v) >= 1


def test_from_pyproject_parses_version():
    # pyproject.toml exists in this repo and declares a version.
    text = ver_mod._PYPROJECT.read_text(encoding="utf-8")
    assert 'version = "' in text
    v = ver_mod._from_pyproject()
    assert v and v != ""


def test_from_pyproject_oserror(monkeypatch, tmp_path):
    fake = tmp_path / "pyproject.toml"
    def read(self, *a, **k):
        raise OSError("no")
    monkeypatch.setattr(ver_mod.Path, "read_text", read)
    monkeypatch.setattr(ver_mod, "_PYPROJECT", fake)
    assert ver_mod._from_pyproject() == ""


def test_from_pyproject_no_version_match(monkeypatch, tmp_path):
    fake = tmp_path / "pyproject.toml"
    fake.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    monkeypatch.setattr(ver_mod, "_PYPROJECT", fake)
    assert ver_mod._from_pyproject() == ""


def test_project_version_installed_metadata(monkeypatch):
    monkeypatch.setattr(ver_mod.importlib.metadata, "version",
                        lambda pkg: "9.9.9")
    assert ver_mod.project_version() == "9.9.9"


# ---------------------------------------------------------------------------
# a2a cli: port resolution
# ---------------------------------------------------------------------------


def test_resolve_a2a_port_priority(monkeypatch):
    args = argparse.Namespace(port=8444)
    assert a2a_mod._resolve_a2a_port(args) == 8444
    monkeypatch.delenv("SYNAPSE_A2A_PORT", raising=False)
    args2 = argparse.Namespace(port=None)
    assert a2a_mod._resolve_a2a_port(args2) == 8090


def test_resolve_a2a_port_env(monkeypatch):
    args = argparse.Namespace(port=None)
    monkeypatch.setenv("SYNAPSE_A2A_PORT", "8500")
    assert a2a_mod._resolve_a2a_port(args) == 8500


def test_resolve_a2a_port_env_invalid(monkeypatch):
    args = argparse.Namespace(port=None)
    monkeypatch.setenv("SYNAPSE_A2A_PORT", "not-a-port")
    assert a2a_mod._resolve_a2a_port(args) == 8090


# ---------------------------------------------------------------------------
# a2a cli: status command
# ---------------------------------------------------------------------------


def _a2a_args(json=False):
    return argparse.Namespace(json=json)


def _config(tmp_path):
    from synapse.config import Config
    conf = {
        "storage_dir": str(tmp_path / "d"),
        "socket_path": str(tmp_path / "s.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    return Config.from_dict(conf)


def test_cmd_status_stopped(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(a2a_mod, "read_pid_file", lambda c, n: None)
    monkeypatch.setattr(a2a_mod, "_alive", lambda pid: False)
    monkeypatch.setattr(a2a_mod, "http_get", lambda *a, **k: (0, None))
    assert a2a_mod._cmd_status(_a2a_args()) == 0
    assert "A2A bridge stopped" in capsys.readouterr().out


def test_cmd_status_running(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        a2a_mod, "read_pid_file",
        lambda c, n: {"pid": 4242, "port": 8090, "agent_name": "alice",
                      "started_at": "t", "version": "1.0"})
    monkeypatch.setattr(a2a_mod, "_alive", lambda pid: True)
    monkeypatch.setattr(a2a_mod, "http_get", lambda *a, **k: (200, None))
    assert a2a_mod._cmd_status(_a2a_args()) == 0
    out = capsys.readouterr().out
    assert "A2A bridge running (PID 4242)" in out
    assert "alice" in out


def test_cmd_status_degraded(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        a2a_mod, "read_pid_file",
        lambda c, n: {"pid": 4243, "port": 8090, "agent_name": "bob",
                      "started_at": "t", "version": "1.0"})
    monkeypatch.setattr(a2a_mod, "_alive", lambda pid: True)
    monkeypatch.setattr(a2a_mod, "http_get", lambda *a, **k: (503, None))
    assert a2a_mod._cmd_status(_a2a_args()) == 0
    assert "DEGRADED (PID 4243)" in capsys.readouterr().out


def test_cmd_status_json(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(
        a2a_mod, "read_pid_file",
        lambda c, n: {"pid": 4244, "port": 8091, "agent_name": "carol",
                      "started_at": "t", "version": "1.0"})
    monkeypatch.setattr(a2a_mod, "_alive", lambda pid: True)
    monkeypatch.setattr(a2a_mod, "http_get", lambda *a, **k: (200, None))
    assert a2a_mod._cmd_status(_a2a_args(json=True)) == 0
    import json
    payload = json.loads(capsys.readouterr().out)["data"]
    assert payload["state"] == "running"
    assert payload["port"] == 8091
    assert payload["agent_name"] == "carol"


def test_cmd_start_missing_service(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(config=str(tmp_path / "c.json"))
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(a2a_mod, "socket_responds", lambda c: False)
    import pytest
    with pytest.raises(SystemExit) as exc:
        a2a_mod._cmd_start(args)
    assert exc.value.code == 3
    out = capsys.readouterr().out
    assert "service not ready" in out


def test_cmd_start_empty_password(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(config=str(tmp_path / "c.json"), agent_name="a",
                              password_stdin=True, token_stdin=True,
                              foreground=False, port=None, log_level=None)
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(a2a_mod, "socket_responds", lambda c: True)
    monkeypatch.setattr(a2a_mod.sys, "stdin", _Stdin(""))
    assert a2a_mod._cmd_start(args) == 1
    assert "empty agent password" in capsys.readouterr().out


def test_cmd_start_empty_token(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(config=str(tmp_path / "c.json"), agent_name="a",
                              password_stdin=True, token_stdin=True,
                              foreground=False, port=None, log_level=None)
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(a2a_mod, "socket_responds", lambda c: True)
    monkeypatch.setattr(a2a_mod.sys, "stdin", _Stdin("secretpw\n"))
    assert a2a_mod._cmd_start(args) == 1
    assert "access token is required" in capsys.readouterr().out


def test_cmd_start_already_running(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(config=str(tmp_path / "c.json"), agent_name="a",
                              password_stdin=True, token_stdin=True,
                              foreground=False, port=8090, log_level=None)
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(a2a_mod, "socket_responds", lambda c: True)
    monkeypatch.setattr(a2a_mod.sys, "stdin", _Stdin("pw\nTOKEN123\n"))
    monkeypatch.setattr(a2a_mod, "read_pid_file",
                        lambda c, n: {"pid": 4242, "port": 8090})
    monkeypatch.setattr(a2a_mod, "_alive", lambda pid: True)
    monkeypatch.setattr(a2a_mod, "_a2a_responding", lambda c, p: True)
    assert a2a_mod._cmd_start(args) == 0
    assert "already running" in capsys.readouterr().out


def test_cmd_start_stale_pid_then_foreground(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(config=str(tmp_path / "c.json"), agent_name="a",
                              password_stdin=True, token_stdin=True,
                              foreground=True, port=8090, log_level=None)
    monkeypatch.setattr(a2a_mod, "resolve_config", lambda a: config)
    monkeypatch.setattr(a2a_mod, "socket_responds", lambda c: True)
    monkeypatch.setattr(a2a_mod.sys, "stdin", _Stdin("pw\nTOKEN123\n"))
    monkeypatch.setattr(a2a_mod, "read_pid_file",
                        lambda c, n: {"pid": 4242, "port": 8090})
    monkeypatch.setattr(a2a_mod, "_alive", lambda pid: True)
    monkeypatch.setattr(a2a_mod, "_a2a_responding", lambda c, p: False)
    monkeypatch.setattr(a2a_mod, "_run_foreground", lambda c, a, p, t: 0)
    assert a2a_mod._cmd_start(args) == 0
    assert "stale A2A PID file" in capsys.readouterr().out


class _Stdin:
    def __init__(self, text):
        self._lines = iter(text.splitlines())

    def readline(self):
        return next(self._lines, "")
