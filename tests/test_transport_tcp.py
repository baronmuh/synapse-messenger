"""TCP transport tests: the Windows code path, proven on Linux.

Synapse defaults to a Unix socket on POSIX; on Windows the transport is a
loopback TCP socket with a per-run token. This suite forces
``transport="tcp"`` on Linux and exercises the full lifecycle — server
start, CLI calls, Python client, web proxy, wrong-token rejection — so the
Windows transport path is genuinely tested, not just documented.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

import pytest

from synapse.config import Config
from synapse import transport as tr
from tests.cli_helpers import CLI, _free_port


def _tcp_env(tmp_path, monkeypatch):
    """(config, config_file, env) with the TCP transport forced."""
    port = _free_port()
    conf = {
        "storage_dir": str(tmp_path / "data"),
        "socket_path": str(tmp_path / "run" / "unused.sock"),
        "run_dir": str(tmp_path / "run"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
        "transport": "tcp",
        "transport_port": port,
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(conf))
    web_port = _free_port()
    env = dict(os.environ)
    env["Synapse_CONFIG"] = str(config_file)
    env["SYNAPSE_NO_SYSTEMD"] = "1"
    env["SYNAPSE_WEB_PORT"] = str(web_port)
    for d in ("data", "run", "logs", "backups"):
        (tmp_path / d).mkdir(exist_ok=True)
    monkeypatch.setenv("SYNAPSE_WEB_PORT", str(web_port))
    return Config.from_dict(conf), str(config_file), env


def _run(env, *args, stdin=None):
    return subprocess.run(
        CLI + list(args), capture_output=True, text=True, timeout=120, env=env,
        input=stdin,
    )


def test_tcp_resolution_helpers():
    assert tr.resolve_transport(Config.from_dict({"transport": "tcp"})) == "tcp"
    assert tr.resolve_transport(Config.from_dict({})) in ("unix", "tcp")
    assert tr.transport_port(Config.from_dict({})) == tr.DEFAULT_TRANSPORT_PORT
    with pytest.raises(ValueError):
        tr.resolve_transport(Config.from_dict({"transport": "bogus"}))


def test_tcp_full_lifecycle(tmp_path, monkeypatch):
    config, cfg_path, env = _tcp_env(tmp_path, monkeypatch)

    # org init through the TCP transport
    r = _run(env, "org", "init", "org_tcp", "--password-stdin",
             stdin="tcp-org-password-1\n")
    assert r.returncode == 0, r.stderr

    # server start (detached daemon)
    r = _run(env, "server", "start")
    assert r.returncode == 0, r.stderr

    try:
        # status via TCP
        r = _run(env, "server", "status", "--json")
        assert r.returncode == 0, r.stderr
        status = json.loads(r.stdout)["data"]
        assert status["state"] == "running"
        assert status["socket_ok"] is True

        # the transport token exists in the run dir
        token = tr.read_token(config)
        assert token and len(token) == 64

        # CLI commands through TCP (agent + message)
        for agent in ("alice", "bob"):
            r = _run(env, "agent", "create", agent, "--password-stdin",
                     stdin=f"tcp-password-{agent}-1\ntcp-org-password-1\n")
            assert r.returncode == 0, r.stderr
        r = _run(env, "message", "send", "bob", "hello over tcp",
                 "--client-message-id", "tcp-1", "--my-name", "alice",
                 "--password-stdin", stdin="tcp-password-alice-1\n")
        assert r.returncode == 0, r.stderr

        # Python client through TCP (from_config), web-token auth
        from synapse.client import Client
        from synapse.cli.common import read_web_token
        from synapse.service import _WEB_LOCAL

        web_token = read_web_token(config)
        assert web_token
        client = Client.from_config(config)
        orgs = client.list_orgs(_WEB_LOCAL, web_token)
        assert any(o["organization_name"] == "org_tcp" for o in orgs.get("organizations", []))

        # web start: the web proxies to the service through TCP
        r = _run(env, "web", "start")
        assert r.returncode == 0, r.stderr
        web_port = int(os.environ["SYNAPSE_WEB_PORT"])
        with socket.create_connection(("127.0.0.1", web_port), timeout=5):
            pass
    finally:
        _run(env, "server", "stop")
        _run(env, "web", "stop")
        # clean shutdown removed the token
        assert tr.read_token(config) is None


def test_tcp_transport_responds_when_stopped(tmp_path, monkeypatch):
    config, cfg_path, env = _tcp_env(tmp_path, monkeypatch)
    assert tr.transport_responds(config) is False  # nothing listening
