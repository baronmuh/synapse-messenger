"""Shared functions of the unified ``synapse`` CLI (SPEC_CLI.md).

This module centralizes:

* configuration resolution (``--config`` > ``$SYNAPSE_CONFIG`` >
  ``$Synapse_CONFIG`` > default path);
* the runtime directory (derived from the socket path) and the
  local trust token (``web_token``, 0600);
* the three-mode authentication (SPEC_CLI §2.1): local token (no
  password), human account (``--organization-name``), agent account
  (``--my-name``);
* the PID files (``synapse.pid``, ``web.pid``, ``a2a.pid``) with the
  double check live PID + socket/HTTP (SPEC_CLI §2.2/§7.4);
* exit codes (0 success, 1 error, 3 service unavailable,
  4 already running);
* machine JSON output and human display (aligned columns).

Passwords are never accepted as command arguments: they
go through ``getpass`` or ``--password-stdin`` (transversal rule 3).
"""

from __future__ import annotations

import argparse
import getpass
import importlib.metadata
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..validation import human_username_for

PROG = "synapse"

# Exit codes (SPEC_CLI §2): 0 success; 1 error; 3 service
# unavailable (socket missing, server stopped); 4 already
# running (starting an already active service).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNAVAILABLE = 3
EXIT_RUNNING = 4

# Project version for PID files and ``update check``. The source
# of truth is the installed package; in development (not installed), we
# fall back to the version declared in pyproject.toml.
_FALLBACK_VERSION = "3.1.3"


class CliError(Exception):
    """Error of the CLI itself (arguments, refusals, local state).

    ``code`` is the exit code of the process (SPEC_CLI §2).
    """

    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class Parser(argparse.ArgumentParser):
    """ArgumentParser with the specification's exit code (1).

    argparse exits with code 2 by default on argument errors;
    the specification fixes ``1`` for any argument error.
    """

    def error(self, message: str) -> None:  # noqa: D102
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def project_version() -> str:
    """Installed package version (falls back to pyproject in development)."""
    try:
        return importlib.metadata.version("synapse-messenger")
    except importlib.metadata.PackageNotFoundError:
        return _FALLBACK_VERSION


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def resolve_config(args: argparse.Namespace | None = None) -> Config:
    """Loads the effective configuration (SPEC_CLI §2).

    Search order: ``--config`` (root or subcommand), then
    ``$SYNAPSE_CONFIG`` (specification), then ``$Synapse_CONFIG``
    (legacy service variable), then the default path.
    """
    path = None
    if args is not None:
        path = getattr(args, "config", None) or getattr(args, "config_root", None)
    if path is None:
        path = os.environ.get("SYNAPSE_CONFIG") or os.environ.get("Synapse_CONFIG")
    try:
        return Config.load(path)
    except ValueError as exc:
        raise CliError(str(exc)) from exc


def config_arg_path(args: argparse.Namespace | None = None) -> str:
    """Absolute config path for daemons (SPEC_CLI §2 resolution order:
    ``--config``/``--config-root`` > ``$SYNAPSE_CONFIG`` >
    ``$Synapse_CONFIG``); empty string when nothing was provided."""
    if args is not None:
        path = getattr(args, "config", None) or getattr(args, "config_root", None)
        if path:
            return os.path.abspath(path)
    path = os.environ.get("SYNAPSE_CONFIG") or os.environ.get("Synapse_CONFIG")
    return os.path.abspath(path) if path else ""


def run_dir(config: Config) -> str:
    """Runtime directory: socket parent (unix) or platform default (tcp)."""
    from ..transport import run_dir as _run_dir

    return _run_dir(config)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def normalize_datetime(value: str | None) -> str | None:
    """Normalizes a timestamp into the exact API format
    (YYYY-MM-DDTHH:MM:SS.sssZ). The API requires milliseconds; the
    SPEC_CLI documentation uses ``YYYY-MM-DDTHH:MM:SSZ`` — the
    missing milliseconds are added (no other transformation)."""
    if value is None:
        return None
    import re

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        return value[:-1] + ".000Z"
    return value


