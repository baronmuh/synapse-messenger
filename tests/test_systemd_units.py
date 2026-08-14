"""Tests for the systemd units and operations scripts (SPEC_PRODUCTION
§1/§2/§6/§7):

- the scripts/systemd/ templates carry the full hardened block and the
  supervision directives;
- the install.sh substitution covers ALL template placeholders;
- the substituted units pass ``systemd-analyze verify`` (real
  syntax check, without root);
- the A2A gateway wrapper reads the secrets and passes them on stdin;
- the shell scripts are syntactically valid (bash -n);
- the lock ↔ pyproject check works (check_lock.sh).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SYSTEMD_DIR = REPO / "scripts" / "systemd"
INSTALL_SH = REPO / "install.sh"

_ALL_UNITS = [
    "synapse.service", "synapse-web.service", "synapse-a2a@.service",
    "synapse-backup.service", "synapse-backup.timer",
    "synapse-backup-verify.service", "synapse-backup-verify.timer",
    "synapse-monitor.service", "synapse-monitor.timer",
    "synapse-ci.service", "synapse-ci.timer",
]


def _read(name: str) -> str:
    return (SYSTEMD_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Template contents
# ---------------------------------------------------------------------------


def test_server_unit_hardened():
    unit = _read("synapse.service")
    for directive in (
        "Type=simple",
        "StateDirectory=synapse",
        "LogsDirectory=synapse",
        "RuntimeDirectory=synapse",
        "WatchdogSec=30",
        "StartLimitIntervalSec=600",
        "StartLimitBurst=5",
        "MemoryHigh=4G",
        "MemoryMax=6G",
        "OOMScoreAdjust=500",
        "RestrictAddressFamilies=AF_UNIX",
        "CapabilityBoundingSet=",
        "SystemCallFilter=@system-service",
        "PrivateDevices=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "LockPersonality=yes",
        "RestrictSUIDSGID=yes",
        "RestrictRealtime=yes",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "UMask=0077",
    ):
        assert directive in unit, directive
    assert "server start --foreground" in unit
    assert "Restart=on-failure" in unit


def test_web_unit_hardened():
    unit = _read("synapse-web.service")
    assert "MemoryMax=512M" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in unit
    assert "Requires=synapse.service" in unit
    assert "After=synapse.service" in unit
    assert "web start --foreground --port 8080" in unit
    assert "ExecStartPre=" in unit  # bounded wait for the server socket


def test_a2a_template():
    unit = _read("synapse-a2a@.service")
    assert "ConditionPathExists=@@SECRETS_DIR@@/a2a-%i.password" in unit
    assert "ExecStart=@@WRAPPER@@ %i 8090" in unit
    assert "MemoryMax=512M" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in unit
    assert "ExecStartPre=" in unit


def test_runtime_directory_server_only():
    """RuntimeDirectory belongs ONLY to the server: a unit that fails
    with a shared RuntimeDirectory would have /run/synapse cleaned by
    systemd (server socket removed — bug found in real deployment,
    SPEC_PRODUCTION constraint 14)."""
    assert "RuntimeDirectory=synapse" in _read("synapse.service")
    for name in ("synapse-web.service", "synapse-a2a@.service",
                 "synapse-backup.service", "synapse-backup-verify.service",
                 "synapse-monitor.service"):
        assert "RuntimeDirectory" not in _read(name), name


def test_timers_persistent():
    for name in ("synapse-backup.timer", "synapse-backup-verify.timer",
                 "synapse-monitor.timer", "synapse-ci.timer"):
        timer = _read(name)
        assert "Persistent=true" in timer
        assert "[Timer]" in timer


def test_backup_service_retention_and_verify():
    service = _read("synapse-backup.service")
    assert "backup create --dir @@BACKUP_DIR@@" in service
    assert "backup prune --keep 14" in service
    verify = _read("synapse-backup-verify.service")
    assert "backup verify --latest" in verify


def test_monitor_and_ci_units():
    monitor = _read("synapse-monitor.service")
    assert "synapse-monitor.py --config @@CONFIG@@" in monitor
    ci = _read("synapse-ci.service")
    assert "scripts/ci.sh" in ci
    assert "WorkingDirectory=@@REPO@@" in ci


def test_install_sh_substitutes_all_placeholders():
    """Every template placeholder has a substitution in install.sh."""
    install = INSTALL_SH.read_text(encoding="utf-8")
    placeholders = set()
    for name in _ALL_UNITS:
        for token in ("@@VENV@@", "@@CONFIG@@", "@@BACKUP_DIR@@",
                      "@@SECRETS_DIR@@", "@@WRAPPER@@", "@@SCRIPTS_DIR@@",
                      "@@SOCKET@@", "@@REPO@@"):
            if token in _read(name):
                placeholders.add(token)
    for token in placeholders:
        assert f"s|{token}|" in install, token
    assert "for unit in synapse.service" in install
    assert "systemctl daemon-reload" in install
    assert "systemctl enable" in install
    assert "requirements.lock" in install
    assert "--require-hashes" in install
    assert "--no-deps" in install
    assert "backup.key.vault" in install


def test_shell_scripts_syntax():
    for script in ("install.sh", "scripts/ci.sh", "scripts/check_lock.sh",
                   "scripts/install-git-hooks.sh", "scripts/synapse-a2a-systemd"):
        result = subprocess.run(["bash", "-n", str(REPO / script)],
                                capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"


# ---------------------------------------------------------------------------
# systemd-analyze verification (real, without root)
# ---------------------------------------------------------------------------


def _substitute_units(tmp_path: Path) -> dict:
    """Substitutes the placeholders and installs the fake executables."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    config = tmp_path / "etc" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}")
    wrapper = tmp_path / "opt" / "synapse-a2a-systemd"
    wrapper.parent.mkdir(parents=True)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    monitor = scripts_dir / "synapse-monitor.py"
    monitor.write_text("#!/usr/bin/env python3\n")
    monitor.chmod(monitor.stat().st_mode | stat.S_IXUSR)
    ci = scripts_dir / "ci.sh"
    ci.write_text("#!/usr/bin/env bash\nexit 0\n")
    ci.chmod(ci.stat().st_mode | stat.S_IXUSR)

    for exe in ("synapse", "python", "pip"):
        path = venv / "bin" / exe
        path.write_text("#!/usr/bin/env bash\nexit 0\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    wrapper.write_text("#!/usr/bin/env bash\nexit 0\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    replacements = {
        "@@VENV@@": str(venv),
        "@@CONFIG@@": str(config),
        "@@BACKUP_DIR@@": str(tmp_path / "backups"),
        "@@SECRETS_DIR@@": str(tmp_path / "secrets"),
        "@@WRAPPER@@": str(wrapper),
        "@@SCRIPTS_DIR@@": str(scripts_dir),
        "@@REPO@@": str(tmp_path),
    }
    units = {}
    for name in _ALL_UNITS:
        content = _read(name)
        for token, value in replacements.items():
            content = content.replace(token, value)
        path = tmp_path / name
        path.write_text(content)
        units[name] = path
    return units


