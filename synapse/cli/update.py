"""``update`` group (SPEC_CLI §4.16): checking and applying updates.

``apply`` runs the plan: automatic backup → clean stop of the
server (and web) → update command → restart. The update
command is configured via ``update_command`` (config) or the
environment variable ``SYNAPSE_UPDATE_COMMAND``; without it, the
update is explicitly refused (no simulated behavior).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from ..platform import default_paths
from .common import (
    EXIT_OK,
    emit,
    emit_error,
    project_version,
    resolve_config,
)

GROUP = "update"

_EXAMPLES = """\
Examples:
  synapse update check                 installed version vs remote channel
  synapse update apply --dry-run       plan without executing anything
  synapse update apply                 backup → stop → update → restart
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="updates (check, apply) — bare 'synapse update' = check + apply",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Bare ``synapse update``: check + apply in one step (simple path
    # for a non-technical operator). The sub-actions below are the
    # fine-grained controls.
    p.add_argument("--check-only", action="store_true",
                   help="only checks the available version (like 'update check')")
    p.add_argument("--dry-run", action="store_true",
                   help="shows the plan without doing anything")
    p.add_argument("--no-backup", action="store_true",
                   help="skips the automatic backup (not recommended)")
    p.add_argument("--yes", action="store_true",
                   help="applies without the interactive confirmation")
    p.add_argument("--json", action="store_true",
                   help="machine JSON output (check)")
    p.set_defaults(run=_cmd_simple)
    actions = p.add_subparsers(dest="action", required=False)

    a = actions.add_parser("check", parents=[common],
                           help="compares the installed version to the latest published one")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_check)

    a = actions.add_parser("apply", parents=[common],
                           help="backup → stop → update → restart")
    a.add_argument("--dry-run", action="store_true",
                   help="shows the plan without doing anything")
    a.add_argument("--no-backup", action="store_true",
                   help="skips the automatic backup (not recommended)")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_apply)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _update_url(config) -> str | None:  # noqa: ANN001
    return (config._extra.get("update_url") or os.environ.get("SYNAPSE_UPDATE_URL")
            or None)


def _update_command(config) -> str | None:  # noqa: ANN001
    return (config._extra.get("update_command")
            or os.environ.get("SYNAPSE_UPDATE_COMMAND") or None)


# ---------------------------------------------------------------------------
# systemd supervision detection (SPEC_PRODUCTION §1/§5)
#
# When the systemd units exist (production install via
# install.sh), the update drives systemctl: a CLI stop would be
# countered immediately by Restart=on-failure. Without systemd (dev,
# tests), the legacy CLI behavior is kept.
# ---------------------------------------------------------------------------


def _systemd_unit_exists(unit: str) -> bool:
    """True if the systemd unit is installed (``systemctl cat <unit>``).

    ``SYNAPSE_NO_SYSTEMD=1`` forces CLI mode (useful in tests and
    for an operator who wants to ignore the local systemd supervision)."""
    if os.environ.get("SYNAPSE_NO_SYSTEMD") == "1":
        return False
    try:
        result = subprocess.run(["systemctl", "cat", unit],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _a2a_instances() -> list[str]:
    """ACTIVE instances of the ``synapse-a2a@.service`` template."""
    if os.environ.get("SYNAPSE_NO_SYSTEMD") == "1":
        return []
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "--no-legend", "--no-pager",
             "synapse-a2a@*.service"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    units = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].endswith(".service") and parts[2] == "active":
            units.append(parts[0])
    return units


