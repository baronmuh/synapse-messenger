"""``synapse uninstall`` validation bench (SPEC_CLI — new group).

The uninstall is tested in an ISOLATED environment (disposable
configuration under tmp_path, never the real /var): the plan is
exercised with ``--dry-run`` (lists everything, removes nothing), with
``--keep-data`` (data and backups survive), and the refusal/clean-stop
behavior while the server is running is verified.

The systemd/account/root parts are only listed in the plan on this
machine (the removal itself re-runs with sudo or prints the command —
never exercised here).
"""

from __future__ import annotations

import json

from tests.cli_helpers import run_cli


def _bootstrap(env):
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    proc = run_cli(env, "server", "start")
    assert proc.returncode == 0, proc.stderr.decode()


def test_uninstall_dry_run_lists_everything_removes_nothing(cli_env, tmp_path):
    """--dry-run: the full plan is displayed, NOTHING is removed."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        # marker: the data dir exists before the dry-run
        data_dir = tmp_path / "data"
        assert (data_dir / "synapse.db").exists()

        proc = run_cli(env, "uninstall", "--dry-run")
        assert proc.returncode == 0, proc.stderr.decode()
        out = proc.stdout.decode()
        # the plan lists the systemd units, the account, the dirs, the package
        assert "Synapse uninstall plan:" in out
        assert "synapse.service" in out
        assert "synapse-web.service" in out
        assert "synapse" in out  # the account
        assert str(tmp_path / "data") in out
        assert str(tmp_path / "backups") in out
        assert "synapse-messenger (pip)" in out
        assert "(--dry-run: nothing removed)" in out

        # nothing was removed
        assert (data_dir / "synapse.db").exists()
        proc = run_cli(env, "server", "status", "--json")
        assert json.loads(proc.stdout.decode())["data"]["state"] == "running"
    finally:
        run_cli(env, "server", "stop")


def test_uninstall_refuses_while_server_running(cli_env):
    """Without --yes, the uninstall refuses while the server runs."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        proc = run_cli(env, "uninstall", stdin="uninstall\n")
        assert proc.returncode == 1
        err = proc.stderr.decode()
        assert "stop the services first" in err
        # the server is still running (nothing was stopped)
        proc = run_cli(env, "server", "status", "--json")
        assert json.loads(proc.stdout.decode())["data"]["state"] == "running"
    finally:
        run_cli(env, "server", "stop")


def test_uninstall_keep_data_preserves_data_and_backups(cli_env, tmp_path):
    """--keep-data: the plan still lists everything but data/backups are
    filtered out of the removal (this machine: the dry-run shows it)."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        proc = run_cli(env, "uninstall", "--dry-run", "--keep-data")
        assert proc.returncode == 0, proc.stderr.decode()
        out = proc.stdout.decode()
        # data and backups are NOT listed for removal
        assert str(tmp_path / "data") not in out
        assert str(tmp_path / "backups") not in out
        # the rest is still listed
        assert "synapse.service" in out
        assert str(tmp_path / "logs") in out
    finally:
        run_cli(env, "server", "stop")


def test_uninstall_yes_stops_services_then_proceeds(cli_env, tmp_path):
    """--yes stops the running services cleanly first, then proceeds.

    The removal itself re-runs with sudo on this machine (root parts);
    the proof here is the SERVICE STOP + the plan before the sudo
    re-execution (the test environment never touches the real /var).
    """
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        proc = run_cli(env, "uninstall", "--yes")
        out = proc.stdout.decode()
        # the services were stopped (the plan ran, then the sudo/root path)
        assert "Stopping the services..." in out
        # the server is no longer running (stopped by --yes)
        proc2 = run_cli(env, "server", "status", "--json")
        assert json.loads(proc2.stdout.decode())["data"]["state"] == "stopped"
    finally:
        run_cli(env, "server", "stop")
