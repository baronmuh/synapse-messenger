"""``api`` group (SPEC_CLI §4.11): raw access to ANY service command.

Evolution door: a new API command is usable immediately
without CLI changes. Parameters follow the existing validation
(``synapse.validation.COMMAND_SPECS``); identity (local token, human
or agent account) is resolved like for structured commands.
"""

from __future__ import annotations

import argparse

from ..client import ApiClientError, Client, ClientTransportError
from ..validation import COMMAND_SPECS
from .common import (
    EXIT_OK,
    emit_error,
    read_password,
    read_web_token,
    resolve_config,
)

GROUP = "api"

_EXAMPLES = """\
Examples:
  synapse api get_org_metrics --organization-name acme --password-stdin
  synapse api send_message --recipient bob --message "Bonjour" \\
      --client-message-id m1 --my-name alice --password-stdin
  synapse api help --my-name alice --password-stdin

Recognized options: --config, --json, --password-stdin, --my-name,
--organization-name. Any other ``--key value`` (or ``--key=value``) is
passed as a command parameter (dashes converted to
underscores); a valueless ``--flag`` means true. The
authentication parameters (my_name_auth / my_password_auth /
organization_name_auth / organization_password_auth) are inferred from
the options above.
"""

_AUTH_OPTIONS = {
    "--config", "--json", "--password-stdin", "--my-name",
    "--organization-name", "--help", "-h",
}


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="raw access to any service command (evolution)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # NB: the positional is called « api_command » — its name IS the dest
    # (argparse forbids an explicit dest= on a named positional); the
    # root group's "command" dest is therefore never overwritten.
    p.add_argument("api_command",
                   help="service command name (e.g. get_org_metrics)")
    p.add_argument("params", nargs="*", help="command options (--key value)")
    p.add_argument("--json", action="store_true", help="raw JSON output (default)")
    p.add_argument("--my-name", default=None, help="account identity")
    p.add_argument("--organization-name", default=None,
                   help="organization (org commands or human account)")
    p.add_argument("--password-stdin", action="store_true",
                   help="read the password from stdin")
    p.set_defaults(run=_cmd_api)


def _cmd_api(args: argparse.Namespace) -> int:
    """argparse entry point (guard) — the real path goes through
    ``run_raw`` (called by main(), which does NOT pass the
    arbitrary arguments through argparse)."""
    from .common import resolve_config

    config = resolve_config(args)
    tokens = list(getattr(args, "api_params", None) or args.params)
    return run_api(
        config,
        command=args.api_command,
        raw_tokens=tokens,
        json_out=getattr(args, "json", False),
        my_name=getattr(args, "my_name", None),
        organization_name=getattr(args, "organization_name", None),
        password_stdin=getattr(args, "password_stdin", False),
    )


def run_raw(tokens: list[str], prefix: list[str] | None = None) -> int:
    """Raw access without argparse: ``[command, options...]`` + root
    options (``--config``). The ``--key value`` tokens are
    matched here (argparse cannot do this for arbitrary options)."""
    from .common import resolve_config

    prefix = prefix or []
    config = None
    i = 0
    while i < len(prefix):
        if prefix[i] == "--config" and i + 1 < len(prefix):
            from ..config import Config

            try:
                config = Config.load(prefix[i + 1])
            except ValueError as exc:
                return emit_error(str(exc))
            i += 2
            continue
        i += 1
    if config is None:
        config = resolve_config()

    if not tokens or tokens[0].startswith("--"):
        print(_EXAMPLES)
        return EXIT_OK

    # Recognized CLI options (never forwarded to the service): --json,
    # --password-stdin, --config. --my-name / --organization-name are
    # kept in the tokens: they serve as authentication context
    # AND as a parameter when the command declares that name (create_org).
    command = tokens[0]
    rest = tokens[1:]
    json_out = False
    my_name = None
    organization_name = None
    password_stdin = False
    params_tokens: list[str] = []
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == "--json":
            json_out = True
        elif token == "--password-stdin":
            password_stdin = True
        elif token == "--my-name" and i + 1 < len(rest):
            my_name = rest[i + 1]
            i += 1
        elif token == "--organization-name" and i + 1 < len(rest):
            organization_name = rest[i + 1]
            i += 1
        elif token == "--config" and i + 1 < len(rest):
            from ..config import Config

            try:
                config = Config.load(rest[i + 1])
            except ValueError as exc:
                return emit_error(str(exc))
            i += 1
        else:
            params_tokens.append(token)
        i += 1

    return run_api(config, command=command, raw_tokens=params_tokens,
                   json_out=json_out, my_name=my_name,
                   organization_name=organization_name,
                   password_stdin=password_stdin)


