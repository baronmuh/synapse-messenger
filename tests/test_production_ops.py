"""Tests of the operations functions introduced by SPEC_PRODUCTION §5
(``version`` command) and §4 (A2A gateway state in ``status``)."""

from __future__ import annotations

import json
from importlib.metadata import version as _pkg_version

from tests.cli_helpers import run_cli


def test_version_flag(cli_env):
    """synapse --version: installed version, no server needed."""
    _, _, env = cli_env
    proc = run_cli(env, "--version")
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == _pkg_version("synapse-messenger")


def test_version_subcommand(cli_env):
    """synapse version: same version, simple output."""
    _, _, env = cli_env
    proc = run_cli(env, "version")
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == _pkg_version("synapse-messenger")


def test_version_no_server_required(cli_env):
    """The version depends neither on the server nor on the configuration."""
    import os

    _, _, env = cli_env
    env = dict(env)
    env.pop("Synapse_CONFIG", None)
    proc = run_cli(env, "--version")
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == _pkg_version("synapse-messenger")


def test_status_includes_a2a_stopped(cli_env):
    """synapse status --json exposes the state of the A2A gateway (stopped
    when not provisioned — a legitimate, optional state)."""
    _, _, env = cli_env
    proc = run_cli(env, "status", "--json")
    assert proc.returncode == 0, proc.stderr.decode()
    data = json.loads(proc.stdout.decode())["data"]
    assert "a2a" in data
    assert data["a2a"]["state"] == "stopped"
    assert data["server"]["state"] == "stopped"
