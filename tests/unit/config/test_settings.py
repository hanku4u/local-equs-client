"""Unit tests for ``local_equs_client.config.settings`` (C0.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_equs_client.config import paths, settings


@pytest.fixture(autouse=True)
def _isolated_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    settings.reset_settings()
    yield tmp_path
    settings.reset_settings()


def test_defaults_when_no_config_file(_isolated_app_dir: Path) -> None:
    s = settings.get_settings()
    assert s.data_dir == _isolated_app_dir / "data"


def test_reads_data_dir_from_config(_isolated_app_dir: Path) -> None:
    custom = _isolated_app_dir / "elsewhere"
    paths.config_file().write_text(f'data_dir = "{custom}"\n', encoding="utf-8")

    s = settings.get_settings()
    assert s.data_dir == custom


def test_singleton_returns_same_instance(_isolated_app_dir: Path) -> None:
    assert settings.get_settings() is settings.get_settings()


def test_reset_reloads_from_file(_isolated_app_dir: Path) -> None:
    first = settings.get_settings()
    custom = _isolated_app_dir / "after_reset"
    paths.config_file().write_text(f'data_dir = "{custom}"\n', encoding="utf-8")

    settings.reset_settings()
    second = settings.get_settings()

    assert second is not first
    assert second.data_dir == custom


def test_partial_config_falls_back_to_defaults(_isolated_app_dir: Path) -> None:
    paths.config_file().write_text("# empty config\n", encoding="utf-8")
    s = settings.get_settings()
    assert s.data_dir == _isolated_app_dir / "data"


def test_expands_user_home_in_data_dir(
    _isolated_app_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    paths.config_file().write_text('data_dir = "~/equs-data"\n', encoding="utf-8")

    s = settings.get_settings()
    assert s.data_dir == home / "equs-data"