def run_api(config, *, command: str, raw_tokens: list[str], json_out: bool,
            my_name: str | None, organization_name: str | None,
            password_stdin: bool) -> int:
    """Raw access body: command + parameters + authentication."""
    from .common import require_service

    require_service(config)
    spec = COMMAND_SPECS.get(command)

    # Command parameters: --key value / --key=value / --flag.
    params: dict = {}
    tokens = list(raw_tokens)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            return emit_error(
                f"unexpected parameter: {token!r} (use --key value)"
            )
        if "=" in token:
            key, _, value = token[2:].partition("=")
            params[key.replace("-", "_")] = _coerce(value)
            i += 1
            continue
        key = token[2:].replace("-", "_")
        if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
            params[key] = _coerce(tokens[i + 1])
            i += 2
        else:
            params[key] = True  # boolean flag
            i += 1

    # Known command: all declared parameters are sent (missing
    # ones are null) — the API requires the exact key set.
    if spec is not None:
        for name, _typ, _required, _validator in spec[1]:
            params.setdefault(name, None)
        # --organization-name / --my-name also act as parameters when
        # the command declares that name (e.g. create_org → organization_name).
        if organization_name is not None and "organization_name" in params:
            params["organization_name"] = organization_name
        if my_name is not None and "my_name" in params:
            params["my_name"] = my_name
        _fill_secret_params(params, spec, args=_SecretArgs(password_stdin))

    # Authentication: local token, human or agent (SPEC_CLI §2.1).
    token = read_web_token(config)
    if spec is not None and spec[2]:  # organization command
        org = organization_name or params.get("organization_name_auth")
        if token is not None:
            params["organization_name_auth"] = org or _unique_org(config, token)
            params["organization_password_auth"] = token
        else:
            if not org:
                return emit_error(
                    "organization command: --organization-name required "
                    "(or local web token present)"
                )
            params["organization_name_auth"] = org
            params["organization_password_auth"] = read_password(
                _SecretArgs(password_stdin),
                f"Password of the organization '{org}' : ",
            )
    else:
        if command == "create_org" and token is not None:
            # Web equivalent: local web identity + token (creation from
            # the login page — no organization required).
            from ..service import _WEB_LOCAL

            params["my_name_auth"] = _WEB_LOCAL
            params["my_password_auth"] = token
        elif my_name is None and token is not None:
            # Human account of the organization (the token replaces the
            # human password) — humans call the account commands.
            from ..validation import human_username_for

            human = human_username_for(
                organization_name or _unique_org(config, token)
            )
            params["my_name_auth"] = human
            params["my_password_auth"] = token
        elif my_name is not None:
            params["my_name_auth"] = my_name
            params["my_password_auth"] = read_password(
                _SecretArgs(password_stdin),
                f"Password of agent '{my_name}' : ",
            )
        else:
            return emit_error(
                "identity required: --my-name <account> (or --organization-name, "
                "or local web token present)"
            )

    try:
        data = Client.from_config(config).request(command, params)
    except ApiClientError as exc:
        return emit_error(exc.message, api_code=exc.code)
    except ClientTransportError as exc:
        return emit_error(f"service unavailable: {exc}", code=3)
    # Raw access prints the JSON response (full envelope, scripting).
    import json as json_mod

    print(json_mod.dumps({"success": True, "data": data, "error": None},
                         ensure_ascii=False))
    return EXIT_OK


class _SecretArgs:
    """Minimal namespace: only ``password_stdin`` is read by read_password."""

    def __init__(self, password_stdin: bool) -> None:
        self.password_stdin = password_stdin


def _fill_secret_params(params: dict, spec, args: argparse.Namespace) -> None:
    """Reads the declared SECRET parameters from stdin (never argv):
    ``password``, ``new_password``, ``organization_password`` (rule
    3). Authentication parameters (*_auth) are excluded —
    they are resolved by the authentication block."""
    for name, _typ, _required, _validator in spec[1]:
        if name.endswith("_auth") or "password" not in name:
            continue
        if params.get(name) is not None:
            raise SystemExit(emit_error(
                f"password forbidden as a command argument: "
                f"--{name.replace('_', '-')} (read it via --password-stdin "
                "or getpass — SPEC_CLI §2)"
            ))
        params[name] = read_password(args, f"{name}: ")


def _coerce(value: str):
    """Flexible conversion: booleans and integers for typed parameters."""
    lowered = value.lower()
    if lowered in ("true", "yes", "1"):
        return True
    if lowered in ("false", "no", "0"):
        return False
    if value.isdigit():
        return int(value)
    return value


def _unique_org(config, token: str) -> str:  # noqa: ANN001
    from ..service import _WEB_LOCAL

    data = Client.from_config(config).list_orgs(_WEB_LOCAL, token)
    orgs = [o["organization_name"] for o in data.get("organizations", [])]
    if len(orgs) == 1:
        return orgs[0]
    if not orgs:
        raise SystemExit(emit_error(
            "no active organization: specify --organization-name"
        ))
    raise SystemExit(emit_error(
        "multiple active organizations: specify --organization-name"
    ))
