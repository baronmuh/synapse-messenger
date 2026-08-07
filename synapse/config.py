"""Configuration du service.

The configuration is a minimal JSON file. The default paths target
a system installation (``/etc/synapse``, ``/var/lib/synapse``, ``/var/run/synapse``,
``/var/log/synapse``, ``/var/backups/synapse``); in development, everything can be
redirected to a test directory via ``--config`` or the
``Synapse_CONFIG`` environment variable (no secret is ever passed via
l'environnement : uniquement le chemin du fichier de configuration).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/etc/synapse/config.json")

# Limits fixed by the API v1 specification.
DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024  # 1 MiB
DEFAULT_AUTH_MAX_FAILURES = 5
DEFAULT_AUTH_WINDOW_SECONDS = 15 * 60  # sliding window of 15 minutes
DEFAULT_LOG_RETENTION_DAYS = 90
DEFAULT_EVENT_RETENTION_DAYS = 90

# Interface web humaine (SPEC-WEB §6) : sessions par utilisateur.
DEFAULT_WEB_SESSION_TTL_SECONDS = 15 * 60  # expiration on inactivity
DEFAULT_WEB_LOGIN_MAX_ATTEMPTS = 5  # failures before lockout
DEFAULT_WEB_LOGIN_LOCKOUT_SECONDS = 15 * 60  # lockout duration
DEFAULT_WEB_MAX_SESSIONS = 3  # simultaneous sessions per organization


@dataclass(frozen=True)
class Config:
    storage_dir: str = "/var/lib/synapse"
    socket_path: str = "/var/run/synapse/synapse.sock"
    log_dir: str = "/var/log/synapse"
    backup_dir: str = "/var/backups/synapse"
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    auth_max_failures: int = DEFAULT_AUTH_MAX_FAILURES
    auth_window_seconds: int = DEFAULT_AUTH_WINDOW_SECONDS
    auth_cache_ttl_seconds: float = 30.0  # window remembering a successful authentication
    log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS
    event_retention_days: int = DEFAULT_EVENT_RETENTION_DAYS  # retention of consultable events (F10)
    db_busy_timeout_ms: int = 10_000
    # Interface web humaine (SPEC-WEB §6) : sessions par utilisateur.
    web_session_ttl_seconds: int = DEFAULT_WEB_SESSION_TTL_SECONDS
    web_login_max_attempts: int = DEFAULT_WEB_LOGIN_MAX_ATTEMPTS
    web_login_lockout_seconds: int = DEFAULT_WEB_LOGIN_LOCKOUT_SECONDS
    web_max_sessions: int = DEFAULT_WEB_MAX_SESSIONS
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    # --- Derived paths -------------------------------------------------
    @property
    def db_path(self) -> str:
        return os.path.join(self.storage_dir, "synapse.db")

    @property
    def cursor_key_path(self) -> str:
        return os.path.join(self.storage_dir, "cursor.key")

    @property
    def backup_key_path(self) -> str:
        return os.path.join(self.storage_dir, "backup.key")

    @property
    def lock_path(self) -> str:
        return os.path.join(self.storage_dir, "service.lock")

    # --- Chargement ------------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        """Charge la configuration depuis un fichier JSON.

        Le fichier peut omettre n'importe quel champ : les valeurs par
        defaults apply. Any unknown field is silently ignored
        (la configuration n'is not the contract of theAPI).
        """
        config_path = Path(path or os.environ.get("Synapse_CONFIG") or DEFAULT_CONFIG_PATH)
        data: dict = {}
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Configuration illisible : {config_path} ({exc})") from exc
        if not isinstance(data, dict):
            raise ValueError("The configuration must be a JSON object")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f.name: f for f in fields(cls) if f.name != "_extra"}
        values: dict = {}
        extra: dict = {}
        for key, value in data.items():
            if key in known:
                values[key] = value
            else:
                extra[key] = value
        return cls(**values, _extra=extra)

    def to_dict(self) -> dict:
        result = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name != "_extra"
        }
        return result
