"""``diag`` group (SPEC_CLI §4.15): detailed state and diagnostics."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

from ..client import ApiClientError, Client, ClientTransportError
from ..config import Config
from .common import (
    EXIT_ERROR,
    EXIT_OK,
    colorize,
    emit,
    http_get,
    project_version,
    read_pid_file,
    read_web_token,
    resolve_config,
    run_dir,
    service_state,
    socket_responds,
    table,
)

GROUP = "diag"

_EXAMPLES = """\
Examples:
  synapse diag status --json     detailed global state
  synapse diag doctor            environment diagnostics (7 checks)
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="diagnostics (detailed status, doctor)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("status", parents=[common],
                           help="detailed global state")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_status)

    a = actions.add_parser("doctor", parents=[common],
                           help="environment diagnostics (7 checks)")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_doctor)


# ---------------------------------------------------------------------------
# diag status
# ---------------------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    server = service_state(config, "synapse")
    web = _web_state(config)
    info = read_pid_file(config, "synapse") or {}
    payload = {
        "version": project_version(),
        "config": config.to_dict(),
        "server": server,
        "web": web,
        "socket": config.socket_path,
        "web_token_present": read_web_token(config) is not None,
        "database": config.db_path,
        "storage_dir": config.storage_dir,
        "log_dir": config.log_dir,
        "backup_dir": config.backup_dir,
    }
    if server["state"] != "stopped":
        from .common import CliError, resolve_org_auth

        try:
            org, password = resolve_org_auth(config, args)
            payload["server_status"] = Client.from_config(config).get_server_status(
                org, password
            )
        except (ApiClientError, CliError, ClientTransportError) as exc:
            payload["server_status"] = {"error": str(exc)}
    if web["state"] != "stopped":
        payload["web_status"] = {"http": web["http"], "status": web["status"]}

    if getattr(args, "json", False):
        return emit(args, payload)
    print(f"Synapse {project_version()} — {config.socket_path}")
    print(f"  server   {_state_label(server['state'])}"
          + (f" (PID {server['pid']})" if server["pid"] else ""))
    print(f"  web       {_state_label(web['state'])}"
          + (f" (PID {web['pid']})" if web["pid"] else ""))
    print(f"  base      {config.db_path}")
    print(f"  web token {'present' if payload['web_token_present'] else 'missing'}")
    print(f"  storage   {config.storage_dir}")
    print(f"  logs      {config.log_dir}")
    print(f"  backups   {config.backup_dir}")
    if payload.get("server_status") and "error" not in payload["server_status"]:
        st = payload["server_status"]
        print(f"  requests  {st.get('requests_total')} | uptime "
              f"{st.get('uptime_seconds')} s | {st.get('commands_count')} commands")
    return EXIT_OK


def _web_state(config) -> dict:  # noqa: ANN001
    """Web state: live PID AND HTTP responds (no Unix socket)."""
    info = read_pid_file(config, "web") or {}
    pid = info.get("pid")
    alive = pid is not None and _pid_alive(pid)
    port = info.get("port") or 8080
    code, status = http_get(port, "/api/status")
    if not alive:
        state = "stopped"
    elif code == 200:
        state = "running"
    else:
        state = "degraded"
    return {"state": state, "pid": pid, "pid_file": info, "http": code,
            "status": status}


def _pid_alive(pid) -> bool:  # noqa: ANN001
    from .common import pid_alive

    return pid_alive(pid)


def _state_label(state: str) -> str:
    if state == "running":
        return colorize("running", "green")
    if state == "degraded":
        return colorize("DEGRADED", "yellow")
    return colorize("stopped", "red")


