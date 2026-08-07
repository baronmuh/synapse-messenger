"""``event`` group (SPEC_CLI §4.10): append-only event journal."""

from __future__ import annotations

import argparse

from ..client import ApiClientError, Client, ClientTransportError
from .common import (
    EXIT_OK,
    emit,
    emit_error,
    resolve_config,
    resolve_identity,
    resolve_org_auth,
    table,
)

GROUP = "event"

_EXAMPLES = """\
Exemples :
  synapse event stream --limit 100 --json
  synapse event retention 90
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="event journal (stream, retention)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("stream", parents=[common],
                           help="event stream (cursor pagination)")
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None,
                   help="opaque pagination cursor (--seq is not "
                        "supported by the API, cursor pagination only)")
    a.add_argument("--seq", default=None,
                   help="not supported: the API paginates by opaque cursor "
                        "(--cursor), not by sequence")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_stream)

    a = actions.add_parser("retention", parents=[common],
                           help="event retention duration (days)")
    a.add_argument("days", type=int, help="number of retention days")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_retention)


def _client(config) -> Client:  # noqa: ANN001
    return Client(config.socket_path)


def _cmd_stream(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.seq is not None:
        return emit_error(
            "'--seq' is not supported by the API (SPEC.txt F10: pagination by "
            "curseur opaque uniquement) — utilisez --cursor"
        )
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).get_events(
            my_name, password, limit=args.limit, cursor=args.cursor
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    events = data.get("events", [])
    rows = [
        [str(e.get("seq", "")), e.get("event_type", ""), e.get("at", ""),
         e.get("by_username", "")]
        for e in events
    ]
    print(table(rows, ["seq", "type", "horodatage", "acteur"]))
    if data.get("next_cursor"):
        print(f"(page suivante : --cursor {data['next_cursor']})")
    return EXIT_OK


def _cmd_retention(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    org, password = resolve_org_auth(config, args)
    try:
        data = _client(config).set_event_retention_days(args.days, org, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data,
                f"Event retention set to {args.days} days.")


def _api_error(exc: Exception) -> int:
    if isinstance(exc, ClientTransportError):
        return emit_error(f"service indisponible : {exc}", code=3)
    return emit_error(exc.message, api_code=exc.code)  # type: ignore[attr-defined]
