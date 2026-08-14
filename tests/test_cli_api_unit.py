"""Unit coverage for the ``api`` CLI group (raw command access).

Tests ``_coerce`` value conversion, ``run_raw`` token parsing, and the
``run_api`` authentication/error branches with monkeypatched client +
auth helpers (no server subprocess).
"""

from __future__ import annotations

import argparse
import json

import pytest

from synapse.cli import api as api_mod
from synapse.client import ApiClientError, ClientTransportError


def _config(tmp_path):
    from synapse.config import Config
    conf = {
        "storage_dir": str(tmp_path / "d"),
        "socket_path": str(tmp_path / "s.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    return Config.from_dict(conf)


def _patch_common(monkeypatch, **kw):
    """api.py imports these names from .common locally inside functions,
    so patch the common module (single source of each name)."""
    from synapse.cli import common as _common
    for name, value in kw.items():
        monkeypatch.setattr(_common, name, value)
    return _common


def test_coerce_variants():
    assert api_mod._coerce("true") is True
    assert api_mod._coerce("yes") is True
    assert api_mod._coerce("1") is True
    assert api_mod._coerce("false") is False
    assert api_mod._coerce("no") is False
    assert api_mod._coerce("0") is False
    assert api_mod._coerce("42") == 42
    assert api_mod._coerce("hello") == "hello"
    assert api_mod._coerce("") == ""


def test_cmd_api_guard(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    args = argparse.Namespace(config=str(tmp_path / "c.json"),
                              api_command="help", api_params=None,
                              params=[], json=False, my_name="alice",
                              organization_name=None, password_stdin=False)
    monkeypatch.setattr(api_mod, "run_api", lambda *a, **k: 0)
    monkeypatch.setattr(_patch_common(monkeypatch), "resolve_config", lambda a: config)
    assert api_mod._cmd_api(args) == 0


def test_run_raw_no_tokens_prints_examples(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "resolve_config",
                        lambda *a, **k: config)
    assert api_mod.run_raw([], prefix=["--config", "/tmp/x/c.json"]) == 0
    assert "Examples:" in capsys.readouterr().out


def test_run_raw_empty_prefix_and_help(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "resolve_config",
                        lambda *a, **k: config)
    assert api_mod.run_raw(["--json"], prefix=[]) == 0
    assert "Examples:" in capsys.readouterr().out


def test_run_raw_config_load_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(_patch_common(monkeypatch), "resolve_config", lambda a: _config(tmp_path))
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: "tok")
    monkeypatch.setattr(api_mod, "_unique_org", lambda c, t: "acme")
    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {
                            "request": lambda self, c, p: {}})()))
    rc = api_mod.run_raw(["help"], prefix=["--config", "/no/such/config.json"])
    assert rc == 0


def test_run_api_requires_service(monkeypatch, capsys):
    from pathlib import Path
    config = _config(Path("/tmp/x"))
    from synapse.cli.common import CliError
    def boom(c):
        raise CliError("service unavailable: x", code=3)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", boom)
    with pytest.raises(CliError) as exc:
        api_mod.run_api(config, command="help", raw_tokens=[],
                        json_out=False, my_name=None,
                        organization_name=None, password_stdin=False)
    assert exc.value.code == 3


def test_run_api_unknown_command_needs_identity(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: None)
    # unknown command -> non-org branch -> my_name None + no token -> error
    rc = api_mod.run_api(config, command="no_such_cmd", raw_tokens=[],
                         json_out=False, my_name=None,
                         organization_name=None, password_stdin=False)
    assert rc == 1
    assert "identity required" in capsys.readouterr().out


def test_run_api_org_command_needs_org(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: None)
    # get_org_metrics is an org command (spec[2] truthy), no org name/token
    rc = api_mod.run_api(config, command="get_org_metrics", raw_tokens=[],
                         json_out=False, my_name=None,
                         organization_name=None, password_stdin=False)
    assert rc == 1
    assert "--organization-name required" in capsys.readouterr().out


def test_run_api_params_parsing_unexpected(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: None)
    # org command with a bare non-option token -> unexpected parameter
    rc = api_mod.run_api(config, command="get_org_metrics",
                         raw_tokens=["bareword"],
                         json_out=False, my_name=None,
                         organization_name=None, password_stdin=False)
    assert rc == 1
    assert "unexpected parameter" in capsys.readouterr().out


