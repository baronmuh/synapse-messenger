"""``synapse uninstall`` — complete uninstallation of Synapse.

Mirror of ``install.sh``: stops and removes the systemd units and
timers, removes the service account, the configuration, the data, the
run, the log and the backup directories (paths from the configuration
or the platform defaults), then uninstalls the Python package and the
``synapse`` command.

Safety:
- ``--dry-run`` lists exactly what would be removed, removes nothing.
- ``--keep-data`` uninstalls everything except data and backups.
- ``--yes`` confirms without any interactive prompt.
- While the server is running, the uninstall refuses (the operator
  stops it first) unless ``--yes`` is given, in which case the
  services are stopped cleanly first.
- On Linux, the systemd/account/directory parts require root: if the
  user is not root, the exact command to run is printed (or the
  process re-executes itself with sudo when available and safe).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import Config
from ..platform import default_paths
from .common import (
    EXIT_OK,
    emit_error,
    resolve_config,
)

GROUP = "uninstall"

_SYSTEMD_UNITS = (
    "synapse.service",
    "synapse-web.service",
    "synapse-a2a@.service",
    "synapse-backup.service",
    "synapse-backup.timer",
    "synapse-backup-verify.service",
    "synapse-backup-verify.timer",
    "synapse-monitor.service",
    "synapse-monitor.timer",
    "synapse-ci.service",
    "synapse-ci.timer",
)

_SYSTEMD_DIR = "/etc/systemd/system"


def add_parser(sub: argparse._SubParsersAction,
               common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="completely uninstalls Synapse (services, files, package)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dry-run", action="store_true",
                   help="shows exactly what would be removed, removes nothing")
    p.add_argument("--keep-data", action="store_true",
                   help="uninstalls everything except data and backups")
    p.add_argument("--yes", action="store_true",
                   help="confirms without the interactive prompt (stops the "
                        "running services first)")
    p.set_defaults(run=_cmd_uninstall)


_EXAMPLES = """\
Examples:
  synapse uninstall --dry-run     show the uninstall plan (no changes)
  synapse uninstall --yes         uninstall everything (no prompt)
  synapse uninstall --keep-data   uninstall but preserve data and backups
"""


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class _Plan:
    """The list of removals, with their kind (for --keep-data filtering)."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []  # (kind, description)

    def add(self, kind: str, description: str) -> None:
        self.items.append((kind, description))

    def keep(self, *descriptions: str) -> None:
        """Removes the given descriptions from the plan (--keep-data)."""
        self.items = [(k, d) for k, d in self.items
                      if d not in descriptions]

    def systemd(self) -> list[str]:
        return [d for k, d in self.items if k == "systemd"]

    def dirs(self) -> list[str]:
        return [d for k, d in self.items if k == "dir"]

    def files(self) -> list[str]:
        return [d for k, d in self.items if k == "file"]

    def account(self) -> list[str]:
        return [d for k, d in self.items if k == "account"]

    def package(self) -> list[str]:
        return [d for k, d in self.items if k == "package"]


def _build_plan(config: Config, keep_data: bool,
                config_path: str | None = None) -> _Plan:
    """The uninstall plan (systemd units, account, directories, package).

    Directories are read from the effective configuration (falling back
    to the platform defaults) so that a custom layout is respected.
    """
    plan = _Plan()

    # systemd units (Linux production install) — always listed; the
    # removal itself is skipped when the unit file does not exist.
    for unit in _SYSTEMD_UNITS:
        plan.add("systemd", unit)

    # service account (Linux production install)
    plan.add("account", "synapse")

    # directories: data/run/log/backup/config/secrets + the install
    # root (/opt/synapse) on Linux.
    data_dir, backup_dir = _add_directories(plan, config, config_path)

    # package + CLI command
    plan.add("package", "synapse-messenger (pip) + the synapse command")

    if keep_data:
        # keep the data and the backups; the rest is still removed.
        plan.keep(data_dir, backup_dir)
    return plan


def _add_directories(plan: _Plan, config: Config,
                     config_path: str | None) -> tuple[str, str]:
    """Adds the data/run/log/backup/config/secrets + Linux install-root
    directories to the plan. Returns (data_dir, backup_dir) for the
    --keep-data filter."""
    defaults = default_paths()
    data_dir = config.storage_dir or defaults["storage"]
    run_dir = config.run_dir or defaults["run"]
    log_dir = config.log_dir or defaults["log"]
    backup_dir = config.backup_dir or defaults["backup"]
    etc_dir = str(Path(config_path).parent) if config_path else defaults["config"]
    secrets_dir = defaults["secrets"]
    plan.add("dir", data_dir)
    plan.add("dir", run_dir)
    plan.add("dir", log_dir)
    plan.add("dir", backup_dir)
    plan.add("dir", etc_dir)
    plan.add("dir", secrets_dir)
    if sys.platform.startswith("linux"):
        plan.add("dir", "/opt/synapse")
    return data_dir, backup_dir


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:  # pragma: no cover - non-POSIX
        return False


def _services_running(config: Config) -> list[str]:
    """Names of the running services (server/web/a2a) for this config."""
    from .common import read_pid_file

    running = []
    for name, pid_name in (("server", "synapse"), ("web", "web"),
                           ("a2a", "a2a")):
        info = read_pid_file(config, pid_name)
        if info and info.get("pid"):
            running.append(name)
    return running


