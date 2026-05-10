"""Unit tests for ``local_equs_client.config.paths`` (C0.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_equs_client.config import paths


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (paths.APP_DIR_ENV_VAR, "LOCALAPPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)


def test_app_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    assert paths.app_dir() == tmp_path


def test_app_dir_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.app_dir() == tmp_path / "LocalEQUS"


def test_app_dir_darwin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    expected = tmp_path / "Library" / "Application Support" / "LocalEQUS"
    assert paths.app_dir() == expected


def test_app_dir_linux_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.app_dir() == tmp_path / "LocalEQUS"


def test_app_dir_linux_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.app_dir() == tmp_path / ".local" / "share" / "LocalEQUS"


def test_subpaths_derive_from_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    assert paths.data_dir() == tmp_path / "data"
    assert paths.state_db() == tmp_path / "state.db"
    assert paths.logs_dir() == tmp_path / "logs"
    assert paths.config_file() == tmp_path / "config.toml"