def test_run_api_org_command_success(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: "tok")
    monkeypatch.setattr(api_mod, "_unique_org", lambda c, t: "acme")
    seen = {}

    def request(self, command, params):
        seen.update(params)
        return {"metrics": "ok"}

    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {"request": request})()))
    rc = api_mod.run_api(config, command="get_org_metrics", raw_tokens=[],
                         json_out=False, my_name=None,
                         organization_name=None, password_stdin=False)
    assert rc == 0
    assert seen["organization_name_auth"] == "acme"
    assert seen["organization_password_auth"] == "tok"
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["metrics"] == "ok"


def test_run_api_account_human_via_token(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: "tok")
    monkeypatch.setattr(api_mod, "_unique_org", lambda c, t: "acme")
    seen = {}

    def request(self, command, params):
        seen.update(params)
        return {}

    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {"request": request})()))
    rc = api_mod.run_api(config, command="send_message", raw_tokens=[],
                         json_out=False, my_name=None,
                         organization_name=None, password_stdin=False)
    assert rc == 0
    assert seen["my_name_auth"] == "acme_humain"
    assert seen["my_password_auth"] == "tok"


def test_run_api_agent_identity(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: None)
    monkeypatch.setattr(api_mod, "read_password", lambda args, prompt: "secret")
    seen = {}

    def request(self, command, params):
        seen.update(params)
        return {}

    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {"request": request})()))
    rc = api_mod.run_api(config, command="send_message", raw_tokens=[],
                         json_out=False, my_name="alice",
                         organization_name=None, password_stdin=True)
    assert rc == 0
    assert seen["my_name_auth"] == "alice"
    assert seen["my_password_auth"] == "secret"


def test_run_api_error_mapping(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: "tok")
    monkeypatch.setattr(api_mod, "_unique_org", lambda c, t: "acme")

    def boom(self, command, params):
        raise ApiClientError("DENIED", "not allowed")
    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {"request": boom})()))
    rc = api_mod.run_api(config, command="get_org_metrics", raw_tokens=[],
                         json_out=False, my_name=None,
                         organization_name=None, password_stdin=False)
    assert rc == 1
    assert "not allowed" in capsys.readouterr().out

    def boom2(self, command, params):
        raise ClientTransportError("down")
    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {"request": boom2})()))
    assert api_mod.run_api(config, command="get_org_metrics", raw_tokens=[],
                           json_out=False, my_name=None,
                           organization_name=None, password_stdin=False) == 3


def test_unique_org_single(tmp_path, monkeypatch):
    config = _config(tmp_path)
    data = {"organizations": [{"organization_name": "acme"}]}
    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {
                            "list_orgs": lambda self, a, b: data})()))
    assert api_mod._unique_org(config, "tok") == "acme"


def test_unique_org_no_org(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    data = {"organizations": []}
    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {
                            "list_orgs": lambda self, a, b: data})()))
    with pytest.raises(SystemExit):
        api_mod._unique_org(config, "tok")
    assert "no active organization" in capsys.readouterr().out


def test_unique_org_multiple(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    data = {"organizations": [{"organization_name": "a"}, {"organization_name": "b"}]}
    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {
                            "list_orgs": lambda self, a, b: data})()))
    with pytest.raises(SystemExit):
        api_mod._unique_org(config, "tok")
    assert "multiple active organizations" in capsys.readouterr().out


def test_fill_secret_params_rejects_password_arg(tmp_path, monkeypatch, capsys):
    # A command with a password param, provided on the command line.
    config = _config(tmp_path)
    monkeypatch.setattr(_patch_common(monkeypatch), "require_service", lambda c: None)
    monkeypatch.setattr(api_mod, "read_web_token", lambda c: "tok")
    monkeypatch.setattr(api_mod, "_unique_org", lambda c, t: "acme")

    def request(self, command, params):
        return {}
    monkeypatch.setattr(api_mod.Client, "from_config",
                        classmethod(lambda cls, config: type("C", (), {"request": request})()))
    with pytest.raises(SystemExit):
        api_mod.run_api(config, command="change_organization_password",
                        raw_tokens=["--new-password", "secretpw"],
                        json_out=False, my_name=None,
                        organization_name=None, password_stdin=False)
    assert "password forbidden" in capsys.readouterr().out
