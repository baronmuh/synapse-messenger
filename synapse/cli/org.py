"""``org`` group (SPEC_CLI §4.4): organizations and human accounts.

``init`` and ``enable`` are LOCAL procedures (direct access to the
database, transversal rules 6): organization creation and thaw — never
a remote API to thaw (SPEC-WEB §4).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from ..client import ApiClientError, Client, ClientTransportError
from ..install import create_organization, enable_organization
from ..validation import human_username_for
from .common import (
    EXIT_OK,
    CliError,
    emit,
    emit_error,
    getpass_get,
    read_password,
    read_web_token,
    resolve_config,
    resolve_human_auth,
    resolve_org_auth,
    table,
)

GROUP = "org"

_EXAMPLES = """\
Examples:
  echo "motdepasse-acme-1" | synapse org init acme --password-stdin
  synapse org list --json                 active organizations
  synapse org list --all                  active + deactivated (human account)
  synapse org status acme --json          state of an organization
  synapse org enable acme                 local thaw (org password)
  synapse org disable acme                absolute freeze (irreversible via API)
  synapse org password acme               rotation of the password
  synapse org agents acme --json          organization agents
  synapse org structure acme --json       org chart
  synapse org metrics acme --json         metrics
  synapse org audit acme --limit 50       audit journal
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="organizations (init, list, status, enable, disable, audit…)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("init", parents=[common],
                           help="creates an organization + its human account (local procedure)")
    a.add_argument("name", nargs="?", default=None,
                   help="organization name (otherwise prompted interactively)")
    a.add_argument("--password-stdin", action="store_true",
                   help="read the password from stdin")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_init)

    a = actions.add_parser("list", parents=[common],
                           help="lists the active organizations")
    a.add_argument("--all", action="store_true",
                   help="include deactivated ones (human account required)")
    a.add_argument("--json", action="store_true", help="machine JSON output")
    a.add_argument("--organization-name", default=None,
                   help="organization whose human account authenticates (default: the only one)")
    a.set_defaults(run=_cmd_list)

    a = actions.add_parser("status", parents=[common],
                           help="organization state (active/deactivated, agents, metrics)")
    a.add_argument("name", help="organization name")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_status)

    a = actions.add_parser("enable", parents=[common],
                           help="locally reactivates a deactivated organization (freeze lifted)")
    a.add_argument("name", help="organization name")
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_enable)

    a = actions.add_parser("disable", parents=[common],
                           help="deactivates an organization (absolute freeze)")
    a.add_argument("name", help="organization name")
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_disable)

    a = actions.add_parser("password", parents=[common],
                           help="rotation of the organization password")
    a.add_argument("name", help="organization name")
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_password)

    a = actions.add_parser("agents", parents=[common],
                           help="lists the organization agents")
    a.add_argument("name", help="organization name")
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_agents)

    a = actions.add_parser("structure", parents=[common],
                           help="full org chart of the organization")
    a.add_argument("name", help="organization name")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_structure)

    a = actions.add_parser("metrics", parents=[common],
                           help="organization metrics")
    a.add_argument("name", help="organization name")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_metrics)

    a = actions.add_parser("audit", parents=[common],
                           help="organization audit journal")
    a.add_argument("name", help="organization name")
    a.add_argument("--limit", type=int, default=20, help="number of entries (default: 20)")
    a.add_argument("--cursor", default=None)
    a.add_argument("--actor", default=None, help="filter by actor")
    a.add_argument("--since", default=None, help="filter from a timestamp")
    a.add_argument("--command", dest="command_filter", default=None,
                   help="filter by command")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_audit)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _client(config) -> Client:  # noqa: ANN001
    return Client.from_config(config)