# ---------------------------------------------------------------------------
# Local trust token (SPEC_CLI §2.1, mode 1)
# ---------------------------------------------------------------------------


def read_web_token(config: Config) -> str | None:
    """Reads the local trust token from the run dir (0600), if it exists."""
    path = os.path.join(run_dir(config), "web_token")
    try:
        with open(path, encoding="ascii") as fh:
            token = fh.read().strip()
        return token or None
    except OSError:
        return None


def socket_responds(config: Config) -> bool:
    """True if the local transport endpoint accepts a connection."""
    from ..transport import transport_responds

    return transport_responds(config)


# ---------------------------------------------------------------------------
# Passwords (transversal rule 3: never as command arguments)
# ---------------------------------------------------------------------------


def getpass_get(prompt: str) -> str:
    """Interactive entry of a secret (single point of ``getpass`` — the
    tests replace this module to simulate the secrets)."""
    return getpass.getpass(prompt)


def read_password(args: argparse.Namespace, prompt: str) -> str:
    """Reads a password: a line of stdin (``--password-stdin``) or getpass.

    With ``--password-stdin``, each call consumes a line of stdin
    (commands with several secrets read several lines, in
    the order documented by their help).
    """
    if getattr(args, "password_stdin", False):
        value = sys.stdin.readline().rstrip("\n")
        if not value:
            raise CliError("synapse: empty password on stdin")
        return value
    return getpass_get(prompt)


# ---------------------------------------------------------------------------
# Authentication (SPEC_CLI §2.1) — three modes by priority order
# ---------------------------------------------------------------------------


def require_service(config: Config) -> None:
    """Any command served by the API requires a reachable service: code 3
    (service unavailable, SPEC_CLI §2) if the socket does not respond."""
    if not socket_responds(config):
        raise CliError(
            f"service unavailable: the socket {config.socket_path} does not respond "
            "(server stopped?)",
            code=EXIT_UNAVAILABLE,
        )


def unique_org_name(config: Config) -> str:
    """Name of the unique active organization (local token context).

    Raises an explicit error if no token is available, if no
    organization is active, or if several active organizations
    require ``--organization-name``.
    """
    from ..client import ApiClientError, Client, ClientTransportError
    from ..service import _WEB_LOCAL

    token = read_web_token(config)
    if token is None:
        raise CliError(
            "local web token missing: specify --organization-name "
            "(or --my-name for an account identity)"
        )
    try:
        data = Client.from_config(config).list_orgs(_WEB_LOCAL, token)
    except ClientTransportError as exc:
        raise CliError(
            f"service unavailable: {exc}", code=EXIT_UNAVAILABLE
        ) from exc
    except ApiClientError as exc:
        raise CliError(f"cannot list the organizations: {exc.message}") from exc
    orgs = [o["organization_name"] for o in data.get("organizations", [])]
    if len(orgs) == 1:
        return orgs[0]
    if not orgs:
        raise CliError("no active organization: specify --organization-name")
    raise CliError(
        "multiple active organizations ({}): specify --organization-name".format(
            ", ".join(orgs)
        )
    )


def resolve_org_auth(config: Config, args: argparse.Namespace,
                     org_name: str | None = None) -> tuple[str, str]:
    """(organization_name_auth, organization_password_auth).

    The local token serves as the organization password when it is
    present (rule 7: a command served by the token requires no
    password); otherwise the password is prompted on stdin/getpass.
    """
    require_service(config)
    org = org_name or getattr(args, "organization_name", None) or unique_org_name(config)
    token = read_web_token(config)
    if token is not None:
        return org, token
    return org, read_password(args, f"Password of the organization '{org}' : ")


