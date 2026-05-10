"""App-data, data, logs, and config-file path conventions (C0.3).

All app-owned filesystem locations derive from a single ``app_dir()`` root so
that tests and dev sessions can relocate the entire tree by setting
``LOCAL_EQUS_APP_DIR``. Production layout follows the OS conventions:

- Windows: ``%LOCALAPPDATA%\\LocalEQUS\\``
- macOS:   ``~/Library/Application Support/LocalEQUS``
- Linux:   ``$XDG_DATA_HOME/LocalEQUS`` or ``~/.local/share/LocalEQUS``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "LocalEQUS"
APP_DIR_ENV_VAR = "LOCAL_EQUS_APP_DIR"


def app_dir() -> Path:
    """Return the root directory holding all app-owned state."""
    override = os.environ.get(APP_DIR_ENV_VAR)
    if override:
        return Path(override)

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    base_path = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base_path / APP_NAME


def data_dir() -> Path:
    """Directory holding downloaded parquet files."""
    return app_dir() / "data"


def state_db() -> Path:
    """Path to the SQLite state database."""
    return app_dir() / "state.db"


def logs_dir() -> Path:
    """Directory holding rotating log files."""
    return app_dir() / "logs"


def config_file() -> Path:
    """Path to the user-editable ``config.toml``."""
    return app_dir() / "config.toml"