def _cmd_init(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    name = args.name
    if name is None:
        try:
            name = input("Organization name: ")
        except (EOFError, KeyboardInterrupt):
            return emit_error("operation canceled")
        if not name.strip():
            return emit_error("empty organization name")
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
        if not password:
            return emit_error("empty password on stdin")
        confirm = None
    else:
        password = getpass_get("Password (>= 12 printable characters): ")
        confirm = getpass_get("Password confirmation: ")
    try:
        created = create_organization(config, name, password, confirm)
    except (ValueError, OSError, sqlite3.Error) as exc:
        return emit_error(str(exc))
    print(f"Organization '{created}' created successfully.")
    print("The password is never stored in clear text (Argon2id).")
    print(f"Human account created: {human_username_for(created)} (web access, SPEC-WEB).")
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.all:
        try:
            my_name, password = resolve_human_auth(config, args)
        except CliError as exc:
            if "aucune organisation active" in exc.message:
                return emit_error(
                    "no active organization to derive the human account from: "
                    "specify --organization-name (human account of an "
                    "active organization, or password)"
                )
            raise
    else:
        token = read_web_token(config)
        if token is None:
            my_name, password = resolve_human_auth(config, args)
        else:
            from ..service import _WEB_LOCAL

            my_name, password = _WEB_LOCAL, token
    try:
        data = _client(config).list_orgs(my_name, password,
                                         include_disabled=args.all)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [[o["organization_name"]] for o in data.get("organizations", [])]
    print(table(rows, ["active organizations"]))
    disabled = data.get("disabled")
    if disabled:
        print(table([[d["organization_name"]] for d in disabled],
                    ["deactivated organizations"]))
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    name = args.name
    client = _client(config)
    # Reading the snapshot requires the human account of the target organization.
    my_name, password = resolve_human_auth(config, args, org_name=name)
    try:
        snapshot = client.get_org_snapshot(my_name, password)
    except ApiClientError as exc:
        if exc.code == "AUTH_FAILED":
            return _status_disabled_org(config, args, name, client)
        return _api_error(exc)
    except ClientTransportError as exc:
        return _api_error(exc)
    try:
        metrics = client.get_org_metrics(name, password)
    except (ApiClientError, ClientTransportError):
        metrics = None
    payload = {
        "organization_name": name,
        "state": "active",
        "snapshot": snapshot,
        "metrics": metrics,
    }
    if getattr(args, "json", False):
        return emit(args, payload)
    print(f"Organization '{name}': ACTIVE")
    print(f"  agents         {len(snapshot.get('agents', []))} "
          f"({sum(1 for a in snapshot.get('agents', []) if a.get('status') == 'active')} active)")
    print(f"  tasks          {snapshot.get('tasks_by_state', {})}")
    print(f"  departments   {len(snapshot.get('departments', []))}")
    print(f"  messages/h     {snapshot.get('messages_last_hour', 0)}")
    if metrics:
        print(f"  total agents   {metrics.get('total_agents')}")
        print(f"  active         {metrics.get('active_agents')}")
    return EXIT_OK


def _status_disabled_org(config, args, name: str, client: Client) -> int:
    """AUTH_FAILED on the snapshot: the org is unknown or deactivated (absolute
    freeze — no read possible). We try to distinguish via the local list
    of deactivated ones (human account of another org or token)."""
    try:
        human, password = resolve_human_auth(config, args)
    except CliError:
        human = password = None
    if human is not None and password is not None \
            and human_username_for(name) != human:
        try:
            data = client.list_orgs(human, password, include_disabled=True)
            if name in {d["organization_name"] for d in data.get("disabled", [])}:
                payload = {"organization_name": name, "state": "disabled"}
                if getattr(args, "json", False):
                    return emit(args, payload)
                print(f"Organization '{name}': DEACTIVATED (absolute freeze — data "
                      "intact, no read possible while frozen)")
                print("Local thaw: synapse org enable <name> --password-stdin")
                return EXIT_OK
        except (ApiClientError, ClientTransportError, CliError):
            pass
    return emit_error(
        f"organization '{name}' unreachable: unknown or deactivated (the freeze "
        "blocks all reads) — check the name or thaw locally "
        "(synapse org enable)"
    )


def _cmd_enable(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    password = read_password(args, f"Password of the organization '{args.name}' : ")
    try:
        enabled = enable_organization(config, args.name, password)
    except ValueError as exc:
        message = str(exc)
        if "is not deactivated" in message:
            print(f"organization '{args.name}' is already active (nothing to do)")
            return EXIT_OK  # idempotent (SPEC_CLI §2)
        return emit_error(message)
    except (OSError, sqlite3.Error) as exc:
        return emit_error(str(exc))
    print(f"Organization '{enabled}' reactivated successfully (freeze lifted).")
    return EXIT_OK


def _cmd_disable(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_human_auth(config, args, org_name=args.name)
    try:
        data = _client(config).disable_org(args.name, my_name, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Organization '{args.name}' deactivated (absolute freeze).")


def _cmd_password(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.password_stdin:
        new_password = sys.stdin.readline().rstrip("\n")
        if not new_password:
            return emit_error("empty password on stdin")
    else:
        new_password = getpass_get("New password of the organization: ")
    org, password = resolve_org_auth(config, args, org_name=args.name)
    try:
        data = _client(config).change_organization_password(
            new_password, org, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data,
                f"Password of organization '{args.name}' changed "
                "(the human delegation follows automatically).")


def _cmd_agents(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_human_auth(config, args, org_name=args.name)
    try:
        data = _client(config).list_org_agents(
            my_name, password, limit=args.limit, cursor=args.cursor
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [[u] for u in data.get("usernames", [])]
    print(table(rows, [f"agents of '{args.name}'"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK


def _cmd_structure(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args, org_name=args.name)
    try:
        data = _client(config).get_org_structure(org, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    for dept in data.get("departments", []):
        print(f"{dept.get('department_name')} ({dept.get('role') or '—'})")
        for member in dept.get("members", []):
            print(f"  - {member}")
    return EXIT_OK


def _cmd_metrics(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args, org_name=args.name)
    try:
        data = _client(config).get_org_metrics(org, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [[k, str(v)] for k, v in sorted(data.items())]
    print(table(rows, ["metric", "value"]))
    return EXIT_OK


def _cmd_audit(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args, org_name=args.name)
    try:
        data = _client(config).get_org_audit(
            org, password, since=args.since, actor_username=args.actor,
            command=args.command_filter, limit=args.limit, cursor=args.cursor,
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [
        [str(e.get("at", "")), e.get("actor_username", ""), e.get("command", ""),
         e.get("outcome", "")]
        for e in data.get("entries", [])
    ]
    print(table(rows, ["timestamp", "actor", "command", "result"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK


def _api_error(exc: Exception) -> int:
    """API error: JSON envelope + code 1 (refusal) or 3 (transport)."""
    if isinstance(exc, ClientTransportError):
        return emit_error(f"service unavailable: {exc}", code=3)
    return emit_error(exc.message, api_code=exc.code)  # type: ignore[attr-defined]
