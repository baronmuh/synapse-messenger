"""Unified ``synapse`` CLI (SPEC_CLI.md) — command tree and dispatch.

Structure : ``synapse <groupe> <action> [options]`` (maximum 2 niveaux,
decision §7.5). Bare ``synapse`` = idempotent ``server start`` (§7.2).
Help is in English and documents each group with examples (§5.5).
"""

from __future__ import annotations

import argparse
import sys

from . import a2a, agent, api, backup, diag, event, group as group_mod
from . import logs, message, org, policy, server, status, task, update, web
from .common import PROG, CliError, Parser

_GROUPS = (server, web, org, agent, message, task, group_mod, policy, event,
           api, backup, a2a, logs, diag, update, status)

_ROOT_HELP = """\
Synapse — secure messaging for organizations of AI agents.

Usage :
  synapse <group> <action> [options]     structured command
  synapse api <command> [options...]     raw access to any service command
  synapse                                 equivalent to "synapse server start"

Groupes :
  server   server start, stop, status, logs, config
  web      interface web (start, stop, restart, status, logs)
  org      organisations (init, list, status, enable, disable, password,
           agents, structure, metrics, audit)
  agent    comptes agents (create, status, card, budget, observers…)
  message  messaging (send, inbox, conversation, read, notifications)
  task     tasks (list, create, status, update, approve, transfer, my-work)
  group    groupes de discussion (create, members, send, messages…)
  policy   policys (show, set, escalation, delegate, revoke, delegations)
  event    event journal (stream, retention)
  api      raw access to ANY service command (evolution)
  backup   sauvegarde et restauration (create, restore, list)
  a2a      interoperability bridge (start, stop, status)
  logs     merged server + web logs
  diag     diagnostics (detailed status, doctor)
  update   updates (check, apply)
  status   global state
  version  installed version (or --version)

General options:
  --config <path>      configuration (else $SYNAPSE_CONFIG, then default)
  --json                machine JSON output sur les commandes de lecture
  --password-stdin      lire le(s) mot(s) de passe sur stdin (jamais en argument)
  --my-name <account>  agent account identity
  --organization-name   organisation (jeton local ou mot de passe)

Exit codes: 0 success; 1 error; 3 service unavailable;
4 already running (starting an already active service).

Use "synapse <group> <action> --help" for details and examples.
"""


def _common_parent() -> argparse.ArgumentParser:
    parent = Parser(add_help=False, prog=PROG)
    parent.add_argument("--config", default=None, dest="config",
                        help="path of the JSON configuration file")
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(prog=PROG, description="Synapse — unified CLI",
                    formatter_class=argparse.RawDescriptionHelpFormatter)
    # Root options: used by bare "synapse" (= server start, §4.1);
    # subcommands carry their own options (--config resolved
    # via config ou config_root).
    parser.add_argument("--config", default=None, dest="config_root",
                        help="path of the JSON configuration file")
    parser.add_argument("--foreground", action="store_true", dest="fg_root",
                        help="stay in the foreground (bare `synapse` = server start)")
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                        default=None, dest="log_root",
                        help="niveau de journalisation")
    parser.add_argument("--version", action="store_true", dest="show_version",
                        help="prints the installed version and exits")
    parser.set_defaults(command=None)

    sub = parser.add_subparsers(dest="command")
    common = _common_parent()

    # "synapse help": general help (decision §7.2 — help stays
    # disponible sans server).
    help_p = sub.add_parser("help", help="prints the general help", add_help=False)
    help_p.set_defaults(run=_cmd_help)

    # "synapse version": installed version (SPEC_PRODUCTION §5).
    version_p = sub.add_parser("version", help="prints the installed version",
                               add_help=False)
    version_p.set_defaults(run=_cmd_version)

    for group in _GROUPS:
        group.add_parser(sub, common)

    # Hidden internal group: detached processes (synapse _daemon …).
    daemon_group = sub.add_parser(
        "_daemon", help=argparse.SUPPRESS, add_help=False
    )
    daemon_actions = daemon_group.add_subparsers(dest="daemon_service")
    d = daemon_actions.add_parser("server", add_help=False)
    d.add_argument("--config", default=None)
    d.add_argument("--log-level", default=None)
    d.set_defaults(daemon_run="server")
    d = daemon_actions.add_parser("web", add_help=False)
    d.add_argument("--config", default=None)
    d.add_argument("--port", type=int, default=8080)
    d.add_argument("--log-level", default=None)
    d.set_defaults(daemon_run="web")
    d = daemon_actions.add_parser("a2a", add_help=False)
    d.add_argument("--config", default=None)
    d.add_argument("--agent-name", required=True)
    d.add_argument("--port", type=int, default=8090)
    d.add_argument("--token", required=True)
    d.add_argument("--log-level", default=None)
    d.set_defaults(daemon_run="a2a")

    return parser


def _run_daemon(args: argparse.Namespace) -> int:
    from . import daemon

    if args.daemon_run == "server":
        daemon.run_server_daemon(args.config, args.log_level)
        return 0
    if args.daemon_run == "web":
        daemon.run_web_daemon(args.config, args.port, args.log_level)
        return 0
    # a2a : le mot de passe de l'agent arrive sur stdin (pipe du parent).
    import sys as _sys

    password = _sys.stdin.readline().rstrip("\n")
    if not password:
        print("synapse _daemon a2a: mot de passe de l'agent absent",
              file=_sys.stderr)
        return 1
    daemon.run_a2a_daemon(args.config, args.agent_name, args.port, args.token,
                          args.log_level, password)
    return 0


def _cmd_help(_args: argparse.Namespace) -> int:
    print(_ROOT_HELP)
    return 0


def _cmd_version(_args: argparse.Namespace) -> int:
    from .common import project_version

    print(project_version())
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point of the unified CLI. Returns the exit code (0 by default)."""
    from ..platform import ensure_utf8_stdio

    ensure_utf8_stdio()
    if argv is None:
        argv = sys.argv[1:]

    # Raw access: "synapse [--config X] api <command> [options...]".
    # Arbitrary options (service parameters) do NOT go through
    # argparse: everything after "api" is handled by api.run_raw.
    api_idx = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "api":
            api_idx = i
            break
        if tok in ("--config", "--log-level") and i + 1 < len(argv):
            i += 2  # saute la valeur de l'option racine
            continue
        i += 1
    if api_idx is not None:
        from .api import run_raw

        try:
            return run_raw(argv[api_idx + 1:], prefix=argv[:api_idx])
        except CliError as exc:
            from .common import emit_error

            return emit_error(exc.message, code=exc.code)

    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    args.api_params = unknown

    # "synapse --version": installed version, without server (SPEC_CLI §4.18).
    if getattr(args, "show_version", False):
        from .common import project_version

        print(project_version())
        return 0

    # "synapse" bare = idempotent server start (decision §7.2).
    if args.command is None:
        args.command = "server"
        args.action = "start"
        args.foreground = args.fg_root
        args.log_level = args.log_root
        args.config = getattr(args, "config_root", None)
        return server._cmd_start(args)

    if args.command == "_daemon":
        return _run_daemon(args)

    if args.command != "api" and unknown:
        parser.error(f"arguments inattendus : {' '.join(unknown)}")

    handler = getattr(args, "run", None)
    if handler is None:
        parser.error(f"missing action for the group '{args.command}'")
    try:
        return handler(args)
    except CliError as exc:
        from .common import emit_error

        return emit_error(exc.message, code=exc.code)
    except SystemExit as exc:  # raised by the handlers (exit codes)
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BrokenPipeError:
        # stdout closed upstream (e.g. "synapse … | head"): silent output.
        import os

        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
