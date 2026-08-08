"""Service logging.

Logs never contain passwords nor message content.
Only ``username``, ``command``, ``target_id``, ``timestamp``,
``result`` and ``process_id`` are allowed (specification section 4).

Logs are written in JSON-lines format, one file per day
(rotation at midnight), kept ``log_retention_days`` days then deleted
automatically.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

_ALLOWED_FIELDS = ("timestamp", "process_id", "username", "command", "target_id", "result")


class JsonFormatter(logging.Formatter):
    """Formats each record as a JSON line with the allowed fields.

    For a logged exception, only the **type** of the exception is
    included (never its message nor its traceback: no content can leak),
    to ease diagnosis without compromising confidentiality.
    """

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        # time.strftime (used by logging) does not know %f: we generate
        # the timestamp via datetime, in the specification format
        # (YYYY-MM-DDTHH:MM:SS.sssZ, milliseconds).
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        ms = dt.microsecond // 1000
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{ms:03d}Z"

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "process_id": os.getpid(),
        }
        for field in _ALLOWED_FIELDS:
            if field in ("timestamp", "process_id"):
                continue
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception_type"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False)


def _make_handler(log_dir: str, retention_days: int, name: str) -> logging.Handler:
    Path(log_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    handler = logging.handlers.TimedRotatingFileHandler(
        os.path.join(log_dir, name),
        when="midnight",
        backupCount=retention_days,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())
    return handler


def setup_logging(config: Config, verbose: bool = False,
                  log_name: str = "synapse.log",
                  error_log_name: str = "synapse.error.log",
                  level: int = logging.INFO) -> None:
    """Configures a service's logging (file + optionally
    console).

    ``log_name``/``error_log_name`` let each process
    log to its own file (server: ``synapse.log``;
    web interface: ``web.log`` — SPEC_CLI ``synapse web logs``).
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(_make_handler(config.log_dir, config.log_retention_days, log_name))
    error_logger = logging.getLogger("synapse.error")
    error_logger.setLevel(logging.ERROR)
    error_logger.propagate = False
    error_logger.handlers.clear()
    error_logger.addHandler(
        _make_handler(config.log_dir, config.log_retention_days, error_log_name)
    )
    if verbose:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(console)
