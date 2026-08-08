"""Encrypted backup and restore of the storage.

Backups are encrypted (AES-256-GCM) with a key kept **outside**
the backups themselves (``<storage_dir>/backup.key``, permissions
``0600``). They contain the consistent copy of the database (accounts, roles,
states, messages, conversations, UUIDs, dates, statuses, idempotency keys)
as well as the cursor signing key: a restore re-establishes the
values without regenerating identifiers or modifying dates or statuses.

File format (``.synbk`` extension):
    magic ``SYNBK\\x01`` (7 bytes) | 12-byte nonce | AES-GCM ciphertext
    where the ciphertext contains a JSON header line then the SQLite bytes.

Restore requires the service to be stopped (no lock) and the key
present.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Config
from .db import StorageError, ensure_storage
from .security import load_or_create_key

_MAGIC = b"SYNBK\x01\n"
_FORMAT = 1
_NONCE_LENGTH = 12


class BackupError(Exception):
    """Backup or restore error."""


def _now() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y%m%d-%H%M%S")


def _temp_path(config: Config, prefix: str, suffix: str) -> str:
    """Path of a temporary file **inside** the storage directory.

    The directory is 0700 (no other account can read it) and the
    final ``os.replace`` stays on the same filesystem (no
    EXDEV). The file is created 0600.
    """
    ensure_storage(config)
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=config.storage_dir)
    os.close(fd)
    os.chmod(path, 0o600)
    return path


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup(config: Config, output_path: str | None = None) -> str:
    """Creates an encrypted backup; returns the path of the created file."""
    try:
        return _backup(config, output_path)
    except BackupError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"Backup failed: {exc}") from exc


def _backup(config: Config, output_path: str | None) -> str:
    ensure_storage(config)
    key = load_or_create_key(config.backup_key_path)
    cursor_key = load_or_create_key(config.cursor_key_path)

    # Consistent copy of the database (SQLite's backup API handles the WAL), in
    # a temporary file of the storage directory (0700, same FS).
    tmp_path = _temp_path(config, "synapse-bak-", ".db")
    try:
        src = sqlite3.connect(config.db_path)
        try:
            dst = sqlite3.connect(tmp_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        db_bytes = Path(tmp_path).read_bytes()
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

    header = {
        "format": _FORMAT,
        "created_at": _now(),
        "cursor_key": base64.b64encode(cursor_key).decode("ascii"),
    }
    plaintext = json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n" + db_bytes

    nonce = os.urandom(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)

    if output_path is None:
        backup_dir = Path(config.backup_dir)
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(backup_dir, 0o700)
        output_path = str(backup_dir / f"synapse-backup-{_now()}.synbk")
    with open(output_path, "wb") as fh:
        fh.write(_MAGIC + nonce + ciphertext)
    os.chmod(output_path, 0o600)
    return output_path


# ---------------------------------------------------------------------------
# Archive reading / decryption
# ---------------------------------------------------------------------------


def _decrypt_archive(config: Config, backup_path: str) -> tuple[dict, bytes]:
    """Decrypts and validates an archive: returns ``(header, db_bytes)``.

    Any anomaly (magic, decryption, header, format, cursor key)
    raises ``BackupError``. Used by ``restore`` and by
    ``verify`` (SPEC_PRODUCTION §3).
    """
    if not Path(backup_path).exists():
        raise BackupError(f"Backup not found: {backup_path}")
    data = Path(backup_path).read_bytes()
    if not data.startswith(_MAGIC):
        raise BackupError("Unrecognized file (invalid magic)")
    nonce = data[len(_MAGIC): len(_MAGIC) + _NONCE_LENGTH]
    ciphertext = data[len(_MAGIC) + _NONCE_LENGTH:]
    key = load_or_create_key(config.backup_key_path)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise BackupError(
            "Decryption failed: corrupted backup or wrong key"
        ) from exc

    header_line, sep, db_bytes = plaintext.partition(b"\n")
    if not sep:
        raise BackupError("Corrupted backup (missing header)")
    try:
        header = json.loads(header_line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupError("Corrupted backup (invalid header)") from exc
    if header.get("format") != _FORMAT:
        raise BackupError("Unsupported backup version")
    cursor_key_b64 = header.get("cursor_key")
    if not isinstance(cursor_key_b64, str):
        raise BackupError("Corrupted backup (missing cursor key)")
    try:
        cursor_key = base64.b64decode(cursor_key_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise BackupError("Corrupted backup (invalid cursor key)") from exc
    if len(cursor_key) != 32:
        raise BackupError("Corrupted backup (invalid cursor key)")
    return header, db_bytes


def _check_sqlite_integrity(db_path: str) -> int:
    """Verifies the SQLite integrity of a database file; returns the number
    of application tables (excluding the internal ``sqlite_*`` tables)."""
    check = sqlite3.connect(db_path)
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            raise BackupError("Corrupted backup (invalid SQLite integrity)")
        tables = check.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    finally:
        check.close()
    return tables


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(config: Config, backup_path: str) -> None:
    """Restores the storage from an encrypted backup.

    The service lock is **acquired for the whole duration** of the
    restore (not just checked): no server can start
    between the check and the database replacement. The database is
    fully replaced; the cursor signing key is
    restored.
    """
    try:
        _restore(config, backup_path)
    except BackupError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"Restore failed: {exc}") from exc


def _restore(config: Config, backup_path: str) -> None:
    lock_held = _acquire_service_lock(config)
    try:
        header, db_bytes = _decrypt_archive(config, backup_path)

        # Integrity check before replacement (the validated temporary file
        # is then atomically moved to the database).
        tmp_path = _temp_path(config, "synapse-res-", ".db")
        try:
            Path(tmp_path).write_bytes(db_bytes)
            _check_sqlite_integrity(tmp_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

        ensure_storage(config)
        # Atomic replacement of the database and WAL cleanup.
        os.replace(tmp_path, config.db_path)
        for suffix in ("-wal", "-shm"):
            try:
                os.unlink(config.db_path + suffix)
            except FileNotFoundError:
                pass
        os.chmod(config.db_path, 0o600)
        cursor_key = base64.b64decode(header["cursor_key"], validate=True)
        with open(config.cursor_key_path, "wb") as fh:
            fh.write(cursor_key)
        os.chmod(config.cursor_key_path, 0o600)
        _fsync_dir(os.path.dirname(config.db_path))
    finally:
        if lock_held:
            _release_service_lock(config)


# ---------------------------------------------------------------------------
# Retention and verification (SPEC_PRODUCTION §3)
# ---------------------------------------------------------------------------


def prune(config: Config, keep: int = 14) -> list[str]:
    """Deletes the oldest archives beyond the ``keep`` most
    recent ones; returns the list of deleted paths.

    Only touches the ``*.synbk`` files of the configured ``backup_dir`` — never an
    arbitrary path (entries are sorted by modification date).
    """
    if keep < 1:
        raise ValueError("keep must be >= 1")
    backup_dir = Path(config.backup_dir)
    if not backup_dir.is_dir():
        return []

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    archives = sorted(backup_dir.glob("*.synbk"), key=_mtime, reverse=True)
    deleted: list[str] = []
    for path in archives[keep:]:
        try:
            path.unlink()
        except OSError as exc:
            raise BackupError(f"Cannot delete {path} : {exc}") from exc
        deleted.append(str(path))
    return deleted


def _check_scratch_isolation(config: Config, scratch_dir: str) -> None:
    """Refuses a verification directory that would contain the production
    storage (the database file would be written there)."""
    real_scratch = os.path.realpath(scratch_dir)
    real_storage = os.path.realpath(config.storage_dir)
    try:
        contained = os.path.commonpath([real_scratch, real_storage]) == real_storage
    except ValueError:
        contained = False  # distinct filesystems (e.g. /tmp vs /var/lib)
    if real_scratch == real_storage or contained:
        raise BackupError(
            "invalid verification directory: it must not contain "
            "the production storage"
        )


def verify(config: Config, backup_path: str,
           scratch_dir: str | None = None) -> dict:
    """Verifies a backup WITHOUT touching production.

    Decrypts the archive (AES-GCM authentication), restores the database into
    an isolated temporary storage, verifies SQLite integrity and counts the
    tables, then destroys the scratch. The service lock is neither acquired
    nor consulted: production is never modified.

    ``scratch_dir``: working directory (temporarily created if
    absent). Returns a report: archive, format, created_at, integrity,
    number of tables.
    """
    try:
        return _verify(config, backup_path, scratch_dir)
    except BackupError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"Verification failed: {exc}") from exc


def _verify(config: Config, backup_path: str,
            scratch_dir: str | None) -> dict:
    header, db_bytes = _decrypt_archive(config, backup_path)

    remove_scratch = scratch_dir is None
    if remove_scratch:
        scratch = tempfile.mkdtemp(prefix="synapse-verify-")
    else:
        scratch = str(scratch_dir)
        _check_scratch_isolation(config, scratch)
        os.makedirs(scratch, mode=0o700, exist_ok=True)

    db_file = os.path.join(scratch, "synapse.db")
    try:
        Path(db_file).write_bytes(db_bytes)
        os.chmod(db_file, 0o600)
        tables = _check_sqlite_integrity(db_file)
    finally:
        try:
            os.unlink(db_file)
        except OSError:
            pass
        if remove_scratch:
            try:
                os.rmdir(scratch)
            except OSError:
                pass

    return {
        "archive": str(backup_path),
        "format": header.get("format"),
        "created_at": header.get("created_at"),
        "integrity": "ok",
        "tables": tables,
    }


def _acquire_service_lock(config: Config) -> bool:
    """Acquires the service lock for the duration of the restore.

    Raises ``BackupError`` if a server is running (active lock). A stale
    lock (dead PID, e.g. after a crash) is removed automatically.
    """
    from .server import lock_is_stale

    lock_path = Path(config.lock_path)
    if lock_path.exists():
        if lock_is_stale(lock_path):
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass
        else:
            raise BackupError(
                "The service is running: stop it before restoring"
            )
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BackupError(
            "The service is running: stop it before restoring"
        ) from exc
    os.write(fd, b"restore")
    os.close(fd)
    return True


def _release_service_lock(config: Config) -> None:
    try:
        os.unlink(config.lock_path)
    except FileNotFoundError:
        pass


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Console entry points
# ---------------------------------------------------------------------------


def _common_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--config", default=None, help="JSON configuration file path")
    return parser


def backup_main() -> None:
    parser = _common_parser("synapse-backup", "Creates an encrypted backup of the Synapse storage")
    parser.add_argument("--out", default=None, help="Output file path (default: backup_dir)")
    args = parser.parse_args()
    try:
        config = Config.load(args.config)
        path = backup(config, args.out)
    except (ValueError, StorageError, BackupError) as exc:
        print(f"synapse-backup: {exc}", file=sys.stderr)
        sys.exit(1)
    print(path)


def restore_main() -> None:
    parser = _common_parser("synapse-restore", "Restores the Synapse storage from a backup")
    parser.add_argument("backup", help="Path of the .synbk file to restore")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Confirms the destruction of current data",
    )
    args = parser.parse_args()
    if not args.force:
        print(
            "synapse-restore: destructive operation — pass --force to confirm",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        config = Config.load(args.config)
        restore(config, args.backup)
    except (ValueError, StorageError, BackupError) as exc:
        print(f"synapse-restore: {exc}", file=sys.stderr)
        sys.exit(1)
    print("Restore complete.")
