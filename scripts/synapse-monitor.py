#!/usr/bin/env python3
"""Periodic Synapse monitor (SPEC_PRODUCTION §4).

Run every 5 minutes by ``synapse-monitor.timer`` (User=synapse,
local token 0600). Checks:

  1. service state (``synapse status --json``): the server and the web
     must be ``running``; the A2A bridge is OPTIONAL (only
     ``degraded`` is an anomaly — ``stopped`` is a legitimate state);
  2. age of the last backup: < 26 h (default;
     ``SYNAPSE_MONITOR_BACKUP_MAX_AGE_HOURS``) ;
  3. database freshness: last event of the ``events`` table
     (the "a session is running" metric) — reported, anomaly only if
     the database is missing ;
  4. disk space: usage >= 90% (default;
     ``SYNAPSE_MONITOR_DISK_WARN_PERCENT``) on storage, logs,
     backups ;
  5. error bursts: ``AUTH_FAILED`` and ``exception_type`` in the
     last 15 minutes (default; ``SYNAPSE_MONITOR_ERROR_WINDOW_SECONDS``,
     ``SYNAPSE_MONITOR_MAX_AUTH_FAILURES`` = 30,
     ``SYNAPSE_MONITOR_MAX_EXCEPTIONS`` = 1) ;
  6. ``backup.key`` backup copy: presence + identical sha256
     fingerprint (``SYNAPSE_MONITOR_KEY_VAULT``, default
     ``/etc/synapse/backup.key.vault``).

Output: ``monitor.json`` (``--out``, default ``<storage_dir>/monitor.json``,
0600) ; code de sortie 0 si tout va bien, 1 en anomalie. En cas d'anomalie,
``alert_command`` (the ``alert_command`` config key or the
``SYNAPSE_ALERT_COMMAND`` variable) receives the JSON report on stdin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from synapse.config import Config

# Default thresholds (overridable via the SYNAPSE_MONITOR_* environment).
DEFAULT_BACKUP_MAX_AGE_HOURS = 26.0
DEFAULT_DISK_WARN_PERCENT = 90.0
DEFAULT_ERROR_WINDOW_SECONDS = 15 * 60
DEFAULT_MAX_AUTH_FAILURES = 30
DEFAULT_MAX_EXCEPTIONS = 1
DEFAULT_KEY_VAULT = "/etc/synapse/backup.key.vault"
DEFAULT_LOG_TAIL_LINES = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    dt = _now()
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{dt.microsecond // 1000:03d}Z"


def _parse_iso(value: str) -> datetime | None:
    """Parses an ISO timestamp ``YYYY-MM-DDTHH:MM:SS.sssZ`` (API format)."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _hours_since(when: datetime) -> float:
    return max(0.0, (_now() - when).total_seconds() / 3600.0)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_services(config_path: str) -> tuple[list[str], dict]:
    """Service state via ``synapse status --json`` (real CLI path)."""
    anomalies: list[str] = []
    cmd = [sys.executable, "-m", "synapse.cli", "status", "--json",
           "--config", config_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except OSError as exc:
        return [f"status unreachable: {exc}"], {"error": str(exc)}
    if result.returncode != 0:
        return [f"status failed (code {result.returncode})"], {
            "error": result.stderr.strip() or f"code {result.returncode}"}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ["sortie status illisible"], {"error": "JSON invalide"}
    if not data.get("success"):
        message = (data.get("error") or {}).get("message", "status failure")
        return [f"status failed: {message}"], {"error": message}

    status = data.get("data") or {}
    services = {
        "server": (status.get("server") or {}).get("state"),
        "web": (status.get("web") or {}).get("state"),
        "a2a": (status.get("a2a") or {}).get("state"),
    }
    if services["server"] != "running":
        anomalies.append(f"server {services['server'] or 'unknown'}")
    if services["web"] != "running":
        anomalies.append(f"web {services['web'] or 'unknown'}")
    if services["a2a"] == "degraded":
        anomalies.append("A2A bridge degraded")
    return anomalies, {"services": services, "status": status}


def check_backup_age(config: Config,
                     max_age_hours: float) -> tuple[list[str], dict]:
    """Age of the most recent backup (< max_age_hours)."""
    anomalies: list[str] = []
    backup_dir = Path(config.backup_dir)
    info: dict = {}
    if not backup_dir.is_dir():
        return [f"no backup in {config.backup_dir}"], info
    try:
        archives = sorted(backup_dir.glob("*.synbk"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as exc:
        return [f"cannot read the backups: {exc}"], info
    if not archives:
        return [f"no backup in {config.backup_dir}"], info
    latest = archives[0]
    age_hours = (time.time() - latest.stat().st_mtime) / 3600.0
    info = {"latest": latest.name, "age_hours": round(age_hours, 2),
            "count": len(archives)}
    if age_hours > max_age_hours:
        anomalies.append(
            f"backup too old: {latest.name} "
            f"({age_hours:.1f} h > {max_age_hours:g} h)")
    return anomalies, info


def check_database(config: Config) -> tuple[list[str], dict]:
    """Database present + freshness (last event of the events table)."""
    anomalies: list[str] = []
    db_path = config.db_path
    info: dict = {"db": db_path}
    if not os.path.exists(db_path):
        return [f"database missing: {db_path}"], info
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT MAX(at) FROM events").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [f"database unreadable: {exc}"], info
    latest = row[0] if row else None
    if latest is None:
        info["freshness"] = "no event"
        info["freshness_hours"] = None
    else:
        parsed = _parse_iso(latest)
        if parsed is None:
            info["freshness"] = latest
            info["freshness_hours"] = None
        else:
            info["freshness"] = latest
            info["freshness_hours"] = round(_hours_since(parsed), 2)
    return anomalies, info


def check_disk(config: Config, warn_percent: float) -> tuple[list[str], dict]:
    """Disk space of the data directories (alert threshold)."""
    anomalies: list[str] = []
    report: dict = {}
    for name, path in (("storage", config.storage_dir),
                       ("logs", config.log_dir),
                       ("backups", config.backup_dir)):
        if not os.path.isdir(path):
            report[name] = {"path": path, "missing": True}
            continue
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            anomalies.append(f"disque {name} illisible : {exc}")
            report[name] = {"path": path, "error": str(exc)}
            continue
        percent = usage.used / usage.total * 100.0
        report[name] = {"path": path, "percent": round(percent, 1),
                        "free_bytes": usage.free}
        if percent >= warn_percent:
            anomalies.append(
                f"disk {name} at {percent:.0f}% (threshold {warn_percent:g}%)")
    return anomalies, report


def _tail_lines(path: Path, lines: int) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = 8192
            data = b""
            while size > 0 and len(data) < lines * 256:
                read = min(block, size)
                size -= read
                fh.seek(size)
                data = fh.read(read) + data
            return data.decode("utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def check_log_bursts(config: Config, window_seconds: int,
                     max_auth_failures: int, max_exceptions: int,
                     tail_lines: int = DEFAULT_LOG_TAIL_LINES
                     ) -> tuple[list[str], dict]:
    """Error bursts in the JSON logs of the last 15 minutes."""
    anomalies: list[str] = []
    log_dir = Path(config.log_dir)
    names = ("synapse.log", "web.log", "a2a.log",
             "synapse.error.log", "web.error.log", "a2a.error.log")
    since = time.time() - window_seconds
    counts = {"auth_failures": 0, "exceptions": 0, "window_seconds": window_seconds}
    for name in names:
        path = log_dir / name
        for line in _tail_lines(path, tail_lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp")
            parsed = _parse_iso(ts) if isinstance(ts, str) else None
            if parsed is None:
                continue
            if parsed.timestamp() < since:
                continue
            if entry.get("result") == "AUTH_FAILED":
                counts["auth_failures"] += 1
            if entry.get("exception_type"):
                counts["exceptions"] += 1
    if counts["auth_failures"] > max_auth_failures:
        anomalies.append(
            f"{counts['auth_failures']} AUTH_FAILED in {window_seconds // 60} min "
            f"(threshold {max_auth_failures})")
    if counts["exceptions"] > max_exceptions:
        anomalies.append(
            f"{counts['exceptions']} internal error(s) in "
            f"{window_seconds // 60} min (threshold {max_exceptions})")
    return anomalies, counts


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_key_vault(config: Config, vault_path: str) -> tuple[list[str], dict]:
    """backup.key backup copy: presence + identical fingerprint."""
    anomalies: list[str] = []
    key_path = config.backup_key_path
    info: dict = {"key": key_path, "vault": vault_path}
    if not os.path.exists(key_path):
        # First backup not done yet: legitimate transient state.
        info["state"] = "no key yet"
        return anomalies, info
    if not os.path.exists(vault_path):
        return [f"backup key copy missing: {vault_path}"], {
            **info, "state": "key copy missing"}
    try:
        key_digest = _sha256(key_path)
        vault_digest = _sha256(vault_path)
    except OSError as exc:
        return [f"cannot read the key: {exc}"], {**info,
                                                           "state": "illisible"}
    info["key_sha256"] = key_digest[:16]
    info["vault_sha256"] = vault_digest[:16]
    if key_digest != vault_digest:
        return [f"backup key copy DIFFERENT (vault: "
                f"{vault_path})"], {**info, "state": "different fingerprint"}
    info["state"] = "ok"
    return anomalies, info


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def run_checks(config: Config, opts: dict) -> dict:
    """Runs the six checks; returns the full report."""
    anomalies: list[str] = []
    services_anomalies, services = check_services(config_path=opts["config_path"])
    backup_anomalies, backup = check_backup_age(config, opts["backup_max_age_hours"])
    db_anomalies, database = check_database(config)
    disk_anomalies, disk = check_disk(config, opts["disk_warn_percent"])
    log_anomalies, errors = check_log_bursts(
        config, opts["error_window_seconds"], opts["max_auth_failures"],
        opts["max_exceptions"])
    vault_anomalies, key_vault = check_key_vault(config, opts["key_vault"])
    anomalies = (services_anomalies + backup_anomalies + db_anomalies
                 + disk_anomalies + log_anomalies + vault_anomalies)
    return {
        "timestamp": _now_iso(),
        "ok": not anomalies,
        "services": services,
        "backup": backup,
        "database": database,
        "disk": disk,
        "errors": errors,
        "key_vault": key_vault,
        "anomalies": anomalies,
    }


def _write_report(path: str, report: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _run_alert(alert_command: str, report: dict) -> None:
    """Runs the alert command with the JSON report on stdin."""
    try:
        subprocess.run(alert_command, shell=True,
                       input=json.dumps(report, ensure_ascii=False).encode("utf-8"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass  # l'alerte ne doit jamais masquer l'anomalie


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="synapse-monitor",
        description="Periodic Synapse monitor (SPEC_PRODUCTION §4).")
    parser.add_argument("--config", default=None,
                        help="configuration path (default: project default)")
    parser.add_argument("--out", default=None,
                        help="monitor.json path (default: <storage>/monitor.json)")
    parser.add_argument("--alert-command", default=None,
                        help="alert command (default: the alert_command config "
                             "configuration ou SYNAPSE_ALERT_COMMAND)")
    args = parser.parse_args(argv)

    config = Config.load(args.config)
    opts = {
        "config_path": args.config or os.environ.get("SYNAPSE_CONFIG")
        or os.environ.get("Synapse_CONFIG") or "/etc/synapse/config.json",
        "backup_max_age_hours": _env_float(
            "SYNAPSE_MONITOR_BACKUP_MAX_AGE_HOURS", DEFAULT_BACKUP_MAX_AGE_HOURS),
        "disk_warn_percent": _env_float(
            "SYNAPSE_MONITOR_DISK_WARN_PERCENT", DEFAULT_DISK_WARN_PERCENT),
        "error_window_seconds": _env_int(
            "SYNAPSE_MONITOR_ERROR_WINDOW_SECONDS", DEFAULT_ERROR_WINDOW_SECONDS),
        "max_auth_failures": _env_int(
            "SYNAPSE_MONITOR_MAX_AUTH_FAILURES", DEFAULT_MAX_AUTH_FAILURES),
        "max_exceptions": _env_int(
            "SYNAPSE_MONITOR_MAX_EXCEPTIONS", DEFAULT_MAX_EXCEPTIONS),
        "key_vault": os.environ.get("SYNAPSE_MONITOR_KEY_VAULT")
        or DEFAULT_KEY_VAULT,
    }

    report = run_checks(config, opts)
    out_path = args.out or os.path.join(config.storage_dir, "monitor.json")
    _write_report(out_path, report)

    if not report["ok"]:
        alert_command = (args.alert_command
                         or config._extra.get("alert_command")
                         or os.environ.get("SYNAPSE_ALERT_COMMAND"))
        if alert_command:
            _run_alert(alert_command, report)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
