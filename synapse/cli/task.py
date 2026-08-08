"""``task`` group (SPEC_CLI §4.7): tasks and work queues."""

from __future__ import annotations

import argparse

from ..client import ApiClientError, Client, ClientTransportError
from .common import (
    EXIT_OK,
    emit,
    emit_error,
    normalize_datetime,
    resolve_config,
    resolve_identity,
    table,
)

GROUP = "task"

# The API uses English states/priorities; the CLI also accepts the
# documented French equivalents (SPEC_CLI) — local translation.
_STATES_FR = {
    "en_attente": "submitted",
    "soumise": "submitted",
    "en_cours": "in_progress",
    "terminee": "completed",
    "echec": "failed",
    "annulee": "canceled",
    "en_approbation": "pending_approval",
}
_PRIORITIES_FR = {"basse": "low", "haute": "high"}
_STATES = sorted(set(_STATES_FR.values()) | set(_STATES_FR))
_PRIORITIES = ["normal", "low", "high", "basse", "haute"]


def _state(value: str) -> str:
    return _STATES_FR.get(value, value)


def _priority(value: str) -> str:
    return _PRIORITIES_FR.get(value, value)


_EXAMPLES = """\
Examples:
  synapse task list --state en_attente --json
  synapse task create "Rapport mensuel" --assignee analyste --priority haute
  synapse task status t-42 --json
  synapse task update t-42 completed
  synapse task approve t-42 / synapse task reject t-42
  synapse task request-approval t-42 --approver directeur
  synapse task transfer t-42 support
  synapse task my-work --my-name directeur --json
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="tasks (list, create, status, update, approve, transfer, my-work)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("list", parents=[common], help="list the tasks")
    a.add_argument("--state", default=None, choices=_STATES,
                   help="filter by state (pending, in_progress, completed…)")
    a.add_argument("--assignee", default=None, help="filter by assignee")
    a.add_argument("--department", default=None,
                   help="tasks of a department (list_department_tasks)")
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_list)

    a = actions.add_parser("create", parents=[common],
                           help="creates a task (creator = authenticated account)")
    a.add_argument("title", help="title of the task")
    a.add_argument("--assignee", required=True, help="assignee (required)")
    a.add_argument("--priority", default=None, choices=_PRIORITIES,
                   help="normal, high/low (default: normal)")
    a.add_argument("--due", default=None, help="due date (ISO timestamp)")
    a.add_argument("--description", default=None, help="description")
    a.add_argument("--creator", default=None,
                   help="reserved: the creator is the authenticated account "
                        "(--my-name), not an API parameter")
    a.add_argument("--department", default=None,
                   help="reserved: create_task does not affect a department")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_create)

    a = actions.add_parser("status", parents=[common],
                           help="details of a task")
    a.add_argument("task_id", help="task identifier")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_status)

    a = actions.add_parser("update", parents=[common],
                           help="changes the state of a task")
    a.add_argument("task_id", help="task identifier")
    a.add_argument("state", choices=_STATES,
                   help="new state (in_progress, completed…)")
    a.add_argument("--result", default=None, help="result (for completed/failed)")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_update)

    a = actions.add_parser("approve", parents=[common],
                           help="approves a pending task")
    a.add_argument("task_id")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_approve)

    a = actions.add_parser("reject", parents=[common],
                           help="rejects a pending task")
    a.add_argument("task_id")
    a.add_argument("--reason", default=None, help="rejection reason")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_reject)

    a = actions.add_parser("request-approval", parents=[common],
                           help="requests approval of a task")
    a.add_argument("task_id")
    a.add_argument("--approver", required=True, help="approver (required)")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_request_approval)

    a = actions.add_parser("transfer", parents=[common],
                           help="transfers a task")
    a.add_argument("task_id")
    a.add_argument("assignee", help="new assignee")
    a.add_argument("--note", default=None, help="transfer note")
    a.add_argument("--my-name", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_transfer)

    a = actions.add_parser("my-work", parents=[common],
                           help="work queue of the current agent")
    a.add_argument("--my-name", default=None)
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--cursor", default=None)
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_my_work)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _client(config) -> Client:  # noqa: ANN001
    return Client.from_config(config)


def _cmd_list(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        if args.department:
            data = _client(config).list_department_tasks(
                args.department, my_name, password,
                limit=args.limit, cursor=args.cursor,
            )
        else:
            data = _client(config).list_tasks(
                my_name, password,
                assignee_username=args.assignee,
                state=_state(args.state) if args.state else None,
                limit=args.limit, cursor=args.cursor,
            )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    tasks = data.get("tasks", [])
    rows = [
        [t.get("task_id", ""), t.get("title", ""), t.get("state", ""),
         t.get("assignee_username", "")]
        for t in tasks
    ]
    print(table(rows, ["id", "title", "state", "assignee"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK


def _cmd_create(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.creator is not None:
        return emit_error(
            "--creator does not exist in the API: the creator is the account "
            "authenticated (identity via --my-name or local token)"
        )
    if args.department is not None:
        return emit_error(
            "--department is not supported by create_task (the tasks of a "
            "department are LISTED with 'synapse task list --department')"
        )
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).create_task(
            args.title, args.assignee, my_name, password,
            description=args.description,
            priority=_priority(args.priority) if args.priority else None,
            due_at=normalize_datetime(args.due),
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data,
                f"Task created: {data.get('task_id')} "
                f"(state {data.get('state')}, assignee {args.assignee})")


def _cmd_status(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).get_task(args.task_id, my_name, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    rows = [[k, str(v)] for k, v in sorted(data.items())]
    print(table(rows, ["field", "value"]))
    return EXIT_OK


def _cmd_update(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).update_task_state(
            args.task_id, _state(args.state), my_name, password,
            result=args.result,
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data,
                f"Task {args.task_id}: state {data.get('state')}")


def _cmd_approve(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).approve_task(args.task_id, my_name, password)
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Task {args.task_id} approved.")


def _cmd_reject(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).reject_task(
            args.task_id, my_name, password, reason=args.reason
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data, f"Task {args.task_id} rejected.")


def _cmd_request_approval(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).request_approval(
            args.task_id, args.approver, my_name, password
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data,
                f"Approval requested for {args.task_id} "
                f"(approver: {args.approver}).")


def _cmd_transfer(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).transfer_task(
            args.task_id, args.assignee, my_name, password, note=args.note
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    return emit(args, data,
                f"Task {args.task_id} transferred to {args.assignee}.")


def _cmd_my_work(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    my_name, password = resolve_identity(config, args, my_name=args.my_name)
    try:
        data = _client(config).get_my_work(
            my_name, password, limit=args.limit, cursor=args.cursor
        )
    except (ApiClientError, ClientTransportError) as exc:
        return _api_error(exc)
    if getattr(args, "json", False):
        return emit(args, data)
    tasks = data.get("tasks", [])
    rows = [
        [t.get("task_id", ""), t.get("title", ""), t.get("state", ""),
         t.get("due_at", "")]
        for t in tasks
    ]
    print(table(rows, ["id", "title", "state", "due"]))
    if data.get("next_cursor"):
        print(f"(next page: --cursor {data['next_cursor']})")
    return EXIT_OK


def _api_error(exc: Exception) -> int:
    if isinstance(exc, ClientTransportError):
        return emit_error(f"service unavailable: {exc}", code=3)
    return emit_error(exc.message, api_code=exc.code)  # type: ignore[attr-defined]
