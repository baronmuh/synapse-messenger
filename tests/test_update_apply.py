"""Update validation bench: simulated ``update check/apply`` cycle
end to end (SPEC_CLI §4.16).

A fake "remote channel" (local ``{"version": ...}`` file served via
file://) and a fake update command (shell script) allow running the REAL
``apply`` path — automatic backup → web stop → server stop → command →
restart — without depending on a more recent PyPI package. Both failure
paths are also exercised: no configured command, and a command that fails
(non-zero code).
"""

from __future__ import annotations

import json
import stat

from tests.cli_helpers import run_cli


def _bootstrap(env):
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    proc = run_cli(env, "server", "start")
    assert proc.returncode == 0, proc.stderr.decode()


def _write_script(path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_update_check_remote_channel(cli_env, tmp_path):
    """check: real comparison against a fake remote channel."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        latest = tmp_path / "latest.json"
        latest.write_text(json.dumps({"version": "3.2.0"}))
        env2 = dict(env)
        env2["SYNAPSE_UPDATE_URL"] = latest.as_uri()

        proc = run_cli(env2, "update", "check", "--json")
        assert proc.returncode == 0, proc.stderr.decode()
        data = json.loads(proc.stdout.decode())["data"]
        from importlib.metadata import version as _pkg_version
        assert data["installed"] == _pkg_version("synapse-messenger")
        assert data["remote"] == "3.2.0"
        assert data["up_to_date"] is False
        assert "latest.json" in data["channel"]
    finally:
        run_cli(env, "server", "stop")


def test_update_apply_full_cycle(cli_env, tmp_path):
    """REAL apply: backup → stop → fake command → restart.

    This is the path that had never been executed end to end: it required
    a more recent package + a configured command. Here, a fake command
    writes proof (marker file) during the window where the server is
    stopped.
    """
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        marker = tmp_path / "upgraded.marker"
        script = tmp_path / "fake-update.sh"
        _write_script(script, f"#!/bin/sh\nprintf '3.1.0-simule' > '{marker}'\n")
        env2 = dict(env)
        env2["SYNAPSE_UPDATE_COMMAND"] = f"sh {script}"

        proc = run_cli(env2, "update", "apply")
        assert proc.returncode == 0, proc.stderr.decode()
        out = proc.stdout.decode()
        assert "Update applied." in out
        assert "1. automatic backup" in out

        # material proof:
        # 1. the command was indeed executed during the stop window
        assert marker.read_text() == "3.1.0-simule"
        # 2. an automatic backup was created before the stop
        backups = list((tmp_path / "backups").glob("*.synbk"))
        assert len(backups) == 1, backups
        # 3. the server restarted (and the web, on the test-isolated port)
        proc = run_cli(env, "server", "status", "--json")
        assert json.loads(proc.stdout.decode())["data"]["state"] == "running"
        proc = run_cli(env, "web", "status", "--json")
        web = json.loads(proc.stdout.decode())["data"]
        assert web["state"] == "running"
        assert web["port"] == int(env["SYNAPSE_WEB_PORT"])
    finally:
        run_cli(env, "web", "stop")
        run_cli(env, "server", "stop")


def test_update_apply_preserves_web_port(cli_env, tmp_path):
    """apply restarts the web on the port it occupied before the stop."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        run_cli(env, "web", "start", "--port", "18102")
        marker = tmp_path / "upgraded.marker"
        script = tmp_path / "fake-update.sh"
        _write_script(script, f"#!/bin/sh\nprintf 'ok' > '{marker}'\n")
        env2 = dict(env)
        env2["SYNAPSE_UPDATE_COMMAND"] = f"sh {script}"

        proc = run_cli(env2, "update", "apply")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "web", "status", "--json")
        web = json.loads(proc.stdout.decode())["data"]
        assert web["state"] == "running"
        assert web["port"] == 18102
    finally:
        run_cli(env, "web", "stop")
        run_cli(env, "server", "stop")


def test_update_apply_dry_run_does_nothing(cli_env, tmp_path):
    """--dry-run: plan displayed, NO side effects."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        marker = tmp_path / "upgraded.marker"
        script = tmp_path / "fake-update.sh"
        _write_script(script, f"#!/bin/sh\nprintf 'ok' > '{marker}'\n")
        env2 = dict(env)
        env2["SYNAPSE_UPDATE_COMMAND"] = f"sh {script}"

        proc = run_cli(env2, "update", "apply", "--dry-run")
        assert proc.returncode == 0
        assert "(--dry-run: no changes made)" in proc.stdout.decode()
        assert not marker.exists()  # nothing executed
        assert not list((tmp_path / "backups").glob("*.synbk"))  # nothing backed up
        proc = run_cli(env, "server", "status", "--json")
        assert json.loads(proc.stdout.decode())["data"]["state"] == "running"
    finally:
        run_cli(env, "server", "stop")


def test_update_apply_no_command_refused(cli_env):
    """Without a configured command, apply refuses (no simulated behavior)."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        proc = run_cli(env, "update", "apply")
        assert proc.returncode == 1
        assert "no update command configured" in proc.stderr.decode()
        # nothing was touched: server still running
        proc = run_cli(env, "server", "status", "--json")
        assert json.loads(proc.stdout.decode())["data"]["state"] == "running"
    finally:
        run_cli(env, "server", "stop")


