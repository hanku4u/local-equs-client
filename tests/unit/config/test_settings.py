"""Unit tests for ``local_equs_client.config.settings`` (C0.3)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from local_equs_client.config import paths, settings


@pytest.fixture(autouse=True)
def _isolated_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    settings.reset_settings()
    yield tmp_path
    settings.reset_settings()


def _toml_string(value: str) -> str:
    """Return a TOML-safe quoted string for paths, including Windows separators."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def test_defaults_when_no_config_file(_isolated_app_dir: Path) -> None:
    s = settings.get_settings()
    assert s.data_dir == _isolated_app_dir / "data"


def test_reads_data_dir_from_config(_isolated_app_dir: Path) -> None:
    custom = _isolated_app_dir / "elsewhere"
    paths.config_file().write_text(
        f"data_dir = {_toml_string(str(custom))}\n", encoding="utf-8"
    )

    s = settings.get_settings()
    assert s.data_dir == custom


def test_singleton_returns_same_instance(_isolated_app_dir: Path) -> None:
    assert settings.get_settings() is settings.get_settings()


def test_reset_reloads_from_file(_isolated_app_dir: Path) -> None:
    first = settings.get_settings()
    custom = _isolated_app_dir / "after_reset"
    paths.config_file().write_text(
        f"data_dir = {_toml_string(str(custom))}\n", encoding="utf-8"
    )

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
    monkeypatch.setenv("USERPROFILE", str(home))
    paths.config_file().write_text('data_dir = "~/equs-data"\n', encoding="utf-8")

    s = settings.get_settings()
    assert s.data_dir == home / "equs-data"


def test_save_writes_config_and_updates_singleton(_isolated_app_dir: Path) -> None:
    custom = _isolated_app_dir / "saved-data"
    settings.save(settings.Settings(data_dir=custom))

    config = tomllib.loads(paths.config_file().read_text(encoding="utf-8"))
    assert config["data_dir"] == str(custom)
    assert settings.get_settings().data_dir == custom


def test_save_round_trips_through_get_settings(_isolated_app_dir: Path) -> None:
    custom = _isolated_app_dir / "round-trip"
    settings.save(settings.Settings(data_dir=custom))
    settings.reset_settings()

    assert settings.get_settings().data_dir == custom


def test_save_escapes_backslashes_in_path(_isolated_app_dir: Path) -> None:
    weird = Path("C:\\Users\\equs\\data")
    settings.save(settings.Settings(data_dir=weird))
    settings.reset_settings()

    assert settings.get_settings().data_dir == weird


def test_server_url_default_is_none(_isolated_app_dir: Path) -> None:
    assert settings.get_settings().server_url is None


def test_server_url_round_trips_through_save(_isolated_app_dir: Path) -> None:
    settings.save(
        settings.Settings(
            data_dir=_isolated_app_dir / "data",
            server_url="https://equs.example.com",
        )
    )
    settings.reset_settings()
    s = settings.get_settings()
    assert s.server_url == "https://equs.example.com"


def test_empty_server_url_in_config_treated_as_none(_isolated_app_dir: Path) -> None:
    paths.config_file().write_text(
        f"data_dir = {_toml_string(str(_isolated_app_dir / 'data'))}\nserver_url = \"\"\n",
        encoding="utf-8",
    )
    s = settings.get_settings()
    assert s.server_url is None


def test_omitting_server_url_means_not_persisted(_isolated_app_dir: Path) -> None:
    settings.save(settings.Settings(data_dir=_isolated_app_dir / "data", server_url=None))
    config = tomllib.loads(paths.config_file().read_text(encoding="utf-8"))
    assert "server_url" not in config


def test_telemetry_opt_out_default_is_false(_isolated_app_dir: Path) -> None:
    assert settings.get_settings().telemetry_opt_out is False


def test_telemetry_opt_out_round_trips_through_save(_isolated_app_dir: Path) -> None:
    settings.save(
        settings.Settings(
            data_dir=_isolated_app_dir / "data",
            telemetry_opt_out=True,
        )
    )
    settings.reset_settings()
    assert settings.get_settings().telemetry_opt_out is True


def test_default_telemetry_not_persisted(_isolated_app_dir: Path) -> None:
    settings.save(settings.Settings(data_dir=_isolated_app_dir / "data"))
    config = tomllib.loads(paths.config_file().read_text(encoding="utf-8"))
    assert "telemetry_opt_out" not in config


def test_update_check_frequency_default_is_daily(_isolated_app_dir: Path) -> None:
    assert settings.get_settings().update_check_frequency_hours == 24


def test_update_check_frequency_round_trips_through_save(_isolated_app_dir: Path) -> None:
    settings.save(
        settings.Settings(
            data_dir=_isolated_app_dir / "data",
            update_check_frequency_hours=168,
        )
    )
    settings.reset_settings()
    assert settings.get_settings().update_check_frequency_hours == 168


def test_default_update_check_frequency_not_persisted(_isolated_app_dir: Path) -> None:
    settings.save(settings.Settings(data_dir=_isolated_app_dir / "data"))
    config = tomllib.loads(paths.config_file().read_text(encoding="utf-8"))
    assert "update_check_frequency_hours" not in config


def test_update_check_frequency_never_round_trips(_isolated_app_dir: Path) -> None:
    settings.save(
        settings.Settings(
            data_dir=_isolated_app_dir / "data",
            update_check_frequency_hours=0,
        )
    )
    settings.reset_settings()
    assert settings.get_settings().update_check_frequency_hours == 0