def _systemctl_stop(unit: str) -> bool:
    if os.environ.get("SYNAPSE_NO_SYSTEMD") == "1":
        return False
    try:
        result = subprocess.run(["systemctl", "stop", unit],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=60)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _systemctl_start(unit: str) -> bool:
    if os.environ.get("SYNAPSE_NO_SYSTEMD") == "1":
        return False
    try:
        result = subprocess.run(["systemctl", "start", unit],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=120)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _a2a_cli_restart(config, agent_name: str, port: int) -> bool:
    """Restarts the bridge via the CLI when the file secrets exist
    (non-systemd mode). Returns False if the secrets are absent — in that
    case, the operator must restart the bridge manually."""
    secrets_dir = os.environ.get("SYNAPSE_SECRETS_DIR") or default_paths()["secrets"]
    password_file = os.path.join(secrets_dir, f"a2a-{agent_name}.password")
    token_file = os.path.join(secrets_dir, f"a2a-{agent_name}.token")
    if not (os.path.isfile(password_file) and os.path.isfile(token_file)):
        return False
    try:
        password = Path(password_file).read_text(encoding="utf-8").rstrip("\n")
        token = Path(token_file).read_text(encoding="utf-8").rstrip("\n")
    except OSError:
        return False
    cmd = [sys.executable, "-m", "synapse.cli", "a2a", "start",
           "--agent-name", agent_name, "--port", str(port),
           "--password-stdin", "--token-stdin"]
    config_arg = _config_arg_path(config)
    if config_arg:
        cmd += ["--config", config_arg]
    try:
        result = subprocess.run(cmd, input=f"{password}\n{token}\n".encode(),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=60)
        return result.returncode == 0
    except OSError:
        return False


def _config_arg_path(config) -> str | None:  # noqa: ANN001
    return os.environ.get("SYNAPSE_CONFIG") or os.environ.get("Synapse_CONFIG")


def _env_port(name: str, default: int) -> int:
    """Port from the environment (``SYNAPSE_WEB_PORT``/``SYNAPSE_A2A_PORT``)."""
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _cmd_simple(args: argparse.Namespace) -> int:
    """Bare ``synapse update``: check then apply — one step for a
    non-technical operator. Reuses ``_cmd_check`` and ``_cmd_apply``
    (no duplicated logic).

    - If ``--check-only``: only the check (like ``update check``).
    - If already up to date: clear message, exit 0, NO action.
    - Otherwise: backup → stop → update → restart (like ``update apply``),
      then confirms the new installed version.
    """
    config = resolve_config(args)
    url = _update_url(config)
    local = project_version()

    if args.check_only:
        check_args = argparse.Namespace(**vars(args))
        check_args.json = getattr(args, "json", False)
        return _cmd_check(check_args)

    # Check first: what is the remote version?
    remote_version = None
    check_error = None
    if url:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            remote_version = payload.get("version")
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            check_error = str(exc)

    if check_error:
        print(f"Remote channel unreachable: {check_error}")
        print("No update applied.")
        return EXIT_OK

    if remote_version is None:
        print("No remote channel configured (SYNAPSE_UPDATE_URL) — "
              "nothing to compare.")
        return EXIT_OK

    if remote_version == local:
        print(f"Already up to date (installed {local}, remote {remote_version}).")
        return EXIT_OK

    print(f"Update available: {local} → {remote_version}.")
    if not args.yes:
        try:
            answer = input("Apply the update now? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Update canceled.")
            return EXIT_OK

    # Reuse the full apply path (backup → stop → command → restart).
    apply_args = argparse.Namespace(**vars(args))
    apply_args.dry_run = getattr(args, "dry_run", False)
    apply_args.no_backup = getattr(args, "no_backup", False)
    code = _cmd_apply(apply_args)
    if code != EXIT_OK:
        return code

    # Confirm the new installed version.
    from .common import project_version as _pv

    print(f"Now installed: {_pv()}")
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    local = project_version()
    url = _update_url(config)
    # up_to_date is None when unknown (no channel configured or the
    # channel payload has no version) — never a fake "True".
    payload = {"installed": local, "remote": None, "up_to_date": None,
               "channel": "no remote channel configured (SYNAPSE_UPDATE_URL)"}
    if url:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                remote = resp.read().decode("utf-8")

            data = json.loads(remote)
            remote_version = data.get("version")
            payload["remote"] = remote_version
            payload["channel"] = url
            if isinstance(remote_version, str):
                payload["up_to_date"] = remote_version == local
        except (OSError, urllib.error.URLError, TimeoutError,
                ValueError) as exc:
            payload["remote_error"] = str(exc)
            payload["up_to_date"] = None
    if getattr(args, "json", False):
        return emit(args, payload)
    print(f"Installed version: {local}")
    if payload.get("remote"):
        if payload["up_to_date"] is None:
            state = "unknown (the channel payload has no version field)"
        else:
            state = "up to date" if payload["up_to_date"] else "UPDATE AVAILABLE"
        print(f"Remote channel ({url}) : {payload['remote']} — {state}")
    elif payload.get("remote_error"):
        print(f"Remote channel unreachable: {payload['remote_error']}")
    else:
        print(f"{payload['channel']} — nothing to compare (local installation).")
    return EXIT_OK