def test_update_apply_failing_command(cli_env, tmp_path):
    """Command that fails (non-zero code): error + restorable state."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        script = tmp_path / "fail-update.sh"
        _write_script(script, "#!/bin/sh\nexit 3\n")
        env2 = dict(env)
        env2["SYNAPSE_UPDATE_COMMAND"] = f"sh {script}"

        proc = run_cli(env2, "update", "apply")
        assert proc.returncode == 1
        err = proc.stderr.decode()
        assert "failed (code 3)" in err
        assert "synapse backup restore" in err  # recovery path indicated
        # the server is stopped (stop before the command) — restorable state
        proc = run_cli(env, "server", "status", "--json")
        assert json.loads(proc.stdout.decode())["data"]["state"] == "stopped"
        # the pre-stop backup exists: restoration is possible
        backups = list((tmp_path / "backups").glob("*.synbk"))
        assert len(backups) == 1
    finally:
        run_cli(env, "server", "stop")


def test_update_apply_no_backup_flag(cli_env, tmp_path):
    """--no-backup: skips the backup (discouraged but documented)."""
    _, config_file, env = cli_env
    _bootstrap(env)
    try:
        marker = tmp_path / "upgraded.marker"
        script = tmp_path / "fake-update.sh"
        _write_script(script, f"#!/bin/sh\nprintf 'ok' > '{marker}'\n")
        env2 = dict(env)
        env2["SYNAPSE_UPDATE_COMMAND"] = f"sh {script}"

        proc = run_cli(env2, "update", "apply", "--no-backup")
        assert proc.returncode == 0, proc.stderr.decode()
        assert marker.read_text() == "ok"
        assert not list((tmp_path / "backups").glob("*.synbk"))
    finally:
        run_cli(env, "web", "stop")
        run_cli(env, "server", "stop")


# ---------------------------------------------------------------------------
# systemd mode (SPEC_PRODUCTION §1/§5): a fake "systemctl" on the PATH
# switches apply to systemctl and includes the A2A bridge in the plan.
# ---------------------------------------------------------------------------


def _fake_systemctl(tmp_path):
    """Fake systemctl: logs the calls, units present and active,
    one A2A bridge instance listed."""
    log = tmp_path / "systemctl.log"
    script = tmp_path / "systemctl"
    script.write_text(f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> '{log}'
case "$*" in
  *" cat "*) exit 0 ;;
  *" is-active "*) exit 0 ;;
  *list-units*)
    printf 'synapse-a2a@support.service loaded active running -\\n'
    exit 0 ;;
  *stop*|*start*) exit 0 ;;
esac
exit 0
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script.parent, log


def test_update_apply_systemd_mode(cli_env, tmp_path):
    """With systemd units present (fake systemctl), apply drives
    systemctl (never the CLI) and includes the A2A bridge in stop/start."""
    _, _, env = cli_env
    _bootstrap(env)
    fake_bin, log = _fake_systemctl(tmp_path)
    marker = tmp_path / "upgraded.marker"
    script = tmp_path / "fake-update.sh"
    _write_script(script, f"#!/bin/sh\nprintf 'ok' > '{marker}'\n")
    env2 = dict(env)
    env2["PATH"] = f"{fake_bin}:{env2['PATH']}"
    # The fake systemctl on the PATH IS the supervision simulation:
    # the CLI-mode test escape variable must be removed.
    env2.pop("SYNAPSE_NO_SYSTEMD", None)
    env2["SYNAPSE_UPDATE_COMMAND"] = f"sh {script}"
    try:
        proc = run_cli(env2, "update", "apply")
        assert proc.returncode == 0, proc.stderr.decode()
        assert "Update applied." in proc.stdout.decode()
        assert marker.read_text() == "ok"
        calls = log.read_text()
        # stops and restarts via systemctl, in the order of the plan
        assert "stop synapse-web.service" in calls
        assert "stop synapse-a2a@support.service" in calls
        assert "stop synapse.service" in calls
        assert "start synapse.service" in calls
        assert "start synapse-web.service" in calls
        assert "start synapse-a2a@support.service" in calls
        # the real test server stayed alive (the fake systemctl stopped
        # nothing): cleanup possible
        proc = run_cli(env, "server", "status", "--json")
        assert json.loads(proc.stdout.decode())["data"]["state"] == "running"
    finally:
        run_cli(env, "server", "stop")


def test_update_systemd_helpers_cli_mode(cli_env, monkeypatch):
    """Without installed units (``SYNAPSE_NO_SYSTEMD=1``), the helpers
    return False / an empty list: the CLI behavior is preserved."""
    from synapse.cli.update import _a2a_instances, _systemd_unit_exists

    monkeypatch.setenv("SYNAPSE_NO_SYSTEMD", "1")
    assert _systemd_unit_exists("synapse.service") is False
    assert _systemd_unit_exists("synapse-web.service") is False
    assert _a2a_instances() == []
