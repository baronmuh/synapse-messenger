"""Unit tests for the deprecated CLI aliases (SPEC_CLI §6, decision §7.1).

These are pure argument-translation functions that delegate to the unified
CLI. We monkeypatch ``synapse.cli.main.main`` so the tests assert the
translated argv and the deprecation warning without running any server.
"""

from __future__ import annotations

import sys

import pytest

import synapse.cli.aliases as aliases


@pytest.fixture()
def capture_main(monkeypatch):
    """Replaces the unified CLI main with a recorder.

    ``synapse.cli.__init__`` does ``from .main import main`` which shadows the
    ``synapse.cli.main`` module attribute with the function, so the module is
    reached through ``sys.modules`` (patched ``main`` is re-fetched on every
    ``from .main import main`` inside the alias functions).
    """
    captured = {"argv": None, "calls": 0, "result": 0}

    def fake_main(argv):
        captured["argv"] = list(argv)
        captured["calls"] += 1
        return captured["result"]

    monkeypatch.setattr(sys.modules["synapse.cli.main"], "main", fake_main)
    return captured


# ---------------------------------------------------------------------------
# _translate
# ---------------------------------------------------------------------------


def test_translate_keeps_only_listed_options():
    argv = ["--config", "/etc/a.conf", "--verbose", "--port", "8080"]
    out = aliases._translate(argv, ("--config", "--port"))
    assert out == ["--config", "/etc/a.conf", "--port", "8080"]


def test_translate_drops_value_when_next_is_flag():
    argv = ["--config", "--port", "8080"]
    # "--port" starts with "--", so it is NOT consumed as the value of
    # "--config"; the option itself is still kept.
    assert aliases._translate(argv, ("--config",)) == ["--config"]


def test_translate_single_trailing_option():
    argv = ["--config"]
    assert aliases._translate(argv, ("--config",)) == ["--config"]


def test_translate_empty_take_returns_empty():
    assert aliases._translate(["--config", "/c"], ()) == []


# ---------------------------------------------------------------------------
# alias entry points
# ---------------------------------------------------------------------------


def test_server_alias_main(capture_main, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["synapse-server", "--config", "/etc/x.conf"])
    rc = aliases.server_alias_main()
    assert rc == 0
    assert capture_main["calls"] == 1
    assert capture_main["argv"] == ["server", "start", "--foreground",
                                    "--config", "/etc/x.conf"]
    assert "synapse-server" in capsys.readouterr().err


def test_web_alias_main(capture_main, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-web", "--config", "/etc/x.conf", "--port", "9000"])
    rc = aliases.web_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["web", "start", "--foreground",
                                    "--config", "/etc/x.conf", "--port", "9000"]


def test_init_org_alias_main_enable(capture_main, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-init-org", "--enable", "acme", "--config", "/c"])
    rc = aliases.init_org_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["org", "enable", "acme", "--config", "/c"]
    assert "synapse-init-org" in capsys.readouterr().err


def test_init_org_alias_main_plain(capture_main, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["synapse-init-org"])
    rc = aliases.init_org_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["org", "init"]


def test_init_org_alias_main_config_only(capture_main, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-init-org", "--config", "/c", "--enable", "x"])
    aliases.init_org_alias_main()
    assert capture_main["argv"] == ["org", "enable", "x", "--config", "/c"]


def test_init_org_alias_main_unknown_option_skipped(capture_main, monkeypatch):
    # an unrecognized flag hits the loop fallthrough and is ignored
    monkeypatch.setattr(sys, "argv", ["synapse-init-org", "--bogus"])
    rc = aliases.init_org_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["org", "init"]


def test_backup_alias_main(capture_main, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-backup", "--config", "/c", "--out", "/o"])
    rc = aliases.backup_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["backup", "create",
                                    "--config", "/c", "--out", "/o"]


def test_restore_alias_main_with_archive(capture_main, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-restore", "/tmp/a.synbk", "--force", "--config", "/c"])
    rc = aliases.restore_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["backup", "restore", "/tmp/a.synbk",
                                    "--force", "--config", "/c"]
    assert "synapse-restore" in capsys.readouterr().err


def test_restore_alias_main_unknown_option_skipped(capture_main, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-restore", "--bogus", "/a.synbk"])
    rc = aliases.restore_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["backup", "restore", "/a.synbk"]


def test_restore_alias_main_missing_archive_returns_1(capture_main, capsys,
                                                      monkeypatch):
    # only flags, no positional archive -> error path
    monkeypatch.setattr(sys, "argv", ["synapse-restore", "--force"])
    rc = aliases.restore_alias_main()
    assert rc == 1
    assert capture_main["calls"] == 0
    assert "missing archive" in capsys.readouterr().err


def test_restore_alias_main_config_value_preserved(capture_main, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-restore", "/a.synbk", "--config", "/c"])
    rc = aliases.restore_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["backup", "restore", "/a.synbk", "--config", "/c"]


def test_restore_alias_main_config_flag_no_value(capture_main, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-restore", "/a.synbk", "--config"])
    rc = aliases.restore_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["backup", "restore", "/a.synbk", "--config"]


def test_a2a_bridge_alias_main(capture_main, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["synapse-a2a-bridge", "--config", "/c", "--agent-name", "x",
                         "--port", "5000", "--password-stdin"])
    rc = aliases.a2a_bridge_alias_main()
    assert rc == 0
    assert capture_main["argv"] == ["a2a", "start",
                                    "--config", "/c", "--agent-name", "x",
                                    "--port", "5000", "--password-stdin"]
    assert "synapse-a2a-bridge" in capsys.readouterr().err
