"""Shared helpers for the CLI tests (not collected by pytest).

The unified CLI is tested in subprocesses: disposable configuration resolved
via ``$Synapse_CONFIG`` (SPEC_CLI §2 search order), execution of
``synapse <args...>`` with controlled stdin/env.

Port isolation: ``SYNAPSE_WEB_PORT`` / ``SYNAPSE_A2A_PORT`` are set to
random free ports — production (or any other session) can listen on the
default ports (8080/8090) without failing the tests (SPEC_PRODUCTION §10.5).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

from synapse.config import Config

CLI = [sys.executable, "-m", "synapse.cli"]


def _free_port() -> int:
    """Random free TCP port on 127.0.0.1 (reduced race window)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def cli_env_data(tmp_path):
    """(config, config_file, env): isolated JSON config + environment."""
    conf = {
        "storage_dir": str(tmp_path / "data"),
        "socket_path": str(tmp_path / "run" / "synapse.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    config = Config.from_dict(conf)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(conf))
    env = dict(os.environ)
    env["Synapse_CONFIG"] = str(config_file)
    # The tests simulate a development environment (no systemd supervision):
    # the real units of the production machine must not switch `update apply`
    # over to systemctl.
    env["SYNAPSE_NO_SYSTEMD"] = "1"
    # Isolated HTTP ports: never collide with production.
    env["SYNAPSE_WEB_PORT"] = str(_free_port())
    env["SYNAPSE_A2A_PORT"] = str(_free_port())
    return config, str(config_file), env


def run_cli(env, *args, stdin: str = "", timeout: int = 60):
    """Runs ``synapse <args...>`` in a subprocess; returns the
    ``CompletedProcess`` (stdout/stderr captured)."""
    return subprocess.run(
        CLI + [*args], input=stdin.encode(), capture_output=True,
        env=env, timeout=timeout,
    )
