"""``update`` group (SPEC_CLI §4.16): checking and applying updates.

``apply`` runs the plan: automatic backup → clean stop of the
server (and web) → update command → restart. The update
command is configured via ``update_command`` (config) or the
variable d'environnement ``SYNAPSE_UPDATE_COMMAND`` ; sans elle, la
update is explicitly refused (no simulated behavior).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .common import (
    EXIT_OK,
    emit,
    emit_error,
    project_version,
    resolve_config,
)

GROUP = "update"

_EXAMPLES = """\
Exemples :
  synapse update check                 installed version vs remote channel
  synapse update apply --dry-run       plan without executing anything
  synapse update apply                 backup → stop → update → restart
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="updates (check, apply)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

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
# Commandes
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

    ``SYNAPSE_NO_SYSTEMD=1`` force le mode CLI (utile dans les tests et
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


def _systemd_active(unit: str) -> bool:
    """True if the systemd unit is active (``systemctl is-active``)."""
    if os.environ.get("SYNAPSE_NO_SYSTEMD") == "1":
        return False
    try:
        result = subprocess.run(["systemctl", "-q", "is-active", unit],
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
    (mode hors systemd). Retourne False si les secrets sont absents — dans ce
    case, the operator must restart the bridge manually."""
    secrets_dir = os.environ.get("SYNAPSE_SECRETS_DIR") or _default_paths()["secrets"]
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
    """Port depuis l'environnement (``SYNAPSE_WEB_PORT``/``SYNAPSE_A2A_PORT``)."""
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _cmd_check(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    local = project_version()
    url = _update_url(config)
    payload = {"installed": local, "remote": None, "up_to_date": True,
               "channel": "no remote channel configured (SYNAPSE_UPDATE_URL)"}
    if url:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                remote = resp.read().decode("utf-8")
            import json as json_mod

            data = json_mod.loads(remote)
            payload["remote"] = data.get("version")
            payload["channel"] = url
            payload["up_to_date"] = data.get("version") == local
        except (OSError, urllib.error.URLError, TimeoutError,
                ValueError) as exc:
            payload["remote_error"] = str(exc)
            payload["up_to_date"] = None
    if getattr(args, "json", False):
        return emit(args, payload)
    print(f"Installed version: {local}")
    if payload.get("remote"):
        state = "up to date" if payload["up_to_date"] else "UPDATE AVAILABLE"
        print(f"Canal distant ({url}) : {payload['remote']} — {state}")
    elif payload.get("remote_error"):
        print(f"Canal distant injoignable : {payload['remote_error']}")
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
    from . import server as server_group
    from . import web as web_group
    from .common import read_pid_file

    # Restarted web port: the current web's (pid file), else the
    # resolved port (--port > $SYNAPSE_WEB_PORT > 8080) — tests isolate
    # leur port par environnement (SPEC_PRODUCTION §10.5).
    web_port = (read_pid_file(config, "web") or {}).get("port")
    if web_port is None:
        web_port = _env_port("SYNAPSE_WEB_PORT", 8080)
    service_args = _service_args(args, web_port)

    if not args.no_backup:
        code = backup_group._cmd_create(service_args)
        if code != EXIT_OK:
            return emit_error("backup failed — update canceled")

    # Stops: web, A2A bridge, server. Under systemd, systemctl (a
    # CLI stop would be countered by Restart=on-failure); otherwise CLI.
    if web_managed:
        if not _systemctl_stop(web_unit):
            return emit_error("web stop failed (systemd) — update canceled")
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
        if not _systemctl_stop(server_unit):
            return emit_error("server stop failed (systemd) — update canceled")
    else:
        code = server_group._cmd_stop(service_args)
        if code != EXIT_OK:
            return emit_error("server stop failed — update canceled")

    try:
        result = subprocess.run(command, shell=True, check=False)
        if result.returncode != 0:
            return emit_error(
                f"the update command failed (code {result.returncode}) — "
                "the previous state can be restored via 'synapse backup restore'"
            )
    except OSError as exc:
        return emit_error(f"cannot run the update command: {exc}")

    # Restarts: server, web, A2A bridge.
    if server_managed:
        if not _systemctl_start(server_unit):
            return emit_error("server restart failed (systemd) — "
                              "lancez 'systemctl start synapse.service'")
    else:
        server_group._cmd_start(service_args)
    if web_managed:
        if not _systemctl_start(web_unit):
            print("  (web: systemd restart failed — see systemctl status)")
    else:
        web_group._cmd_start(service_args)
    for unit, agent, port in a2a_stopped:
        if unit != "cli":
            if not _systemctl_start(unit):
                print(f"  (A2A bridge {unit}: systemd restart failed)")
        elif agent:
            if not _a2a_cli_restart(config, agent, port):
                print(f"  (passerelle A2A : secrets absents — relancez manuellement : "
                      f"synapse a2a start --agent-name {agent} --port {port})")
    print("Update applied.")
    return EXIT_OK


def _service_args(args: argparse.Namespace, web_port: int) -> argparse.Namespace:
    """Namespace for the server/web/backup handlers called by
    ``apply`` : ils lisent des attributs que la sous-commande ``apply`` ne
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