# ---------------------------------------------------------------------------
# diag doctor — 7 checks (SPEC_CLI §4.15)
# ---------------------------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    checks = [
        _check_config(config),
        _check_dirs(config),
        _check_socket(config),
        _check_web_token(config),
        _check_versions(),
        _check_database(config),
        _check_clock(),
    ]
    if getattr(args, "json", False):
        return emit(args, {"checks": checks})
    rows = []
    failed = 0
    for check in checks:
        verdict = check["verdict"]  # OK | WARNING | FAIL
        label = {"OK": "OK", "WARNING": "WARNING", "FAIL": "FAIL"}[verdict]
        color = {"OK": "green", "WARNING": "yellow", "FAIL": "red"}[verdict]
        rows.append([colorize(label, color), check["name"], check["detail"]])
        if verdict == "FAIL":
            failed += 1
    print(table(rows, ["verdict", "check", "detail"]))
    if failed:
        print(f"{failed} check(s) failing — fix before starting.")
        return EXIT_ERROR
    print("Diagnostic complete: no failing checks.")
    return EXIT_OK


def _check(name: str, verdict: str, detail: str) -> dict:
    return {"name": name, "verdict": verdict, "detail": detail}


def _check_config(config: Config) -> dict:
    try:
        loaded = resolve_config()
        assert loaded is not None
        return _check("configuration", "OK", "readable and valid")
    except Exception as exc:  # noqa: BLE001
        return _check("configuration", "FAIL", f"unreadable: {exc}")


def _check_dirs(config: Config) -> dict:
    problems = []
    for label, path in (("storage", config.storage_dir), ("run", run_dir(config)),
                        ("logs", config.log_dir), ("backups", config.backup_dir)):
        p = Path(path)
        if p.exists() and not p.is_dir():
            problems.append(f"{label}: {path} is not a directory")
            continue
        if p.exists():
            mode = p.stat().st_mode & 0o777
            if label == "backups" and not p.exists():
                continue  # created at the first backup
            if mode & 0o077 and label != "backups":
                problems.append(f"{label}: permissions {oct(mode)} (expected 0700)")
    if problems:
        return _check("directories", "WARNING", "; ".join(problems))
    return _check("directories", "OK",
                  "storage, run, logs and backups present with 0700 permissions")


def _check_socket(config: Config) -> dict:
    if not socket_responds(config):
        return _check("socket", "FAIL", "transport endpoint not responding (server stopped?)")
    return _check("socket", "OK", "present and responding")


def _check_web_token(config: Config) -> dict:
    path = os.path.join(run_dir(config), "web_token")
    if not os.path.exists(path):
        return _check("web token", "WARNING", "absent (server stopped?)")
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        return _check("web token", "FAIL", f"permissions {oct(mode)} (expected 0600)")
    if not read_web_token(config):
        return _check("web token", "FAIL", "empty file")
    return _check("web token", "OK", "present and readable (0600)")


def _check_versions() -> dict:
    problems = []
    if sys.version_info < (3, 11):
        problems.append(f"Python {sys.version.split()[0]} (>= 3.11 required)")
    sqlite_version = sqlite3.sqlite_version_info
    if sqlite_version < (3, 35, 0):
        problems.append(f"SQLite {sqlite3.sqlite_version} (>= 3.35 required)")
    if problems:
        return _check("versions", "FAIL", "; ".join(problems))
    return _check("versions", "OK",
                  f"Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}")


def _check_database(config: Config) -> dict:
    db_path = config.db_path
    if not os.path.exists(db_path):
        return _check("base", "WARNING", "missing (never initialized?)")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                return _check("base", "FAIL", f"integrity: {integrity}")
            journal = conn.execute("PRAGMA journal_mode").fetchone()
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            expected = {"organizations", "accounts", "messages", "tasks",
                        "conversations", "groups", "audit_log", "events"}
            missing = expected - tables
            if missing:
                return _check("base", "FAIL",
                              f"missing tables: {', '.join(sorted(missing))}")
            detail = f"integrity ok, journal {journal[0] if journal else '?'}"
            return _check("base", "OK", detail)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _check("base", "FAIL", str(exc))


def _check_clock() -> dict:
    """Clock: the wall clock and the monotonic advance together."""
    mono_a, wall_a = time.monotonic(), time.time()
    time.sleep(0.2)
    mono_b, wall_b = time.monotonic(), time.time()
    drift = abs((mono_b - mono_a) - (wall_b - wall_a))
    if drift > 0.5:
        return _check("clock", "FAIL",
                      f"monotonic/clock drift of {drift:.2f} s (clock adjusted?)")
    return _check("clock", "OK", "monotonic and clock synchronized")