def _cmd_apply(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    command = _update_command(config)

    server_unit = "synapse.service"
    web_unit = "synapse-web.service"
    server_managed = _systemd_unit_exists(server_unit)
    web_managed = _systemd_unit_exists(web_unit)
    a2a_instances = _a2a_instances() if server_managed else []

    _print_update_plan(command)
    if args.dry_run:
        print("(--dry-run: no changes made)")
        return EXIT_OK
    if not command:
        return emit_error(
            "no update command configured: set "
            "update_command in the configuration or SYNAPSE_UPDATE_COMMAND "
            "(ex. \"pip install --upgrade synapse-messenger\")"
        )
    print("In case of a mid-way failure, the previous state can be restored via "
          "'synapse backup restore <archive> --force'.")

    from . import backup as backup_group

    service_args = _service_args(args, _apply_web_port(config))

    if not args.no_backup:
        code = backup_group._cmd_create(service_args)
        if code != EXIT_OK:
            return emit_error("backup failed — update canceled")

    a2a_stopped, error = _stop_services(config, service_args, server_managed,
                                        web_managed, a2a_instances)
    if error is not None:
        return error
    error = _run_update_command(command)
    if error is not None:
        return error
    error = _restart_services(config, service_args, server_managed, web_managed,
                              a2a_stopped)
    if error is not None:
        return error
    print("Update applied.")
    return EXIT_OK


def _print_update_plan(command: str | None) -> None:
    """The 8-step update plan (SPEC_CLI §4.16)."""
    plan = ["1. automatic backup (synapse backup create)",
            "2. clean web stop (systemd or CLI)",
            "3. A2A bridge stop (if active)",
            "4. clean server stop (systemd or CLI)",
            f"5. update command: {command or '(not configured)'}",
            "6. server restart (systemd or CLI)",
            "7. web restart (systemd or CLI)",
            "8. A2A bridge restart (if active)"]
    print("Update plan:")
    for step in plan:
        print(f"  {step}")


def _apply_web_port(config) -> int:  # noqa: ANN001
    """Port for the restarted web: the current web's (pid file), else
    the resolved port (--port > $SYNAPSE_WEB_PORT > 8080) — tests
    isolate their port via the environment (SPEC_PRODUCTION §10.5)."""
    from .common import read_pid_file

    web_port = (read_pid_file(config, "web") or {}).get("port")
    if web_port is None:
        web_port = _env_port("SYNAPSE_WEB_PORT", 8080)
    return web_port


def _stop_services(config, service_args: argparse.Namespace,
                   server_managed: bool, web_managed: bool,
                   a2a_instances: list[str]) -> tuple[list[tuple[str, str, int]], int | None]:
    """Stops web, A2A bridge and server before the update. Under
    systemd, systemctl is used (a CLI stop would be countered by
    Restart=on-failure); otherwise the CLI stops. Returns the list of
    stopped A2A bridges (unit|agent, agent, port) for the restart phase
    and None — or ([], error) when a hard stop failure aborts the
    update (emit_error already printed)."""
    from . import server as server_group
    from . import web as web_group
    from .common import read_pid_file

    if web_managed:
        if not _systemctl_stop("synapse-web.service"):
            return [], emit_error("web stop failed (systemd) — update canceled")
    else:
        code = web_group._cmd_stop(service_args)
        if code not in (EXIT_OK,):
            print(f"  (web : {code})")

    a2a_stopped: list[tuple[str, str, int]] = []  # (unit|agent, agent, port)
    if server_managed and a2a_instances:
        for unit in a2a_instances:
            if not _systemctl_stop(unit):
                print(f"  (A2A bridge {unit}: stop failed, ignored)")
            else:
                a2a_stopped.append((unit, unit.split("@", 1)[1].rsplit(".", 1)[0], 8090))
    else:
        a2a_info = read_pid_file(config, "a2a") or {}
        if a2a_info.get("pid"):
            from . import a2a as a2a_group

            code = a2a_group._cmd_stop(service_args)
            if code in (EXIT_OK,):
                a2a_stopped.append(("cli", a2a_info.get("agent_name") or "",
                                    a2a_info.get("port") or 8090))

    if server_managed:
        if not _systemctl_stop("synapse.service"):
            return [], emit_error("server stop failed (systemd) — update canceled")
    else:
        code = server_group._cmd_stop(service_args)
        if code != EXIT_OK:
            return [], emit_error("server stop failed — update canceled")
    return a2a_stopped, None


def _run_update_command(command: str) -> int | None:
    """Runs the configured update command; None on success, else the
    error code (emit_error already printed)."""
    try:
        result = subprocess.run(command, shell=True, check=False)
        if result.returncode != 0:
            return emit_error(
                f"the update command failed (code {result.returncode}) — "
                "the previous state can be restored via 'synapse backup restore'"
            )
    except OSError as exc:
        return emit_error(f"cannot run the update command: {exc}")
    return None


def _restart_services(config, service_args: argparse.Namespace,
                      server_managed: bool, web_managed: bool,
                      a2a_stopped: list[tuple[str, str, int]]) -> int | None:
    """Restarts server, web and the stopped A2A bridges; None on
    success, else the error code (emit_error already printed)."""
    from . import server as server_group
    from . import web as web_group

    if server_managed:
        if not _systemctl_start("synapse.service"):
            return emit_error("server restart failed (systemd) — "
                              "run 'systemctl start synapse.service'")
    else:
        server_group._cmd_start(service_args)
    if web_managed:
        if not _systemctl_start("synapse-web.service"):
            print("  (web: systemd restart failed — see systemctl status)")
    else:
        web_group._cmd_start(service_args)
    for unit, agent, port in a2a_stopped:
        if unit != "cli":
            if not _systemctl_start(unit):
                print(f"  (A2A bridge {unit}: systemd restart failed)")
        elif agent:
            if not _a2a_cli_restart(config, agent, port):
                print(f"  (A2A bridge: secrets missing — restart it manually: "
                      f"synapse a2a start --agent-name {agent} --port {port})")
    return None


def _service_args(args: argparse.Namespace, web_port: int) -> argparse.Namespace:
    """Namespace for the server/web/backup handlers called by
    ``apply``: they read attributes that the ``apply`` subcommand
    does not declare (``force``, ``foreground``, ``port``, ``out``, ``dir``…)."""
    return argparse.Namespace(
        config=getattr(args, "config", None),
        config_root=getattr(args, "config_root", None),
        json=getattr(args, "json", False),
        force=False,
        foreground=False,
        log_level=None,
        port=web_port,
        out=None,
        dir=None,
        archive=None,
        my_name=None,
        organization_name=None,
        password_stdin=False,
    )
