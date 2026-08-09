"""``message`` group (SPEC_CLI §4.6): account messaging."""

from __future__ import annotations

import argparse
import uuid

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

GROUP = "message"

_EXAMPLES = """\
Exemples :
  synapse message send comptable "Facture transmise" --my-name commercial
  synapse message inbox --my-name support --unread --json
  synapse message conversation devops --my-name support
  synapse message read m-123 --my-name support
  synapse message mark-no-reply commercial --my-name comptable
  synapse message notifications --my-name director --json
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="messaging (send, inbox, conversation, read, notifications)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("send", parents=[common],
                           help="sends a message (agent or human)")
    a.add_argument("recipient", help="recipient")
    a.add_argument("text", help="message content")
    a.add_argument("--my-name", default=None,
                   help="sender account (otherwise the organization human via the token)")
    a.add_argument("--client-message-id", default=None,
                   help="client identifier (otherwise generated)")
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_send)

    a = actions.add_parser("inbox", parents=[common],
                           help="received messages, paginated")
    a.add_argument("--my-name", default=None)
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--unread", action="store_true", help="unread only")
    a.add_argument("--sender", default=None, help="filter by sender")
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_inbox)

    a = actions.add_parser("conversation", parents=[common],
                           help="conversation thread with an interlocutor")
    a.add_argument("other", help="interlocutor")
    a.add_argument("--my-name", default=None)
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_conversation)

    a = actions.add_parser("read", parents=[common],
                           help="marks a message as read (recipient)")
    a.add_argument("message_id", help="message identifier")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_read)

    a = actions.add_parser("mark-no-reply", parents=[common],
                           help='mark the conversation as "no reply"')
    a.add_argument("other", help="interlocutor of the conversation")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_mark_no_reply)

    a = actions.add_parser("notifications", parents=[common],
                           help="unread grouped by sender")
    a.add_argument("--my-name", default=None)
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_notifications)


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


def _client(config) -> Client:  # noqa: ANN001
    return Client.from_config(config)


def _cmd_send(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    client_message_id = args.client_message_id or str(uuid.uuid4())
    try:
        data = _client(config).send_message(
            args.recipient, args.text, client_message_id, my_name, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    return emit(
        args, data,
        f"Message sent to {data.get('recipient_username')} "
        f"({data.get('message_id')})",
    )


def _cmd_inbox(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).get_messages(
            my_name, password,
            status="unread" if args.unread else None,
            sender_username=args.sender, limit=args.limit, cursor=args.cursor,
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [
        [m.get("created_at", ""), m.get("sender_username", ""), m.get("content", "")]
        for m in data.get("messages", [])
    ]
    print(table(rows, ["timestamp", "from", "message"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK


def _cmd_conversation(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).get_conversation(
            args.other, my_name, password, limit=args.limit, cursor=args.cursor
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    print(f"Conversation with '{args.other}' "
          f"({data.get('reply_status', '')})")
    rows = [
        [m.get("created_at", ""), m.get("sender_username", ""), m.get("content", "")]
        for m in data.get("messages", [])
    ]
    print(table(rows, ["timestamp", "from", "message"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK


def _cmd_read(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).read_message(args.message_id, my_name, password)
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    return emit(args, data, f"Message {args.message_id} marked as read.")


def _cmd_mark_no_reply(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    client = _client(config)
    # The API marks a conversation by identifier: first resolve the
    # resolve the conversation with the interlocutor, then mark it.
    try:
        conversation = client.get_conversation(
            args.other, my_name, password, limit=1
        )
        messages = conversation.get("messages", [])
        if not messages:
            return emit_error(
                f"no conversation with '{args.other}' (or inaccessible)"
            )
        conversation_id = messages[0]["conversation_id"]
        data = client.mark_conversation_no_reply(
            conversation_id, my_name, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    return emit(args, data,
                f"Conversation with '{args.other}' marked as no-reply.")


def _cmd_notifications(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).get_notifications(
            my_name, password, limit=args.limit
        )
    except (ApiClientError, ClientTransportError) as exc:
        return api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    for section in ("needs_reply",):
        items = data.get(section, [])
        if not items:
            continue
        print(f"{section} :")
        rows = [
            [i.get("other_username", ""), str(i.get("unread_count", ""))]
            for i in items
        ]
        print(table(rows, ["interlocutor", "unread"]))
    unread = data.get("unread_by_sender", {}) or {}
    if unread:
        print("unread_by_sender :")
        rows = [[sender, str(count)] for sender, count in unread.items()]
        print(table(rows, ["interlocutor", "unread"]))
    if not data.get("needs_reply") and not unread:
        print("no notifications")
    return EXIT_OK