def _systemctl(unit: str, action: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", action, unit],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _stop_services_cli(config: Config, config_path: str | None = None) -> bool:
    """Stops the services via the CLI (non-systemd mode)."""
    from . import server as server_group
    from . import web as web_group

    args = argparse.Namespace(
        config=config_path, config_root=None,
        json=False, force=False, foreground=False, log_level=None,
        port=0, out=None, dir=None, archive=None, my_name=None,
        organization_name=None, password_stdin=False)
    ok = True
    # web stop is best-effort (a web may not be running).
    code = web_group._cmd_stop(args)
    if code not in (EXIT_OK,):
        ok = False
    code = server_group._cmd_stop(args)
    if code not in (EXIT_OK,):
        ok = False
    return ok


def _cmd_uninstall(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    config_path = (getattr(args, "config", None)
                   or getattr(args, "config_root", None)
                   or os.environ.get("SYNAPSE_CONFIG")
                   or os.environ.get("Synapse_CONFIG"))
    plan = _build_plan(config, args.keep_data, config_path)
    _print_plan(plan)

    dry_run_result = _dry_run(config, args.dry_run)
    if dry_run_result is not None:
        return dry_run_result

    error = _handle_running_services(config, config_path, args.yes)
    if error is not None:
        return error

    error = _confirm_uninstall(args.yes)
    if error is not None:
        return error

    error = _ensure_root()
    if error is not None:
        return error

    error = _run_removal_phases(plan)
    if error is not None:
        return error

    print()
    print("Synapse uninstalled.")
    return EXIT_OK


def _print_plan(plan: _Plan) -> None:
    """Prints the uninstall plan (systemd units, account, dirs, package)."""
    print("Synapse uninstall plan:")
    for kind, desc in plan.items:
        print(f"  - [{kind}] {desc}")
    print()


def _dry_run(config: Config, dry_run: bool) -> int | None:
    """--dry-run lists everything and removes NOTHING, even when the
    services are running (no stop, no refusal). Returns the exit code
    when the command was a dry-run, else None."""
    if not dry_run:
        return None
    running = _services_running(config)
    if running:
        print(f"Services currently running: {', '.join(running)}.")
    print("(--dry-run: nothing removed)")
    return EXIT_OK


def _handle_running_services(config: Config, config_path: str | None,
                             yes: bool) -> int | None:
    """Refuses (or cleanly stops, with --yes) the running services."""
    running = _services_running(config)
    if not running:
        return None
    print(f"Services currently running: {', '.join(running)}.")
    if not yes:
        return emit_error(
            "stop the services first (synapse server stop, synapse web "
            "stop, synapse a2a stop --agent-name <name>), or re-run with "
            "--yes to stop them cleanly during the uninstall"
        )
    print("Stopping the services...")
    if not _stop_services_cli(config, config_path):
        return emit_error("failed to stop the services — uninstall canceled")
    return None


def _confirm_uninstall(yes: bool) -> int | None:
    """Interactive confirmation ('uninstall') unless --yes was given."""
    if yes:
        return None
    print("This will permanently remove Synapse from this machine.")
    try:
        answer = input("Type 'uninstall' to confirm: ").strip().lower()
    except EOFError:
        return emit_error("no confirmation given — aborted")
    if answer != "uninstall":
        return emit_error("confirmation failed — aborted")
    return None


def _ensure_root() -> int | None:
    """The systemd/account/root-owned directories need root: re-run with
    sudo when available, else print the exact command to run as root."""
    if _is_root():
        return None
    cmd = " ".join(sys.argv)
    if shutil.which("sudo"):
        print(f"Root privileges required — re-running with sudo: sudo {cmd}")
        try:
            result = subprocess.run(["sudo", *sys.argv])
            return result.returncode
        except OSError as exc:
            return emit_error(f"cannot re-run with sudo: {exc}")
    return emit_error(
        "root privileges required. Run the same command as root:"
        f"\n  sudo {cmd}"
    )


def _remove_systemd_units(plan: _Plan) -> int | None:
    """1. stop + disable + remove the systemd units."""
    for unit in plan.systemd():
        unit_path = Path(_SYSTEMD_DIR) / unit
        if not unit_path.exists():
            continue
        _systemctl(unit, "stop")
        _systemctl(unit, "disable")
        try:
            unit_path.unlink()
            print(f"  removed {unit_path}")
        except OSError as exc:
            return emit_error(f"cannot remove {unit_path}: {exc}")
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=30)
    subprocess.run(["systemctl", "reset-failed"], capture_output=True, timeout=30)
    return None


def _remove_service_account(plan: _Plan) -> int | None:
    """2. remove the service account."""
    for account in plan.account():
        try:
            subprocess.run(["userdel", "-r", account],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
            print(f"  removed system account {account}")
        except OSError:
            pass
    return None


def _remove_directories(plan: _Plan) -> int | None:
    """3. remove the directories."""
    for directory in plan.dirs():
        path = Path(directory)
        if not path.exists():
            continue
        try:
            shutil.rmtree(path)
            print(f"  removed {path}")
        except OSError as exc:
            return emit_error(f"cannot remove {path}: {exc}")
    return None


def _uninstall_package(plan: _Plan) -> int | None:
    """4. uninstall the Python package (pip uninstall) — the synapse
    command disappears with it."""
    for _ in plan.package():
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "uninstall", "-y",
                 "synapse-messenger"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=120)
            print("  uninstalled the synapse-messenger package")
        except OSError as exc:
            return emit_error(f"cannot uninstall the package: {exc}")
    return None


_REMOVAL_PHASES = (
    _remove_systemd_units,
    _remove_service_account,
    _remove_directories,
    _uninstall_package,
)


def _run_removal_phases(plan: _Plan) -> int | None:
    """Runs the four removal phases in order, stopping at the first
    error. Each phase returns None on success or an error code (its
    message was already printed)."""
    for phase in _REMOVAL_PHASES:
        error = phase(plan)
        if error is not None:
            return error
    return None
