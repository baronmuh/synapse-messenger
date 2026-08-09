"""``group`` group (SPEC_CLI §4.8): discussion groups.

The API addresses groups by UUID identifier; the CLI accepts the NAME
(positional, SPEC_CLI) and resolves the identifier via ``list_my_groups``
(groups are personal to the authenticated account).
"""

from __future__ import annotations

import argparse

from ..client import ApiClientError, Client, ClientTransportError
from .common import (
    EXIT_OK,
    emit,
    api_error,
    emit_error,
    resolve_config,
    resolve_identity,
    table,
)

GROUP = "group"

_EXAMPLES = """\
Examples:
  synapse group create direction
  synapse group members direction --json
  synapse group add-member direction comptable
  synapse group remove-member direction comptable
  synapse group messages direction --limit 50 --json
  synapse group send direction "Weekly meeting at 10am" --my-name director
  synapse group list --my-name director --json
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="discussion groups (create, members, send, messages…)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("create", parents=[common], help="creates a group")
    a.add_argument("name", help="group name")
    a.add_argument("--description", default=None,
                   help="not supported by the create_group API (groups without "
                        "description)")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_create)

    a = actions.add_parser("members", parents=[common],
                           help="lists the members of a group")
    a.add_argument("name", help="group name")
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_members)

    a = actions.add_parser("add-member", parents=[common],
                           help="adds a member to a group")
    a.add_argument("name", help="group name")
    a.add_argument("member", help="member to add")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_add_member)

    a = actions.add_parser("remove-member", parents=[common],
                           help="removes a member from a group")
    a.add_argument("name", help="group name")
    a.add_argument("member", help="member to remove")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_remove_member)

    a = actions.add_parser("messages", parents=[common],
                           help="group messages")
    a.add_argument("name", help="group name")
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_messages)

    a = actions.add_parser("send", parents=[common],
                           help="sends a message to the group")
    a.add_argument("name", help="group name")
    a.add_argument("text", help="message content")
    a.add_argument("--client-message-id", default=None)
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_send)

    a = actions.add_parser("list", parents=[common],
                           help="groups of the current agent")
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_list)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _client(config) -> Client:  # noqa: ANN001
    return Client.from_config(config)


def _resolve_group(config, args, name: str) -> tuple[str, str, str]:  # noqa: ANN001
    """(group_id, my_name, password) : resolves the name via list_my_groups."""
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    client = _client(config)
    cursor = None
    while True:
        data = client.list_my_groups(my_name, password, limit=100, cursor=cursor)
        for group in data.get("groups", []):
            if group.get("name") == name:
                return group["group_id"], my_name, password
        cursor = data.get("next_cursor")
        if not cursor:
            break
    raise CliGroupError(
        f"group '{name}' not found among your groups (list_my_groups)"
    )


class CliGroupError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


def _cmd_create(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.description is not None:
        return emit_error(
            "the create_group API does not support a description (groups "
            "are without description — SPEC.txt F15)"
        )
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).create_group(args.name, my_name, password)
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    return emit(args, data,
                f"Group '{args.name}' created ({data.get('group_id')}).")


def _cmd_members(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    try:
        group_id, my_name, password = _resolve_group(config, args, args.name)
        data = _client(config).get_group_members(
            group_id, my_name, password, limit=args.limit, cursor=args.cursor
        )
    except (ApiClientError, ClientTransportError, CliGroupError) as exc:
        return _group_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    members = data.get("members", [])
    rows = [[m.get("username", "")] for m in members] if members and isinstance(
        members[0], dict) else [[str(m)] for m in members]
    print(table(rows, [f"members of '{args.name}'"]))
    return EXIT_OK


def _cmd_add_member(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    try:
        group_id, my_name, password = _resolve_group(config, args, args.name)
        data = _client(config).add_group_member(
            group_id, args.member, my_name, password
        )
    except (ApiClientError, ClientTransportError, CliGroupError) as exc:
        return _group_error(exc)
    return emit(args, data, f"{args.member} added to group '{args.name}'.")


def _cmd_remove_member(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    try:
        group_id, my_name, password = _resolve_group(config, args, args.name)
        data = _client(config).remove_group_member(
            group_id, args.member, my_name, password
        )
    except (ApiClientError, ClientTransportError, CliGroupError) as exc:
        return _group_error(exc)
    return emit(args, data, f"{args.member} removed from group '{args.name}'.")


def _cmd_messages(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    try:
        group_id, my_name, password = _resolve_group(config, args, args.name)
        data = _client(config).get_group_messages(
            group_id, my_name, password, limit=args.limit, cursor=args.cursor
        )
    except (ApiClientError, ClientTransportError, CliGroupError) as exc:
        return _group_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    messages = data.get("messages", [])
    rows = [
        [m.get("created_at", ""), m.get("sender_username", ""), m.get("content", "")]
        for m in messages
    ]
    print(table(rows, ["timestamp", "from", "message"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK


def _cmd_send(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    try:
        group_id, my_name, password = _resolve_group(config, args, args.name)
        data = _client(config).send_group_message(
            group_id, args.text, my_name, password,
            client_message_id=args.client_message_id,
        )
    except (ApiClientError, ClientTransportError, CliGroupError) as exc:
        return _group_error(exc)
    return emit(args, data,
                f"Message sent to group '{args.name}' "
                f"({data.get('message_id', '')}).")


def _cmd_list(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).list_my_groups(
            my_name, password, limit=args.limit, cursor=args.cursor
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    groups = data.get("groups", [])
    rows = [
        [g.get("group_id", ""), g.get("name", "")] for g in groups
    ]
    print(table(rows, ["id", "name"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK


def _group_error(exc: Exception) -> int:
    if isinstance(exc, CliGroupError):
        return emit_error(exc.message)
    return api_error(exc)

