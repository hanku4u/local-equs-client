"""Unit tests for ``local_equs_client.config.logging`` (C0.8)."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from local_equs_client.config import logging as app_logging
from local_equs_client.config import paths


@pytest.fixture(autouse=True)
def _isolated_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    monkeypatch.delenv(app_logging.DEV_ENV_VAR, raising=False)
    yield tmp_path
    for handler in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_configure_creates_log_file(_isolated_app_dir: Path) -> None:
    app_logging.configure_logging()
    logging.getLogger("test").info("hello")
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = _isolated_app_dir / "logs" / app_logging.LOG_FILENAME
    assert log_file.is_file()
    assert "hello" in log_file.read_text(encoding="utf-8")


def test_rotating_handler_configured(_isolated_app_dir: Path) -> None:
    app_logging.configure_logging()
    rotating = [
        h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)
    ]
    assert len(rotating) == 1
    assert rotating[0].when == "MIDNIGHT"
    assert rotating[0].backupCount == app_logging.RETENTION_DAYS


def test_dev_mode_adds_stderr_handler(
    _isolated_app_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(app_logging.DEV_ENV_VAR, "1")
    app_logging.configure_logging()

    stream_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, TimedRotatingFileHandler)
    ]
    assert len(stream_handlers) == 1


def test_idempotent_reset(_isolated_app_dir: Path) -> None:
    app_logging.configure_logging()
    initial_count = len(logging.getLogger().handlers)
    app_logging.configure_logging()
    assert len(logging.getLogger().handlers) == initial_count
