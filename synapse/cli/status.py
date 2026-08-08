"""Root ``status`` group (SPEC_CLI §4.17): global state in one view."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..client import ApiClientError, Client, ClientTransportError
from .common import (
    EXIT_OK,
    emit,
    http_get,
    read_pid_file,
    read_web_token,
    resolve_config,
    service_state,
)

GROUP = "status"

_EXAMPLES = """\
Examples:
  synapse status            condensed view: server + web + organizations
  synapse status --json     full JSON aggregate
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="global state (server, web, organizations, backups)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(run=_cmd_status)


def _web_state(config) -> dict:  # noqa: ANN001
    """Web state: live PID AND HTTP responds (no Unix socket)."""
    info = read_pid_file(config, "web") or {}
    pid = info.get("pid")
    from .common import pid_alive

    alive = pid is not None and pid_alive(pid)
    port = info.get("port") or 8080
    code, _status = http_get(port, "/api/status")
    if not alive:
        state = "stopped"
    elif code == 200:
        state = "running"
    else:
        state = "degraded"
    return {"state": state, "pid": pid, "pid_file": info, "http": code}


def _a2a_state(config) -> dict:  # noqa: ANN001
    """A2A bridge state: live PID AND HTTP responds.

    The bridge is OPTIONAL (provisioned by the presence of agent secrets
    of agent secrets, SPEC_PRODUCTION §1): ``stopped`` is a legitimate state;
    only ``degraded`` (live PID, silent HTTP) signals an anomaly.
    """
    info = read_pid_file(config, "a2a") or {}
    pid = info.get("pid")
    from .common import pid_alive

    alive = pid is not None and pid_alive(pid)
    port = info.get("port") or 8090
    code, _status = http_get(port, "/", timeout=2.0)
    http_ok = 200 <= code < 500
    if not alive:
        state = "stopped"
    elif http_ok:
        state = "running"
    else:
        state = "degraded"
    return {"state": state, "pid": pid, "pid_file": info, "http": code,
            "agent_name": info.get("agent_name")}


def _cmd_status(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    server = service_state(config, "synapse")
    web = _web_state(config)
    a2a = _a2a_state(config)

    payload: dict = {"server": server, "web": web, "a2a": a2a,
                     "organizations": None, "backups": None}

    # Active organizations: local token when available.
    token = read_web_token(config)
    if token is not None and server["state"] != "stopped":
        from ..service import _WEB_LOCAL

        try:
            data = Client.from_config(config).list_orgs(_WEB_LOCAL, token)
            payload["organizations"] = data.get("organizations", [])
        except (ApiClientError, ClientTransportError):
            payload["organizations"] = {"error": "service unreachable"}

    # Recent backups (read from the local directory).
    from .backup import _header_date

    backups = []
    backup_dir = Path(config.backup_dir)
    if backup_dir.is_dir():
        for path in sorted(backup_dir.glob("*.synbk"),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            backups.append({"name": path.name, "size": path.stat().st_size,
                            "created_at": _header_date(config, path)})
    payload["backups"] = backups

    if getattr(args, "json", False):
        return emit(args, payload)

    print("=== server ===")
    if server["state"] == "running":
        print(f"  running (PID {server['pid']})")
    elif server["state"] == "degraded":
        print(f"  DEGRADED (PID {server['pid']} alive, socket silent)")
    else:
        print("  stopped")
    print("=== web ===")
    if web["state"] == "running":
        info = web["pid_file"] or {}
        print(f"  running (PID {web['pid']}, port {info.get('port', 8080)})")
    elif web["state"] == "degraded":
        print(f"  DEGRADED (PID {web['pid']})")
    else:
        print("  stopped")
    print("=== A2A gateway ===")
    if a2a["state"] == "running":
        print(f"  running (PID {a2a['pid']}, agent "
              f"{a2a.get('agent_name') or 'unknown'}, port "
              f"{(a2a['pid_file'] or {}).get('port', 8090)})")
    elif a2a["state"] == "degraded":
        print(f"  DEGRADED (PID {a2a['pid']})")
    else:
        print("  stopped (optional — provision agent secrets to enable it)")
    orgs = payload["organizations"]
    if isinstance(orgs, list):
        print(f"=== organizations === {len(orgs)} active")
        for org in orgs:
            print(f"  - {org.get('organization_name')}")
    else:
        print("=== organizations === unavailable (server stopped or token missing)")
    if backups:
        print("=== recent backups ===")
        for b in backups:
            print(f"  - {b['name']} ({b['size']} bytes, {b['created_at'] or '?'})")
    return EXIT_OK
