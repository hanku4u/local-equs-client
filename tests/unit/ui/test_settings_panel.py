"""Unit tests for ``local_equs_client.ui.settings_panel`` (C5.10)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from local_equs_client.config import paths, settings  # noqa: E402
from local_equs_client.ui.settings_panel import SettingsPanel  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    settings.reset_settings()
    yield tmp_path
    settings.reset_settings()


def test_telemetry_checkbox_checked_by_default(qapp, _isolated_app_dir: Path) -> None:
    panel = SettingsPanel()
    assert panel._telemetry_check.isChecked() is True  # noqa: SLF001


def test_telemetry_checkbox_unchecked_when_opted_out(qapp, _isolated_app_dir: Path) -> None:
    settings.save(replace(settings.get_settings(), telemetry_opt_out=True))
    panel = SettingsPanel()
    assert panel._telemetry_check.isChecked() is False  # noqa: SLF001


def test_unchecking_telemetry_persists_opt_out(qapp, _isolated_app_dir: Path) -> None:
    panel = SettingsPanel()
    panel._telemetry_check.setChecked(False)  # noqa: SLF001
    panel._on_save()  # noqa: SLF001
    assert settings.get_settings().telemetry_opt_out is True


def test_update_freq_dropdown_default_is_daily(qapp, _isolated_app_dir: Path) -> None:
    panel = SettingsPanel()
    assert panel._update_freq_combo.currentText() == "Daily"  # noqa: SLF001
    assert panel._update_freq_combo.currentData() == 24  # noqa: SLF001


def test_update_freq_dropdown_reflects_saved_value(qapp, _isolated_app_dir: Path) -> None:
    settings.save(replace(settings.get_settings(), update_check_frequency_hours=168))
    panel = SettingsPanel()
    assert panel._update_freq_combo.currentText() == "Weekly"  # noqa: SLF001


def test_update_freq_dropdown_falls_back_to_daily_on_unknown_value(
    qapp, _isolated_app_dir: Path
) -> None:
    settings.save(replace(settings.get_settings(), update_check_frequency_hours=72))
    panel = SettingsPanel()
    assert panel._update_freq_combo.currentText() == "Daily"  # noqa: SLF001


def test_selecting_weekly_persists_168_hours(qapp, _isolated_app_dir: Path) -> None:
    panel = SettingsPanel()
    weekly_index = panel._update_freq_combo.findText("Weekly")  # noqa: SLF001
    panel._update_freq_combo.setCurrentIndex(weekly_index)  # noqa: SLF001
    panel._on_save()  # noqa: SLF001
    assert settings.get_settings().update_check_frequency_hours == 168


def test_selecting_never_persists_zero(qapp, _isolated_app_dir: Path) -> None:
    panel = SettingsPanel()
    never_index = panel._update_freq_combo.findText("Never")  # noqa: SLF001
    panel._update_freq_combo.setCurrentIndex(never_index)  # noqa: SLF001
    panel._on_save()  # noqa: SLF001
    assert settings.get_settings().update_check_frequency_hours == 0


def test_save_preserves_existing_fields(qapp, _isolated_app_dir: Path) -> None:
    settings.save(
        replace(
            settings.get_settings(),
            server_url="https://equs.example.com",
            permissions_simulate_admin=True,
        )
    )
    panel = SettingsPanel()
    panel._on_save()  # noqa: SLF001
    saved = settings.get_settings()
    assert saved.server_url == "https://equs.example.com"
    assert saved.permissions_simulate_admin is True
