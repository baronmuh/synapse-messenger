"""Service configuration.

The configuration is a minimal JSON file. The default paths target
a system installation (``/etc/synapse``, ``/var/lib/synapse``, ``/var/run/synapse``,
``/var/log/synapse``, ``/var/backups/synapse``); in development, everything can be
redirected to a test directory via ``--config`` or the
``Synapse_CONFIG`` environment variable (no secret is ever passed via
the environment: only the path of the configuration file).
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

# Human web interface (SPEC-WEB §6): sessions per user.
DEFAULT_WEB_SESSION_TTL_SECONDS = 15 * 60  # expiration on inactivity
DEFAULT_WEB_LOGIN_MAX_ATTEMPTS = 5  # failures before lockout
DEFAULT_WEB_LOGIN_LOCKOUT_SECONDS = 15 * 60  # lockout duration
DEFAULT_WEB_MAX_SESSIONS = 3  # simultaneous sessions per organization


@dataclass(frozen=True)
class Config:
    storage_dir: str = ""
    socket_path: str = ""
    log_dir: str = ""
    backup_dir: str = ""
    # Local transport: "unix" (POSIX default), "tcp" (loopback + token,
    # Windows default) or "" (platform default — see platform.default_transport).
    transport: str = ""
    transport_port: int = 0  # TCP loopback port (0 = DEFAULT_TRANSPORT_PORT)
    run_dir: str = ""  # PID files, web token, transport token ("" = derived)
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    auth_max_failures: int = DEFAULT_AUTH_MAX_FAILURES
    auth_window_seconds: int = DEFAULT_AUTH_WINDOW_SECONDS
    auth_cache_ttl_seconds: float = 30.0  # window remembering a successful authentication
    log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS
    event_retention_days: int = DEFAULT_EVENT_RETENTION_DAYS  # retention of consultable events (F10)
    db_busy_timeout_ms: int = 10_000
    # Human web interface (SPEC-WEB §6): sessions per user.
    web_session_ttl_seconds: int = DEFAULT_WEB_SESSION_TTL_SECONDS
    web_login_max_attempts: int = DEFAULT_WEB_LOGIN_MAX_ATTEMPTS
    web_login_lockout_seconds: int = DEFAULT_WEB_LOGIN_LOCKOUT_SECONDS
    web_max_sessions: int = DEFAULT_WEB_MAX_SESSIONS
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Applies the platform defaults to empty path fields (Config() and
        Config.from_dict both resolve them the same way)."""
        from . import platform as _platform

        defaults = _platform.default_paths()
        replacements = {
            "storage_dir": defaults["storage"],
            "socket_path": str(Path(defaults["run"]) / "synapse.sock"),
            "log_dir": defaults["log"],
            "backup_dir": defaults["backup"],
        }
        for name, value in replacements.items():
            if not getattr(self, name):
                object.__setattr__(self, name, value)

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

    # --- Loading ------------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        """Loads the configuration from a JSON file.

        The file may omit any field: platform defaults apply (Linux keeps
        the systemd-oriented /var|/etc locations; macOS and Windows use
        per-user directories). Any unknown field is silently ignored.
        """
        from . import platform as _platform

        config_path = Path(
            path or os.environ.get("Synapse_CONFIG") or _platform.default_paths()["config"]
        )
        data: dict = {}
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Unreadable configuration: {config_path} ({exc})") from exc
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
        # Empty path fields are resolved by __post_init__ to the platform
        # defaults (same behavior as Config()).
        return cls(**values, _extra=extra)

    def to_dict(self) -> dict:
        result = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name != "_extra"
        }
        return result
