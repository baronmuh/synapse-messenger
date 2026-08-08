"""``backup`` group (SPEC_CLI §4.12): encrypted backups."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..backup import BackupError, backup, prune, restore, verify
from ..config import Config
from ..db import StorageError
from .common import (
    EXIT_OK,
    emit,
    emit_error,
    resolve_config,
    table,
)

GROUP = "backup"

_EXAMPLES = """\
Examples:
  synapse backup create --dir /srv/backups
  synapse server stop && synapse backup restore /srv/backups/2026-08-06.synbk --force
  synapse backup list --json
  synapse backup prune --keep 14           retention: at most 14 archives
  synapse backup verify --latest           restore proof (weekly)
  synapse backup verify /srv/backups/x.synbk --dir /tmp/scratch
"""


def add_parser(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        GROUP,
        help="backup and restore (create, restore, list, prune, verify)",
        parents=[common],
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="action", required=True)

    a = actions.add_parser("create", parents=[common],
                           help="full backup (SQLite database + keys)")
    a.add_argument("--dir", default=None,
                   help="output directory (default: backup_dir from config)")
    a.add_argument("--out", default=None,
                   help="exact file path (synapse-backup compatibility)")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_create)

    a = actions.add_parser("restore", parents=[common],
                           help="restore from an archive (server stopped)")
    a.add_argument("archive", help="path of the .synbk file")
    a.add_argument("--force", action="store_true",
                   help="confirms the destruction of current data")
    a.add_argument("--json", action="store_true",
                   help="machine JSON output")
    a.set_defaults(run=_cmd_restore)

    a = actions.add_parser("list", parents=[common],
                           help="lists the available archives")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_list)

    a = actions.add_parser("prune", parents=[common],
                           help="retention: deletes archives beyond --keep")
    a.add_argument("--keep", type=int, default=14,
                   help="number of archives to keep (default: 14)")
    a.add_argument("--dir", default=None,
                   help="archives directory (default: backup_dir from config)")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_prune)

    a = actions.add_parser("verify", parents=[common],
                           help="restore proof: verifies an archive "
                                "in isolated storage, without touching production")
    a.add_argument("archive", nargs="?", default=None,
                   help="path of the .synbk file (or --latest)")
    a.add_argument("--latest", action="store_true",
                   help="verify the most recent archive of backup_dir")
    a.add_argument("--dir", default=None,
                   help="working (scratch) directory; created temporarily "
                        "by default; must not contain production storage")
    a.add_argument("--json", action="store_true")
    a.set_defaults(run=_cmd_verify)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_create(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    output = args.out
    if args.dir is not None:
        output = None  # backup() writes into the directory with a timestamped name
        config = _with_backup_dir(config, args.dir)
    try:
        path = backup(config, output)
    except (ValueError, StorageError, BackupError, OSError) as exc:
        return emit_error(str(exc))
    print(path)
    print("Encrypted backup (AES-256-GCM) complete.")
    return EXIT_OK


def _with_backup_dir(config: Config, backup_dir: str) -> Config:
    data = config.to_dict()
    data["backup_dir"] = backup_dir
    return Config.from_dict(data)


def _cmd_restore(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if not args.force:
        return emit_error(
            "destructive operation — pass --force to confirm "
            "(current data will be replaced)"
        )
    try:
        restore(config, args.archive)
    except (ValueError, StorageError, BackupError, OSError) as exc:
        return emit_error(str(exc))
    print("Restore complete.")
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    backup_dir = Path(config.backup_dir)
    entries = []
    if backup_dir.is_dir():
        for path in sorted(backup_dir.glob("*.synbk"), key=lambda p: p.stat().st_mtime,
                           reverse=True):
            stat = path.stat()
            entries.append({
                "path": str(path),
                "name": path.name,
                "size": stat.st_size,
                "created_at": _header_date(config, path),
                "format": _header_format(config, path),
            })
    if getattr(args, "json", False):
        return emit(args, {"backups": entries})
    if not entries:
        print(f"no backup in {backup_dir}")
        return EXIT_OK
    rows = [
        [e["name"], str(e["size"]), e["created_at"] or "?", str(e["format"] or "?")]
        for e in entries
    ]
    print(table(rows, ["archive", "size", "created", "format"]))
    return EXIT_OK


def _cmd_prune(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    if args.dir is not None:
        config = _with_backup_dir(config, args.dir)
    try:
        deleted = prune(config, args.keep)
    except (ValueError, BackupError, OSError) as exc:
        return emit_error(str(exc))
    if getattr(args, "json", False):
        return emit(args, {"deleted": deleted, "kept": args.keep})
    if not deleted:
        print(f"retention respected: at most {args.keep} archive(s) in "
              f"{config.backup_dir}")
    else:
        print(f"{len(deleted)} archive(s) deleted (retention: {args.keep}):")
        for path in deleted:
            print(f"  - {path}")
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    archive = args.archive
    if args.latest:
        backup_dir = Path(config.backup_dir)
        if not backup_dir.is_dir():
            return emit_error(f"no backup in {config.backup_dir}")
        archives = sorted(backup_dir.glob("*.synbk"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        if not archives:
            return emit_error(f"no backup in {config.backup_dir}")
        archive = str(archives[0])
    if not archive:
        return emit_error("an archive is required: pass a path or --latest")
    try:
        report = verify(config, archive, scratch_dir=args.dir)
    except (ValueError, BackupError, OSError) as exc:
        return emit_error(str(exc))
    if getattr(args, "json", False):
        return emit(args, report)
    print(f"Valid backup: {report['archive']}")
    print(f"  format      {report.get('format')}")
    print(f"  created     {report.get('created_at') or 'unknown'}")
    print(f"  integrity   {report['integrity']} (SQLite)")
    print(f"  tables      {report['tables']}")
    print("Restore proven in isolated storage; production untouched.")
    return EXIT_OK


def _header(config: Config, path: Path) -> dict | None:
    """Decrypted header of an archive (format + creation date)."""
    from ..backup import _MAGIC, _NONCE_LENGTH
    from ..security import load_or_create_key
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        data = path.read_bytes()
        if not data.startswith(_MAGIC):
            return None
        nonce = data[len(_MAGIC): len(_MAGIC) + _NONCE_LENGTH]
        ciphertext = data[len(_MAGIC) + _NONCE_LENGTH:]
        key = load_or_create_key(config.backup_key_path)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        header_line = plaintext.split(b"\n", 1)[0]
        return json.loads(header_line.decode("utf-8"))
    except (OSError, InvalidTag, ValueError, KeyError):
        return None


def _header_date(config: Config, path: Path) -> str | None:
    header = _header(config, path)
    return header.get("created_at") if header else None


def _header_format(config: Config, path: Path) -> int | None:
    header = _header(config, path)
    return header.get("format") if header else None


def _api_error(exc: Exception) -> int:
    return emit_error(str(exc))
