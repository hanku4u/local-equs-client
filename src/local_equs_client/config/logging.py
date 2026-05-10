"""File-based rotating logger configuration for the client (C0.8).

Call :func:`configure_logging` once from the application entry point. After that,
``logging.getLogger(__name__)`` anywhere in the codebase emits to the daily
rotating file in ``paths.logs_dir()``. When ``EQUS_DEV=1``, output is also
mirrored to stderr.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

from local_equs_client.config import paths

LOG_FILENAME = "local-equs-client.log"
RETENTION_DAYS = 14
DEV_ENV_VAR = "EQUS_DEV"

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Wire up the rotating file handler (and dev-mode stderr mirror).

    Idempotent: subsequent calls reset handlers so test harnesses can reconfigure.
    """
    global _configured

    log_dir = paths.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = TimedRotatingFileHandler(
        log_dir / LOG_FILENAME,
        when="midnight",
        backupCount=RETENTION_DAYS,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [file_handler]
    if os.environ.get(DEV_ENV_VAR) == "1":
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        handlers.append(stderr_handler)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        existing.close()

    root.setLevel(level)
    for handler in handlers:
        root.addHandler(handler)

    _configured = True
