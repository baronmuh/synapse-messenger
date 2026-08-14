"""Tests for the periodic monitor (SPEC_PRODUCTION §4): the six checks,
aggregation, the monitor.json report and the alert command.

The module is loaded from scripts/synapse-monitor.py (it is not a package);
it imports synapse.config — available under pytest.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

import pytest

from synapse.config import Config

_MONITOR_PATH = (Path(__file__).resolve().parent.parent
                 / "scripts" / "synapse-monitor.py")


@pytest.fixture(scope="module")
def monitor():
    spec = importlib.util.spec_from_file_location("synapse_monitor", _MONITOR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mconfig(tmp_path) -> tuple[Config, Path]:
    conf = {
        "storage_dir": str(tmp_path / "data"),
        "socket_path": str(tmp_path / "run" / "synapse.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    for name in ("data", "run", "logs", "backups"):
        (tmp_path / name).mkdir()
    return Config.from_dict(conf), tmp_path


def _write_config(tmp_path, config: Config) -> Path:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(config.to_dict()))
    return cfg_file


# ---------------------------------------------------------------------------
# Unit checks
# ---------------------------------------------------------------------------


def test_check_backup_age_none(monitor, mconfig):
    config, _ = mconfig
    anomalies, info = monitor.check_backup_age(config, 26.0)
    assert anomalies and "no backup" in anomalies[0]


def test_check_backup_age_fresh(monitor, mconfig):
    config, _ = mconfig
    Path(config.backup_dir, "synapse-backup-now.synbk").write_bytes(b"x")
    anomalies, info = monitor.check_backup_age(config, 26.0)
    assert anomalies == []
    assert info["age_hours"] < 1


def test_check_backup_age_stale(monitor, mconfig):
    config, _ = mconfig
    path = Path(config.backup_dir, "synapse-backup-old.synbk")
    path.write_bytes(b"x")
    old = time.time() - 48 * 3600
    os.utime(path, (old, old))
    anomalies, _ = monitor.check_backup_age(config, 26.0)
    assert anomalies and "too old" in anomalies[0]


def test_check_database_missing(monitor, mconfig):
    config, _ = mconfig
    anomalies, info = monitor.check_database(config)
    assert anomalies and "database missing" in anomalies[0]


def test_check_database_freshness(monitor, mconfig):
    config, _ = mconfig
    conn = sqlite3.connect(config.db_path)
    try:
        conn.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "principal TEXT, event_type TEXT, ref_id TEXT, "
                     "by_username TEXT, at TEXT NOT NULL)")
        conn.execute("INSERT INTO events (principal, event_type, at) "
                     "VALUES ('agent_a', 'message_sent', ?)",
                     (monitor._now_iso(),))
        conn.commit()
    finally:
        conn.close()
    anomalies, info = monitor.check_database(config)
    assert anomalies == []
    assert info["freshness_hours"] is not None and info["freshness_hours"] < 1


def test_check_database_empty(monitor, mconfig):
    config, _ = mconfig
    conn = sqlite3.connect(config.db_path)
    try:
        conn.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "principal TEXT, event_type TEXT, ref_id TEXT, "
                     "by_username TEXT, at TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()
    anomalies, info = monitor.check_database(config)
    assert anomalies == []
    assert info["freshness"] == "no event"


def test_check_disk_ok(monitor, mconfig):
    config, _ = mconfig
    anomalies, report = monitor.check_disk(config, 90.0)
    assert anomalies == []
    for name in ("storage", "logs", "backups"):
        assert report[name]["percent"] < 90.0


def test_check_log_bursts_auth_failures(monitor, mconfig):
    config, _ = mconfig
    now = monitor._now_iso()
    lines = "\n".join(
        json.dumps({"timestamp": now, "result": "AUTH_FAILED"}) for _ in range(35)
    )
    Path(config.log_dir, "synapse.log").write_text(lines + "\n")
    anomalies, counts = monitor.check_log_bursts(config, 900, 30, 1)
    assert counts["auth_failures"] == 35
    assert anomalies and "AUTH_FAILED" in anomalies[0]


def test_check_log_bursts_exceptions(monitor, mconfig):
    config, _ = mconfig
    now = monitor._now_iso()
    lines = "\n".join(
        json.dumps({"timestamp": now, "exception_type": "RuntimeError"})
        for _ in range(2)
    )
    Path(config.log_dir, "synapse.log").write_text(lines + "\n")
    anomalies, counts = monitor.check_log_bursts(config, 900, 30, 1)
    assert counts["exceptions"] == 2
    assert anomalies and "internal error(s)" in anomalies[0]


def test_check_log_bursts_ignores_old_and_bad_lines(monitor, mconfig):
    config, _ = mconfig
    old = "2020-01-01T00:00:00.000Z"
    good = monitor._now_iso()
    content = "\n".join([
        json.dumps({"timestamp": old, "result": "AUTH_FAILED"}),
        "invalid line",
        json.dumps({"timestamp": good, "result": "AUTH_OK"}),
    ]) + "\n"
    Path(config.log_dir, "synapse.log").write_text(content)
    anomalies, counts = monitor.check_log_bursts(config, 900, 30, 1)
    assert counts == {"auth_failures": 0, "exceptions": 0,
                      "window_seconds": 900}
    assert anomalies == []


def test_check_key_vault_missing_key(monitor, mconfig):
    """No key yet (first backup): transient state, OK."""
    config, _ = mconfig
    anomalies, info = monitor.check_key_vault(config, "/etc/synapse/backup.key.vault")
    assert anomalies == []
    assert info["state"] == "no key yet"


def test_check_key_vault_missing_copy(monitor, mconfig):
    config, tmp = mconfig
    Path(config.backup_key_path).write_bytes(b"secret-key-32-bytes-00000000")
    vault = tmp / "vault" / "backup.key.vault"
    anomalies, _ = monitor.check_key_vault(config, str(vault))
    assert anomalies and "backup key copy" in anomalies[0]


def test_check_key_vault_match_and_mismatch(monitor, mconfig):
    config, tmp = mconfig
    key = b"secret-key-32-bytes-00000000"
    Path(config.backup_key_path).write_bytes(key)
    vault = tmp / "vault"
    vault.mkdir()
    vault_file = vault / "backup.key.vault"
    vault_file.write_bytes(key)
    anomalies, info = monitor.check_key_vault(config, str(vault_file))
    assert anomalies == []
    assert info["state"] == "ok"

    vault_file.write_bytes(b"autre-cle-32-bytes-11111111111")
    anomalies, _ = monitor.check_key_vault(config, str(vault_file))
    assert anomalies and "DIFFERENT" in anomalies[0]


# ---------------------------------------------------------------------------
# Aggregation and entry point
# ---------------------------------------------------------------------------


def test_run_checks_ok(monitor, mconfig, monkeypatch):
    config, _ = mconfig
    _seed_healthy(monitor, config)
    monkeypatch.setattr(monitor, "check_services",
                        lambda config_path: ([], {"services": {}}))
    report = monitor.run_checks(config, {
        "config_path": "/etc/synapse/config.json",
        "backup_max_age_hours": 26.0,
        "disk_warn_percent": 90.0,
        "error_window_seconds": 900,
        "max_auth_failures": 30,
        "max_exceptions": 1,
        "key_vault": str(Path(config.storage_dir).parent / "backup.key.vault"),
    })
    assert report["ok"] is True
    assert report["anomalies"] == []


def test_run_checks_anomaly(monitor, mconfig, monkeypatch):
    config, _ = mconfig
    _seed_healthy(monitor, config)
    monkeypatch.setattr(monitor, "check_services",
                        lambda config_path: (["server stopped"], {"services": {}}))
    report = monitor.run_checks(config, {
        "config_path": "/etc/synapse/config.json",
        "backup_max_age_hours": 26.0,
        "disk_warn_percent": 90.0,
        "error_window_seconds": 900,
        "max_auth_failures": 30,
        "max_exceptions": 1,
        "key_vault": str(Path(config.storage_dir).parent / "backup.key.vault"),
    })
    assert report["ok"] is False
    assert "server stopped" in report["anomalies"]


def _seed_healthy(monitor, config: Config) -> None:
    """Healthy state: fresh backup, database with event, key + copy."""
    Path(config.backup_dir, "synapse-backup-fresh.synbk").write_bytes(b"x")
    conn = sqlite3.connect(config.db_path)
    try:
        conn.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "principal TEXT, event_type TEXT, ref_id TEXT, "
                     "by_username TEXT, at TEXT NOT NULL)")
        conn.execute("INSERT INTO events (principal, event_type, at) "
                     "VALUES ('agent_a', 'message_sent', ?)", (monitor._now_iso(),))
        conn.commit()
    finally:
        conn.close()
    key = b"secret-key-32-bytes-00000000"
    Path(config.backup_key_path).write_bytes(key)
    vault = Path(config.storage_dir).parent / "backup.key.vault"
    vault.write_bytes(key)


def test_main_ok_and_report_file(monitor, mconfig, tmp_path, monkeypatch):
    config, _ = mconfig
    _seed_healthy(monitor, config)
    monkeypatch.setattr(monitor, "check_services",
                        lambda config_path: ([], {"services": {}}))
    monkeypatch.setenv("SYNAPSE_MONITOR_KEY_VAULT",
                       str(Path(config.storage_dir).parent / "backup.key.vault"))
    cfg_file = _write_config(tmp_path, config)
    out = tmp_path / "monitor.json"
    code = monitor.main(["--config", str(cfg_file), "--out", str(out)])
    assert code == 0
    report = json.loads(out.read_text())
    assert report["ok"] is True
    assert report["timestamp"]


def test_main_anomaly_alert_command(monitor, mconfig, tmp_path, monkeypatch):
    config, _ = mconfig
    _seed_healthy(monitor, config)
    monkeypatch.setattr(monitor, "check_services",
                        lambda config_path: (["server stopped"], {"services": {}}))
    monkeypatch.setenv("SYNAPSE_MONITOR_KEY_VAULT",
                       str(Path(config.storage_dir).parent / "backup.key.vault"))
    cfg_file = _write_config(tmp_path, config)
    capture = tmp_path / "alert.in"
    alert_script = tmp_path / "alert.sh"
    alert_script.write_text(f"#!/bin/sh\ncat > '{capture}'\n")
    alert_script.chmod(alert_script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("SYNAPSE_ALERT_COMMAND", f"sh {alert_script}")

    code = monitor.main(["--config", str(cfg_file),
                         "--out", str(tmp_path / "monitor.json")])
    assert code == 1
    alert = json.loads(capture.read_text())
    assert alert["ok"] is False
    assert "server stopped" in alert["anomalies"]


def test_check_services_real_cli(monitor, mconfig, tmp_path):
    """check_services runs the REAL CLI (no server: anomalies)."""
    config, _ = mconfig
    cfg_file = _write_config(tmp_path, config)
    anomalies, info = monitor.check_services(str(cfg_file))
    assert anomalies  # server and web are stopped
    assert info["services"]["server"] == "stopped"
    assert info["services"]["web"] == "stopped"


def test_main_single_config_resolution(monitor, mconfig, tmp_path, monkeypatch):
    """M10: the monitor must resolve the configuration ONCE — the path
    given to Config.load must be the same one used for the status
    command. Previously $SYNAPSE_CONFIG fed only the status command
    while Config.load used $Synapse_CONFIG (or the default), so the
    monitor checked the directories of a DIFFERENT config than the
    service it interrogated."""
    config, _ = mconfig
    cfg_file = _write_config(tmp_path, config)

    captured = {}

    def _fake_run_checks(cfg, opts):
        captured["config"] = cfg
        captured["opts"] = opts
        return {"ok": True, "anomalies": []}

    monkeypatch.setattr(monitor, "run_checks", _fake_run_checks)
    monkeypatch.setattr(monitor, "_write_report", lambda *a, **k: None)
    monkeypatch.setenv("SYNAPSE_CONFIG", str(cfg_file))
    monkeypatch.delenv("Synapse_CONFIG", raising=False)

    code = monitor.main(["--out", str(tmp_path / "monitor.json")])
    assert code == 0
    # the same path feeds both layers
    assert captured["opts"]["config_path"] == str(cfg_file)
    assert captured["config"].storage_dir == config.storage_dir
    assert captured["config"].backup_dir == config.backup_dir