@pytest.mark.skipif(shutil.which("systemd-analyze") is None,
                    reason="systemd-analyze missing")
def test_systemd_analyze_verify_all_units(tmp_path):
    """The substituted units pass the systemd syntax check."""
    units = _substitute_units(tmp_path)
    result = subprocess.run(
        ["systemd-analyze", "verify", *[str(p) for p in units.values()]],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"systemd-analyze verify failed:\n{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# A2A gateway wrapper (real behavior)
# ---------------------------------------------------------------------------


def test_a2a_wrapper_forwards_secrets_on_stdin(tmp_path):
    fake_bin = tmp_path / "venv" / "bin"
    fake_bin.mkdir(parents=True)
    capture = tmp_path / "capture"
    fake_synapse = fake_bin / "synapse"
    fake_synapse.write_text(
        f"#!/usr/bin/env bash\nprintf 'ARGS:%s\\n' \"$*\" > '{capture}'\n"
        f"cat >> '{capture}'\n")
    fake_synapse.chmod(fake_synapse.stat().st_mode | stat.S_IXUSR)

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "a2a-support.password").write_text("motdepasse-agent\n")
    (secrets / "a2a-support.token").write_text("jeton-secret-123\n")

    wrapper = REPO / "scripts" / "synapse-a2a-systemd"
    env = dict(os.environ)
    env["SYNAPSE_VENV_BIN"] = str(fake_bin)
    env["SYNAPSE_CONFIG"] = "/etc/synapse/config.json"
    env["SYNAPSE_SECRETS_DIR"] = str(secrets)
    result = subprocess.run([str(wrapper), "support", "8090"],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    content = capture.read_text()
    assert "ARGS:a2a start --foreground --agent-name support --port 8090" in content
    assert "--config /etc/synapse/config.json" in content
    assert "--password-stdin --token-stdin" in content
    assert content.split("\n")[1:3] == ["motdepasse-agent", "jeton-secret-123"]


def test_a2a_wrapper_missing_secrets(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    env = dict(os.environ)
    env["SYNAPSE_SECRETS_DIR"] = str(secrets)
    env["SYNAPSE_VENV_BIN"] = str(tmp_path / "venv" / "bin")
    wrapper = REPO / "scripts" / "synapse-a2a-systemd"
    result = subprocess.run([str(wrapper), "agent-x"],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 1
    assert "secrets missing" in result.stderr


def test_a2a_cli_restart_without_secrets_dir_env(monkeypatch, tmp_path):
    """regression: _a2a_cli_restart used an undefined _default_paths()
    when SYNAPSE_SECRETS_DIR was unset (NameError). It must fall back to
    platform.default_paths() and return False cleanly."""
    from synapse.cli.update import _a2a_cli_restart
    from synapse.config import Config

    monkeypatch.delenv("SYNAPSE_SECRETS_DIR", raising=False)
    config = Config.from_dict({
        "storage_dir": str(tmp_path / "data"),
        "socket_path": str(tmp_path / "run.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    })
    # the default secrets dir does not contain the a2a files → False,
    # never a NameError
    assert _a2a_cli_restart(config, "agent-x", 8090) is False


# ---------------------------------------------------------------------------
# check_lock.sh (§7)
# ---------------------------------------------------------------------------


def test_check_lock_ok(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\ndependencies = [\n    "argon2-cffi>=23.1.0",\n'
        '    "cryptography>=42.0.4",\n]\n')
    lock = tmp_path / "requirements.lock"
    lock.write_text("argon2-cffi==23.1.0\ncryptography==42.0.4\n")
    env = dict(os.environ, SYNAPSE_LOCK_CHECK_REPO=str(tmp_path))
    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "check_lock.sh")],
        capture_output=True, text=True, cwd=tmp_path, env=env, timeout=30)
    assert result.returncode == 0, result.stderr


def test_check_lock_missing_dependency(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\ndependencies = [\n    "orjson>=3.9",\n]\n')
    lock = tmp_path / "requirements.lock"
    lock.write_text("argon2-cffi==23.1.0\n")
    env = dict(os.environ, SYNAPSE_LOCK_CHECK_REPO=str(tmp_path))
    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "check_lock.sh")],
        capture_output=True, text=True, cwd=tmp_path, env=env, timeout=30)
    assert result.returncode == 1
    assert "orjson" in result.stderr
