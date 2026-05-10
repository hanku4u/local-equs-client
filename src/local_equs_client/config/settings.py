"""Process-wide ``Settings`` singleton backed by ``config.toml`` (C0.3, C2.1, C5.10).

M0 scope is the data directory only. Server URL, telemetry opt-out, update-check
frequency, and the permissions-simulate-admin flag get added by later tasks.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock

from local_equs_client.config import paths


@dataclass(frozen=True)
class Settings:
    """Effective application settings.

    Construct via :func:`get_settings`; do not instantiate directly outside tests.
    """

    data_dir: Path

    @classmethod
    def defaults(cls) -> Settings:
        return cls(data_dir=paths.data_dir())

    @classmethod
    def from_file(cls, path: Path) -> Settings:
        """Load settings from ``config.toml``, filling missing fields with defaults."""
        defaults = cls.defaults()
        if not path.is_file():
            return defaults

        with path.open("rb") as fh:
            raw = tomllib.load(fh)

        data_dir = raw.get("data_dir")
        return replace(
            defaults,
            data_dir=Path(data_dir).expanduser() if data_dir else defaults.data_dir,
        )


_instance: Settings | None = None
_lock = Lock()


def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`, loading on first call."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = Settings.from_file(paths.config_file())
    return _instance


def reset_settings() -> None:
    """Drop the cached singleton. Intended for tests."""
    global _instance
    with _lock:
        _instance = None


def save(new_settings: Settings) -> None:
    """Atomically persist ``new_settings`` to ``config.toml`` and update the singleton."""
    global _instance
    config_path = paths.config_file()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(_to_toml(new_settings), encoding="utf-8")
    tmp.replace(config_path)

    with _lock:
        _instance = new_settings


def _to_toml(s: Settings) -> str:
    return f"data_dir = {_toml_string(str(s.data_dir))}\n"


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
