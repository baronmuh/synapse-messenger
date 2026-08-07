"""``logs`` group (SPEC_CLI §4.14): merged server + web logs."""

from __future__ import annotations

import argparse
import json
import os
import time

from .common import (
    EXIT_OK,
    resolve_config,
)

GROUP = "logs"

_EXAMPLES = """\
Exemples :
  synapse logs --follow          journaux server + web en suivi continu
  synapse logs web --lines 50    web logs uniquement
  synapse logs server --lines 200
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="merged logs (server + web; or a single service)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("service", nargs="?", choices=["server", "web"],
                   default=None,
                   help="server or web (default: both, merged)")
    p.add_argument("--follow", "-f", action="store_true", help="suivi continu")
    p.add_argument("--lines", type=int, default=100,
                   help="lines per file (default: 100)")
    p.set_defaults(run=_cmd_logs)


def _cmd_logs(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    log_dir = config.log_dir
    if args.service == "server":
        return tail_log(os.path.join(log_dir, "synapse.log"),
                        lines=args.lines, follow=args.follow)
    if args.service == "web":
        return tail_log(os.path.join(log_dir, "web.log"),
                        lines=args.lines, follow=args.follow)
    # Merge server + web, sorted by timestamp when lines are
    # des JSON (le format standard des journaux du service).
    files = [os.path.join(log_dir, "synapse.log"), os.path.join(log_dir, "web.log")]
    return tail_log(files, lines=args.lines, follow=args.follow)


def _read_tail(path: str, lines: int) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return []
    all_lines = content.splitlines()
    return all_lines[-lines:] if lines > 0 else all_lines


def _line_key(line: str) -> str:
    """Sort key: JSON timestamp if present, otherwise the raw line."""
    try:
        entry = json.loads(line)
        ts = entry.get("timestamp")
        if isinstance(ts, str):
            return ts
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return line


def tail_log(paths, *, lines: int = 100, follow: bool = False) -> int:  # noqa: ANN001
    """Prints the tail of the logs (one file or several merged)."""
    if isinstance(paths, str):
        paths = [paths]
    existing = [p for p in paths if os.path.exists(p)]
    if not existing:
        for p in paths:
            print(f"(log file absent: {p})")
        return EXIT_OK

    merged = []
    for path in existing:
        merged.extend(_read_tail(path, lines))
    merged.sort(key=_line_key)
    for line in merged:
        print(line)

    if follow:
        positions = {p: os.path.getsize(p) for p in existing}
        try:
            while True:
                for path in existing:
                    try:
                        size = os.path.getsize(path)
                        if size > positions[path]:
                            with open(path, encoding="utf-8") as fh:
                                fh.seek(positions[path])
                                for raw in fh:
                                    print(raw, end="")
                            positions[path] = size
                    except OSError:
                        pass
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    return EXIT_OK
