"""Process-wide ``Settings`` singleton backed by ``config.toml`` (C0.3, C2.1, C5.10).

C0.3 lit up the data dir; C2.1 (this revision) adds ``server_url``. Telemetry
opt-out, update-check frequency, and the permissions-simulate-admin flag join
later.
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
    server_url: str | None = None

    @classmethod
    def defaults(cls) -> Settings:
        return cls(data_dir=paths.data_dir(), server_url=None)

    @classmethod
    def from_file(cls, path: Path) -> Settings:
        """Load settings from ``config.toml``, filling missing fields with defaults."""
        defaults = cls.defaults()
        if not path.is_file():
            return defaults

        with path.open("rb") as fh:
            raw = tomllib.load(fh)

        data_dir_raw = raw.get("data_dir")
        server_url_raw = raw.get("server_url")
        return replace(
            defaults,
            data_dir=Path(data_dir_raw).expanduser() if data_dir_raw else defaults.data_dir,
            server_url=server_url_raw or None,
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
    lines = [f"data_dir = {_toml_string(str(s.data_dir))}"]
    if s.server_url:
        lines.append(f"server_url = {_toml_string(s.server_url)}")
    return "\n".join(lines) + "\n"


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