def resolve_human_auth(config: Config, args: argparse.Namespace,
                       org_name: str | None = None) -> tuple[str, str]:
    """(my_name_auth, my_password_auth) for a human account.

    The human identity is the ``<org>_humain`` account; the local token
    replaces its password (SPEC-WEB R6.7), otherwise the password of
    the organization is prompted.
    """
    require_service(config)
    org = org_name or getattr(args, "organization_name", None) or unique_org_name(config)
    token = read_web_token(config)
    if token is not None:
        return human_username_for(org), token
    return human_username_for(org), read_password(
        args, f"Password of the organization '{org}' : "
    )


def resolve_agent_auth(config: Config, args: argparse.Namespace,
                       my_name: str | None = None) -> tuple[str, str]:
    """(my_name_auth, my_password_auth) for an agent account."""
    my = my_name or getattr(args, "my_name", None)
    if not my:
        raise CliError("account identity required: --my-name <account>")
    return my, read_password(args, f"Password of agent '{my}' : ")


def resolve_identity(config: Config, args: argparse.Namespace,
                     my_name: str | None = None) -> tuple[str, str]:
    """Identity of an "as account" command (messages, tasks…).

    Priority: ``--my-name`` (agent account); otherwise the human account of
    the organization (local token or password) — the human accounts
    can call the account commands (dispatch _AGENT_HANDLERS).
    """
    if getattr(args, "my_name", None) or my_name:
        return resolve_agent_auth(config, args, my_name)
    return resolve_human_auth(config, args)


# ---------------------------------------------------------------------------
# Fichiers PID (SPEC_CLI §2.2 / §7.4)
# ---------------------------------------------------------------------------
# JSON content: PID + timestamp + version (+ fields specific to each
# service, e.g. web port). Written 0600, removed on clean stop.


def pid_file_path(config: Config, name: str) -> str:
    return os.path.join(run_dir(config), f"{name}.pid")


