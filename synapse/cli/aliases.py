"""Legacy binaries now DEPRECATED ALIASES (SPEC_CLI §6, decision §7.1).

The 6 original entry points (``synapse-server``, ``synapse-web``,
``synapse-init-org``, ``synapse-backup``, ``synapse-restore``,
``synapse-a2a-bridge``) delegate to the unified CLI and print a
deprecation warning. They will be removed in the next major version.

Each alias translates its arguments to the equivalent unified command
and preserves the observable behavior: the servers/web/bridge stay in the
foreground (systemd/supervisor compatibility).
"""

from __future__ import annotations

import sys


def _warn(legacy: str, new: str) -> None:
    print(f'"{legacy}": deprecated — use "{new}" (SPEC_CLI §6). '
          "This alias will be removed in the next major version.",
          file=sys.stderr)


def _translate(argv: list[str], take: tuple[str, ...]) -> list[str]:
    """Translates the arguments of the legacy binary: only keeps the
    listed options (with their value) — the others are ignored."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in take:
            out.append(arg)
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out.append(argv[i + 1])
                i += 2
                continue
        i += 1
    return out


def server_alias_main() -> int:
    from .main import main as unified_main

    args = _translate(sys.argv[1:], ("--config",))
    _warn("synapse-server", "synapse server start --foreground")
    return unified_main(["server", "start", "--foreground", *args])


def web_alias_main() -> int:
    from .main import main as unified_main

    args = _translate(sys.argv[1:], ("--config", "--port"))
    _warn("synapse-web", "synapse web start --foreground")
    return unified_main(["web", "start", "--foreground", *args])


def init_org_alias_main() -> int:
    from .main import main as unified_main

    argv = sys.argv[1:]
    enable = None
    config: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--enable" and i + 1 < len(argv):
            enable = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--config" and i + 1 < len(argv):
            config = ["--config", argv[i + 1]]
            i += 2
            continue
        i += 1
    if enable is not None:
        _warn("synapse-init-org", f"synapse org enable {enable}")
        return unified_main(["org", "enable", enable, *config])
    _warn("synapse-init-org", "synapse org init <name>")
    return unified_main(["org", "init", *config])


def backup_alias_main() -> int:
    from .main import main as unified_main

    args = _translate(sys.argv[1:], ("--config", "--out"))
    _warn("synapse-backup", "synapse backup create")
    return unified_main(["backup", "create", *args])


def restore_alias_main() -> int:
    from .main import main as unified_main

    argv = sys.argv[1:]
    archive = None
    args: list[str] = []
    for arg in argv:
        if arg in ("--config", "--force"):
            args.append(arg)
        elif arg.startswith("--") and arg not in ("--config", "--force"):
            pass  # unknown options ignored (compat)
        elif archive is None and not arg.startswith("--"):
            archive = arg
        else:
            args.append(arg)
    if archive is None:
        print("synapse-restore : archive manquante", file=sys.stderr)
        return 1
    _warn("synapse-restore", f"synapse backup restore {archive} --force")
    return unified_main(["backup", "restore", archive, *args])


def a2a_bridge_alias_main() -> int:
    from .main import main as unified_main

    args = _translate(sys.argv[1:],
                      ("--config", "--agent-name", "--port",
                       "--password-stdin", "--token-stdin"))
    _warn("synapse-a2a-bridge", "synapse a2a start")
    return unified_main(["a2a", "start", *args])
