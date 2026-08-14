"""Unit coverage for ``synapse/cli/main.py`` dispatch logic.

Tests the root entry point's branching (version, bare-synapse, api raw
routing, _daemon, error mapping) with monkeypatched handlers — no server.
"""

from __future__ import annotations

import importlib

main_mod = importlib.import_module("synapse.cli.main")


def test_cmd_help(capsys):
    assert main_mod._cmd_help(None) == 0
    assert "Synapse — secure messaging" in capsys.readouterr().out


def test_cmd_version(monkeypatch, capsys):
    from synapse.cli import common as _common
    monkeypatch.setattr(_common, "project_version", lambda: "1.2.3")
    assert main_mod._cmd_version(None) == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


def test_main_show_version(monkeypatch, capsys):
    from synapse.cli import common as _common
    monkeypatch.setattr(_common, "project_version", lambda: "9.9.9")
    rc = main_mod.main(["--version"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "9.9.9"


def test_main_api_raw_routing(monkeypatch, capsys):
    seen = {}

    def fake_run_raw(tokens, prefix):
        seen.update({"tokens": tokens, "prefix": prefix})
        return 0
    monkeypatch.setattr(main_mod.api, "run_raw", fake_run_raw)
    rc = main_mod.main(["api", "help"])
    assert rc == 0
    assert seen["tokens"] == ["help"]
    assert seen["prefix"] == []


def test_main_api_raw_with_prefix(monkeypatch, capsys):
    seen = {}

    def fake_run_raw(tokens, prefix):
        seen.update({"tokens": tokens, "prefix": prefix})
        return 0
    monkeypatch.setattr(main_mod.api, "run_raw", fake_run_raw)
    main_mod.main(["--config", "/tmp/c.json", "api", "get_x", "--flag"])
    assert seen["tokens"] == ["get_x", "--flag"]
    assert seen["prefix"] == ["--config", "/tmp/c.json"]


def test_main_api_clierror_maps_to_emit(monkeypatch, capsys):
    from synapse.cli.common import CliError

    def fake_run_raw(tokens, prefix):
        raise CliError("bad api", code=7)
    monkeypatch.setattr(main_mod.api, "run_raw", fake_run_raw)
    rc = main_mod.main(["api", "x"])
    assert rc == 7
    assert "bad api" in capsys.readouterr().out


def test_main_bare_synapse_is_server_start(monkeypatch):
    seen = {}

    def fake_start(args):
        seen["config"] = args.config
        return 0
    monkeypatch.setattr(main_mod.server, "_cmd_start", fake_start)
    rc = main_mod.main([])
    assert rc == 0
    assert seen["config"] is None


def test_main_daemon_server(monkeypatch):
    from synapse.cli import daemon as _daemon
    seen = {}
    monkeypatch.setattr(_daemon, "run_server_daemon",
                        lambda config, level: seen.update(
                            {"config": config, "level": level}))
    import argparse
    args = argparse.Namespace(daemon_run="server", config="/c.json",
                              log_level="debug")
    assert main_mod._run_daemon(args) == 0
    assert seen == {"config": "/c.json", "level": "debug"}


def test_main_daemon_web(monkeypatch):
    from synapse.cli import daemon as _daemon
    seen = {}
    monkeypatch.setattr(_daemon, "run_web_daemon",
                        lambda config, port, level: seen.update(
                            {"config": config, "port": port, "level": level}))
    import argparse
    args = argparse.Namespace(daemon_run="web", config="/c.json", port=9999,
                              log_level="info")
    assert main_mod._run_daemon(args) == 0
    assert seen == {"config": "/c.json", "port": 9999, "level": "info"}


def test_main_daemon_a2a_missing_password(monkeypatch, capsys):
    from synapse.cli import daemon as _daemon
    monkeypatch.setattr(_daemon, "run_a2a_daemon", lambda *a, **k: None)
    import sys
    monkeypatch.setattr(sys, "stdin", _Stdin(""))
    monkeypatch.setattr(sys, "stderr", type("E", (), {
        "write": lambda self, s: None})())
    import argparse
    args = argparse.Namespace(daemon_run="a2a", config="/c.json",
                              agent_name="a", port=8090, log_level=None)
    assert main_mod._run_daemon(args) == 1


def test_main_daemon_a2a_ok(monkeypatch):
    from synapse.cli import daemon as _daemon
    seen = {}
    monkeypatch.setattr(_daemon, "run_a2a_daemon",
                        lambda *a, **k: seen.update(a=a))
    import sys
    monkeypatch.setattr(sys, "stdin", _Stdin("pw\ntok\n"))
    import argparse
    args = argparse.Namespace(daemon_run="a2a", config="/c.json",
                              agent_name="agent-x", port=8090, log_level=None)
    assert main_mod._run_daemon(args) == 0
    assert seen["a"][1] == "agent-x"


def test_main_handler_clierror(monkeypatch, capsys):
    from synapse.cli.common import CliError

    def fake_run(args):
        raise CliError("boom", code=4)
    monkeypatch.setattr(main_mod.server, "_cmd_start", fake_run)
    # 'server start' subcommand
    rc = main_mod.main(["server", "start"])
    assert rc == 4
    assert "boom" in capsys.readouterr().out


def test_main_handler_systemexit(monkeypatch):
    def fake_run(args):
        raise SystemExit(0)
    monkeypatch.setattr(main_mod.server, "_cmd_start", fake_run)
    assert main_mod.main(["server", "start"]) == 0


def test_main_broken_pipe(monkeypatch, capsys):
    def fake_run(args):
        raise BrokenPipeError
    monkeypatch.setattr(main_mod.server, "_cmd_start", fake_run)
    rc = main_mod.main(["server", "start"])
    assert rc == 0


class _Stdin:
    def __init__(self, text):
        self._lines = iter(text.splitlines())

    def readline(self):
        return next(self._lines, "")