def write_pid_file(config: Config, name: str, extra: dict | None = None) -> None:
    data = {
        "pid": os.getpid(),
        "started_at": now_iso(),
        "version": project_version(),
    }
    if extra:
        data.update(extra)
    path = pid_file_path(config, name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def read_pid_file(config: Config, name: str) -> dict | None:
    path = pid_file_path(config, name)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def remove_pid_file(config: Config, name: str) -> None:
    try:
        os.unlink(pid_file_path(config, name))
    except FileNotFoundError:
        pass


def pid_alive(pid: int | None) -> bool:
    """True if the process exists (portable: handle probe on Windows)."""
    from ..platform import process_alive

    return process_alive(pid)


def send_sigterm(pid: int) -> bool:
    """Graceful stop request (SIGTERM, or CTRL_BREAK_EVENT on Windows)."""
    from ..platform import send_stop_signal

    return send_stop_signal(pid)


def send_sigkill(pid: int) -> bool:
    """Unconditional termination (SIGKILL, or TerminateProcess on Windows)."""
    from ..platform import send_kill_signal

    return send_kill_signal(pid)


def wait_process_exit(pid: int, timeout: float = 15.0) -> bool:
    """Waits for the process to finish; True if it ended within the delay."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


# ---------------------------------------------------------------------------
# Service state (double check PID + socket/HTTP)
# ---------------------------------------------------------------------------


def service_state(config: Config, name: str = "synapse") -> dict:
    """Service state: ``running`` / ``degraded`` / ``stopped``.

    A live PID without a responding socket = "degraded" state (SPEC_CLI
    §2.2); a responding socket without a PID file = running
    (unknown PID, service started outside the CLI).
    """
    info = read_pid_file(config, name)
    alive = pid_alive(info["pid"]) if info else False
    ok = socket_responds(config)
    if info is None:
        return {"state": "running" if ok else "stopped", "pid": None,
                "degraded": False, "pid_file": None, "socket_ok": ok}
    if alive:
        return {"state": "running" if ok else "degraded", "pid": info["pid"],
                "degraded": not ok, "pid_file": info, "socket_ok": ok}
    return {"state": "stopped", "pid": info["pid"], "degraded": False,
            "pid_file": info, "socket_ok": ok}


def stop_service(config: Config, name: str, *, force: bool = False) -> tuple[int, str]:
    """Clean service stop: SIGTERM, wait ≤ 15s, SIGKILL if --force.

    Returns ``(exit_code, message)``. The PID file is removed once
    the process has ended.
    """
    state = service_state(config, name)
    if state["state"] == "stopped":
        return EXIT_OK, f"the service '{name}' is already stopped"
    info = state["pid_file"] or {}
    pid = state["pid"] or info.get("pid")
    if pid is None or not pid_alive(pid):
        return EXIT_OK, f"the service '{name}' is already stopped"
    if not send_sigterm(pid):
        remove_pid_file(config, name)
        return EXIT_OK, f"the service '{name}' is already stopped"
    if wait_process_exit(pid, timeout=15.0):
        remove_pid_file(config, name)
        return EXIT_OK, f"the service '{name}' is stopped (PID {pid})"
    if not force:
        return EXIT_ERROR, (
            f"the service '{name}' (PID {pid}) does not respond to SIGTERM within "
            "within 15 s — rerun with --force for SIGKILL"
        )
    send_sigkill(pid)
    if wait_process_exit(pid, timeout=5.0):
        remove_pid_file(config, name)
        return EXIT_OK, f"the service '{name}' was stopped by SIGKILL (PID {pid})"
    return EXIT_ERROR, f"cannot stop the service '{name}' (PID {pid})"


def wait_ready(predicate, timeout: float = 15.0, interval: float = 0.1) -> bool:
    """Waits until a predicate becomes true (bounded delay)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def emit(args: argparse.Namespace, data: dict | None,
         human: str | None = None) -> int:
    """Prints the result: machine JSON envelope (--json) or human text.

    The ``--json`` mode forces raw JSON output (full envelope
    success/data/error, identical to the API — scripting, SPEC_CLI §2).
    """
    if getattr(args, "json", False):
        print(json.dumps({"success": True, "data": data, "error": None},
                         ensure_ascii=False))
    elif human:
        print(human)
    else:
        print(json.dumps({"success": True, "data": data, "error": None},
                         ensure_ascii=False))
    return EXIT_OK


def emit_error(message: str, *, code: int = EXIT_ERROR,
               api_code: str | None = None) -> int:
    """Prints an error (JSON envelope on stdout + message on stderr)."""
    print(json.dumps({"success": False, "data": None,
                      "error": {"code": api_code or "CLI_ERROR", "message": message}},
                     ensure_ascii=False))
    print(f"{PROG}: {message}", file=sys.stderr)
    return code


def api_error(exc: Exception) -> int:
    """Maps an API/transport error to the CLI exit contract: transport
    failures (service down) exit 3, API refusals exit 1 with the API
    error code in the envelope. Shared by every command group."""
    from ..client import ClientTransportError

    if isinstance(exc, ClientTransportError):
        return emit_error(f"service unavailable: {exc}", code=EXIT_UNAVAILABLE)
    return emit_error(exc.message, api_code=exc.code)  # type: ignore[attr-defined]


def table(rows: list[list[str]], headers: list[str] | None = None) -> str:
    """Table with aligned columns (human output by default)."""
    if not rows:
        return "(no results)"
    if headers:
        rows = [headers] + rows
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for r in rows:
        lines.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))
    if headers:
        lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def colorize(text: str, color: str, enabled: bool = True) -> str:
    """Simple coloring (auto-disabled outside a tty)."""
    if not enabled or not sys.stdout.isatty():
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "cyan": "36", "bold": "1"}
    code = codes.get(color)
    if not code:
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def http_get(port: int, path: str, timeout: float = 2.0) -> tuple[int, dict | None]:
    """Local HTTP request (127.0.0.1); returns (code, JSON or None)."""
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return resp.status, None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (OSError, urllib.error.URLError, TimeoutError):
        return -1, None
