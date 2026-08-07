"""``agent`` group (SPEC_CLI §4.5): management of agent accounts."""

from __future__ import annotations

import argparse
import sys

from ..client import ApiClientError, Client, ClientTransportError
from .common import (
    getpass_get,
    EXIT_OK,
    emit,
    emit_error,
    resolve_config,
    resolve_identity,
    resolve_org_auth,
    table,
)

GROUP = "agent"

_EXAMPLES = """\
Exemples :
  synapse agent create support --department support --role employee
  synapse agent status comptable --json
  synapse agent description support "Handles incoming requests"
  synapse agent card support --json
  synapse agent card support --set --model synapse-agent-1 --sla "response < 1h"
  synapse agent department support support manager
  synapse agent visibility auditor hidden
  synapse agent budget data --max-active-tasks 5
  synapse agent password data --password-stdin
  synapse agent deactivate commercial / synapse agent reactivate commercial
  synapse agent find --capability audit --json
  synapse agent create-observer observer --password-stdin
  synapse agent revoke-observer observer
  synapse agent observers --json
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="agent accounts (create, status, card, budget, observers…)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("create", parents=[common],
                           help="creates an agent in the organization")
    a.add_argument("name", help="agent name (the _humain suffix is reserved)")
    a.add_argument("--password-stdin", action="store_true",
                   help="read the passwords from stdin (agent then org)")
    a.add_argument("--description", default="", help="description of the agent")
    a.add_argument("--department", default=None, help="department assignment")
    a.add_argument("--role", default=None, choices=["manager", "employee", "rh"])
    a.add_argument("--capability", action="append", default=None,
                   help="card capability (repeatable)")
    a.add_argument("--domain", default=None, help="card domain")
    a.add_argument("--visible", dest="visible", action="store_true",
                   help="whether the agent sees the organization directory")
    a.add_argument("--hidden", dest="visible", action="store_false",
                   help="the agent does not see the directory (default)")
    a.set_defaults(visible=False)
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_create)

    a = actions.add_parser("status", parents=[common],
                           help="full agent state (description, card, reputation)")
    a.add_argument("name", help="agent name")
    a.add_argument("--my-name", default=None, help="account identity (otherwise human/token)")
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_status)

    a = actions.add_parser("description", parents=[common],
                           help="replaces the description of an agent")
    a.add_argument("name", help="agent name")
    a.add_argument("text", help="nouvelle description")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_description)

    a = actions.add_parser("card", parents=[common],
                           help="agent card (read); --set to write")
    a.add_argument("name", help="agent name")
    a.add_argument("--set", action="store_true",
                   help="write: sets the card of the authenticated account")
    a.add_argument("--capability", action="append", default=None,
                   help="capability (repeatable)")
    a.add_argument("--model", default=None, help="model")
    a.add_argument("--sla", default=None, help="SLA")
    a.add_argument("--estimated-cost", default=None, help="estimated cost")
    a.add_argument("--limits", default=None, help="limites")
    a.add_argument("--domain", default=None, help="domaine")
    a.add_argument("--my-name", default=None, help="account identity (write)")
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_card)

    a = actions.add_parser("department", parents=[common],
                           help="assigns an agent to a department (and a role)")
    a.add_argument("name", help="agent name")
    a.add_argument("department", help="department name")
    a.add_argument("--role", default="employee",
                   choices=["manager", "employee", "rh"],
                   help="role in the department (default: employee)")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_department)

    a = actions.add_parser("visibility", parents=[common],
                           help="agent visibility in the directory")
    a.add_argument("name", help="agent name")
    a.add_argument("value", choices=["visible", "hidden"],
                   help="visible or hidden")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_visibility)

    a = actions.add_parser("budget", parents=[common],
                           help="budgets of an agent (active tasks, messages/hour)")
    a.add_argument("name", help="agent name")
    a.add_argument("montant", nargs="?", default=None,
                   help="reserved: the API has no monetary budget (see --help)")
    a.add_argument("--max-active-tasks", type=int, default=None)
    a.add_argument("--max-messages-per-hour", type=int, default=None)
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_budget)

    a = actions.add_parser("password", parents=[common],
                           help="rotation of an agent's password")
    a.add_argument("name", help="agent name")
    a.add_argument("--password-stdin", action="store_true",
                   help="read the passwords from stdin (new then org)")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_password)

    a = actions.add_parser("deactivate", parents=[common],
                           help="deactivates an agent")
    a.add_argument("name", help="agent name")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_deactivate)

    a = actions.add_parser("reactivate", parents=[common],
                           help="reactivates an agent")
    a.add_argument("name", help="agent name")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_reactivate)

    a = actions.add_parser("find", parents=[common],
                           help="agent search (name, capabilities, domain)")
    a.add_argument("motif", nargs="?", default=None,
                   help="pattern in the agent name")
    a.add_argument("--capability", default=None)
    a.add_argument("--domain", default=None)
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_find)

    a = actions.add_parser("create-observer", parents=[common],
                           help="creates an observer account (metadata read)")
    a.add_argument("name", help="observer name")
    a.add_argument("--password-stdin", action="store_true",
                   help="read the passwords from stdin (observer then org)")
    a.add_argument("--description", default="",
                   help="description de l'observer")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_create_observer)

    a = actions.add_parser("revoke-observer", parents=[common],
                           help="revokes an observer account")
    a.add_argument("name", help="observer name")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_revoke_observer)

    a = actions.add_parser("observers", parents=[common],
                           help="lists the observer accounts")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_observers)


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


def _client(config) -> Client:  # noqa: ANN001
    return Client(config.socket_path)


def _cmd_create(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.name.endswith("_humain"):
        return emit_error(
            f"name refused: the '_humain' suffix is reserved for human accounts "
            f"(SPEC-WEB)"
        )
    description = args.description or f"Agent {args.name} (created via CLI)"
    if args.password_stdin:
        new_password = sys.stdin.readline().rstrip("\n")
        if not new_password:
            return emit_error("empty password on stdin")
    else:
        new_password = getpass_get(f"Password of the new agent '{args.name}' : ")
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).create_agent(
            args.name, new_password, description, org, password,
            can_see_org_agents=args.visible,
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if args.department is not None:
        try:
            _client(config).set_agent_department(
                args.name, args.department, args.role or "employee", org, password
            )
        except ApiClientError as exc:
            # The department may not exist: create it, then
            # retry (SPEC_CLI §4.5 ergonomics — no simulated behavior).
            if exc.code == "USER_NOT_FOUND" and "Department" in exc.message:
                try:
                    _client(config).create_department(args.department, org, password)
                    _client(config).set_agent_department(
                        args.name, args.department, args.role or "employee",
                        org, password,
                    )
                except (ApiClientError, ClientTransportError) as retry_exc:
                    return _api_error(retry_exc)
            else:
                return _api_error(exc)
        except ClientTransportError as exc:
            return _api_error(exc)
    if args.capability:
        try:
            # La card est celle du compte AUTHENTIFIÉ : on utilise le mot de
            # passe du nouvel agent (jamais celui de l'organisation — le
            # jeton local ne s'applique pas aux comptes agents).
            _client(config).set_agent_card(
                args.capability, args.name, new_password, domain=args.domain
            )
        except (ApiClientError, ClientTransportError) as exc:
            return _api_error(exc)
    return emit(args, data, f"Agent '{args.name}' created (state {data.get('status')}).")


def _cmd_status(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    client = _client(config)
    try:
        description = client.get_agent_description(args.name, my_name, password)
        card = client.get_agent_card(args.name, my_name, password)
        reputation = client.get_agent_reputation(args.name, my_name, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    payload = {"username": args.name, "description": description,
               "card": card, "reputation": reputation}
    if getattr(args, "json", False):
        return emit(args, payload)
    print(f"Agent '{args.name}'")
    print(f"  description   {description.get('description', '—')}")
    card_public = card.get("card") or card
    if isinstance(card_public, dict):
        print(f"  card         validation {card_public.get('validation_state', '—')}")
        print(f"                capabilities   {card_public.get('capabilities') or '—'}")
        print(f"                model      {card_public.get('model') or '—'}")
        print(f"                SLA         {card_public.get('sla') or '—'}")
    rep = reputation if isinstance(reputation, dict) else None
    if rep is not None:
        if "qualitative" in rep:
            print(f"  reputation    {rep.get('qualitative') or '—'}")
        else:
            print(f"  reputation    completion {rep.get('completion_rate') or '—'} "
                  f"(t{rep.get('completed', 0)} / e{rep.get('failed', 0)} / "
                  f"a{rep.get('active', 0)} / c{rep.get('canceled', 0)})")
    return EXIT_OK


def _cmd_description(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).change_agent_description(
            args.name, args.text, org, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Description of agent '{args.name}' replaced.")


def _cmd_card(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    client = _client(config)
    if args.set:
        if not args.capability and not any(
            (args.model, args.sla, args.estimated_cost, args.limits, args.domain)
        ):
            return emit_error(
                "--set requires at least one value (--capability, --model, --sla, "
                "--estimated-cost, --limits, --domain)"
            )
        # The set_agent_card API only sets the card of the AUTHENTICATED account:
        # the positional name must therefore be the account identity (--my-name).
        my_name = args.my_name
        if my_name is None:
            return emit_error(
                "card writing requires the account identity: "
                "--my-name <agent> (the API only allows setting the card "
                "of the authenticated account)"
            )
        if my_name != args.name:
            return emit_error(
                f"--my-name ({my_name}) must designate the account whose "
                f"card of agent ({args.name})"
            )
        my, password = resolve_agent_auth_or(args, config, my_name)
        try:
            data = client.set_agent_card(
                args.capability or [], my, password,
                domain=args.domain, model=args.model, sla=args.sla,
                limits=args.limits, estimated_cost=args.estimated_cost,
            )
        except (ApiClientError, ClientTransportError) as exc:
            return _api_error(exc)
        card_public = data.get("card") or data
        state = card_public.get("validation_state") if isinstance(card_public, dict) else None
        return emit(args, data,
                    f"Card of agent '{args.name}' soumise (validation : {state}).")
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = client.get_agent_card(args.name, my_name, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    card_public = data.get("card") or data
    if isinstance(card_public, dict):
        rows = [[k, str(v)] for k, v in sorted(card_public.items())]
        print(table(rows, ["champ", "valeur"]))
    else:
        print(data)
    return EXIT_OK


def resolve_agent_auth_or(args: argparse.Namespace, config, my_name: str) -> tuple[str, str]:  # noqa: ANN001
    from .common import resolve_agent_auth

    return resolve_agent_auth(config, args, my_name=my_name)


def _cmd_department(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).set_agent_department(
            args.name, args.department, args.role, org, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data,
                f"Agent '{args.name}' assigned to department '{args.department}' "
                f"(role {args.role}).")


def _cmd_visibility(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args)
    visible = args.value == "visible"
    try:
        data = _client(config).set_agent_visibility(
            args.name, visible, org, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data,
                f"Agent '{args.name}' {'visible' if visible else 'hidden'} in "
                "the directory.")


def _cmd_budget(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.montant is not None:
        return emit_error(
            "the API has no monetary budget: budgets are "
            "--max-active-tasks <n> et --max-messages-per-hour <n>"
        )
    if args.max_active_tasks is None and args.max_messages_per_hour is None:
        return emit_error(
            "no budget provided: --max-active-tasks <n> or "
            "--max-messages-per-hour <n> (both at 0 remove the limits)"
        )
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).set_agent_budget(
            args.name, org, password,
            max_active_tasks=args.max_active_tasks,
            max_messages_per_hour=args.max_messages_per_hour,
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Budgets of agent '{args.name}' updated.")


def _cmd_password(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.password_stdin:
        new_password = sys.stdin.readline().rstrip("\n")
        if not new_password:
            return emit_error("empty password on stdin")
    else:
        new_password = getpass_get(f"New password of agent '{args.name}' : ")
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).change_agent_password(
            args.name, new_password, org, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Password of agent '{args.name}' changed.")


def _cmd_deactivate(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).deactivate_agent(args.name, org, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Agent '{args.name}' deactivated.")


def _cmd_reactivate(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).reactivate_agent(args.name, org, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Agent '{args.name}' reactivated.")


def _cmd_find(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).find_agents(
            my_name, password,
            capability=args.capability, domain=args.domain,
            name_contains=args.motif, limit=args.limit,
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    agents = data.get("agents") or data.get("usernames") or []
    if isinstance(agents, list) and agents and isinstance(agents[0], dict):
        rows = [[a.get("username", ""), a.get("description", "")] for a in agents]
        print(table(rows, ["agent", "description"]))
    else:
        print(table([[str(a)] for a in agents], ["agents"]))
    if data.get("next_cursor"):
        print(f"(page suivante : --cursor {data['next_cursor']})")
    return EXIT_OK


def _cmd_create_observer(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.password_stdin:
        observer_password = sys.stdin.readline().rstrip("\n")
        if not observer_password:
            return emit_error("empty password on stdin")
    else:
        observer_password = getpass_get(
            f"Password of the observer '{args.name}' : "
        )
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).create_observer_account(
            args.name, observer_password, args.description, org, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Observer '{args.name}' created (read-only).")


def _cmd_revoke_observer(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).revoke_observer_account(args.name, org, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Observer '{args.name}' revoked.")


def _cmd_observers(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).list_observers(org, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    observers = data.get("observers", [])
    if isinstance(observers, list) and observers and isinstance(observers[0], dict):
        rows = [[o.get("observer_name") or o.get("username", ""),
                 o.get("description", "")] for o in observers]
        print(table(rows, ["observer", "description"]))
    else:
        print(table([[str(o)] for o in observers], ["observers"]))
    return EXIT_OK


def _api_error(exc: Exception) -> int:
    if isinstance(exc, ClientTransportError):
        return emit_error(f"service indisponible : {exc}", code=3)
    return emit_error(exc.message, api_code=exc.code)  # type: ignore[attr-defined]
