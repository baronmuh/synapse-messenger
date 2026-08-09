"""``policy`` group (SPEC_CLI §4.9): policies, escalation, delegations.

Documented API deviations (docs/SPEC_CLI_ECARTS.md):

* ``delegate``: the ``create_delegation`` API delegates a TASK to an agent
  with a due date (not "capabilities") — the command therefore takes
  ``--task <id>`` and ``--expires <timestamp>`` (both required);
* ``revoke``: the API revokes by (task, delegatee) — ``revoke <agent>
  --task <id>``.
"""

from __future__ import annotations

import argparse

from ..client import ApiClientError, Client, ClientTransportError
from .common import (
    EXIT_OK,
    emit,
    api_error,
    emit_error,
    normalize_datetime,
    resolve_config,
    resolve_identity,
    resolve_org_auth,
    table,
)

GROUP = "policy"

_EXAMPLES = """\
Exemples :
  synapse policy show acme --json
  synapse policy set acme --allow-outgoing-external
  synapse policy escalation acme --json
  synapse policy escalation acme --set --max-hours 24 --targets support
  synapse policy delegate data --task t-42 --expires 2026-09-01T00:00:00Z
  synapse policy revoke data --task t-42
  synapse policy delegations --my-name directeur --json
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="policys (show, set, escalation, delegate, revoke, delegations)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("show", parents=[common],
                           help="current policies of the organization")
    a.add_argument("org", help="organization name")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_show)

    a = actions.add_parser("set", parents=[common],
                           help="modifies the organization policies")
    a.add_argument("org", help="organization name")
    a.add_argument("--allow-incoming-external", action="store_true",
                   help="allow external incoming")
    a.add_argument("--deny-incoming-external", action="store_true",
                   help="refuse external incoming")
    a.add_argument("--allow-outgoing-external", action="store_true",
                   help="allow external outgoing")
    a.add_argument("--deny-outgoing-external", action="store_true",
                   help="refuse external outgoing")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_set)

    a = actions.add_parser("escalation", parents=[common],
                           help="escalation policy (read); --set to write")
    a.add_argument("org", help="organization name")
    a.add_argument("--set", action="store_true",
                   help="write the policy")
    a.add_argument("--max-hours", type=int, default=None,
                   help="late threshold in hours (due delay)")
    a.add_argument("--targets", default=None,
                   help="target agent of the escalation (a single one, commas not "
                        "supported by the API)")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_escalation)

    a = actions.add_parser("delegate", parents=[common],
                           help="delegates a task to an agent (create_delegation)")
    a.add_argument("agent", help="delegatee agent")
    a.add_argument("--task", required=True, help="identifier of the delegated task")
    a.add_argument("--expires", required=True,
                   help="delegation due date (ISO timestamp, required)")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_delegate)

    a = actions.add_parser("revoke", parents=[common],
                           help="revokes a delegation (revoke_delegation)")
    a.add_argument("agent", help="delegatee agent")
    a.add_argument("--task", required=True,
                   help="identifier of the delegated task")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_revoke)

    a = actions.add_parser("delegations", parents=[common],
                           help="delegations received by the current agent")
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_delegations)


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


def _client(config) -> Client:  # noqa: ANN001
    return Client.from_config(config)


def _cmd_show(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args, org_name=args.org)
    try:
        data = _client(config).get_organization_policy(org, password)
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [[k, str(v)] for k, v in sorted(data.items())]
    print(table(rows, ["policy", "value"]))
    return EXIT_OK


def _cmd_set(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args, org_name=args.org)
    try:
        current = _client(config).get_organization_policy(org, password)
        incoming = current.get("allow_incoming_external", False)
        outgoing = current.get("allow_outgoing_external", False)
        if args.allow_incoming_external:
            incoming = True
        if args.deny_incoming_external:
            incoming = False
        if args.allow_outgoing_external:
            outgoing = True
        if args.deny_outgoing_external:
            outgoing = False
        data = _client(config).set_organization_policy(
            incoming, outgoing, org, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    return emit(
        args, data,
        f"Policies of '{args.org}': incoming external "
        f"{'allowed' if incoming else 'denied'}, outgoing external "
        f"{'allowed' if outgoing else 'denied'}.",
    )


def _cmd_escalation(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args, org_name=args.org)
    client = _client(config)
    if not args.set:
        try:
            data = client.get_escalation_policy(org, password)
        except (ApiClientError, ClientTransportError) as exc:
            return api_error(exc)
        if getattr(args, "json", False):
            return emit(args, data)
        rows = [[k, str(v)] for k, v in sorted(data.items())]
        print(table(rows, ["escalation policy", "value"]))
        return EXIT_OK
    # Write: defaults = current policy (read first).
    try:
        current = client.get_escalation_policy(org, password)
        enabled = True
        due_after_seconds = args.max_hours * 3600 if args.max_hours is not None \
            else (current.get("due_after_seconds") or 3600)
        failed_after_seconds = current.get("failed_after_seconds") or 3600
        targets = args.targets or current.get("escalate_to_username")
        if not targets:
            return emit_error(
                "--targets <agent> required (no current targets)"
            )
        data = client.set_escalation_policy(
            enabled, due_after_seconds, failed_after_seconds, targets, org, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    return emit(args, data,
                f"Escalation policy of '{args.org}' updated "
                f"(target: {targets}, max delay: {args.max_hours or 'current'} h).")


def _cmd_delegate(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    expires = normalize_datetime(args.expires) or args.expires
    try:
        data = _client(config).create_delegation(
            args.task, args.agent, expires, my_name, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    return emit(args, data,
                f"Task {args.task} delegated to {args.agent} "
                f"(due {args.expires}).")


def _cmd_revoke(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).revoke_delegation(
            args.task, args.agent, my_name, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    return emit(args, data,
                f"Delegation of task {args.task} to {args.agent} revoked.")


def _cmd_delegations(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).get_my_delegations(
            my_name, password, limit=args.limit, cursor=args.cursor
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [
        [d.get("delegator_username", ""), d.get("task_id", ""),
         d.get("expires_at", "")]
        for d in data.get("delegations", [])
    ]
    print(table(rows, ["delegator", "task", "due"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK

