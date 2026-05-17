"""Unit tests for ``local_equs_client.data_layer.permissions`` (C5.7)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from local_equs_client.config import paths, settings
from local_equs_client.data_layer import permissions


@pytest.fixture(autouse=True)
def _isolated_app_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.APP_DIR_ENV_VAR, str(tmp_path))
    settings.reset_settings()
    permissions.set_admin_check(None)
    yield tmp_path
    permissions.set_admin_check(None)
    settings.reset_settings()


def test_is_admin_defaults_to_false() -> None:
    assert permissions.is_admin() is False


def test_is_admin_reads_simulate_flag_when_set(_isolated_app_dir: Path) -> None:
    settings.save(
        replace(settings.get_settings(), permissions_simulate_admin=True)
    )
    assert permissions.is_admin() is True


def test_is_admin_returns_false_when_simulate_flag_false(_isolated_app_dir: Path) -> None:
    settings.save(
        replace(settings.get_settings(), permissions_simulate_admin=False)
    )
    assert permissions.is_admin() is False


def test_registered_check_overrides_simulate_flag(_isolated_app_dir: Path) -> None:
    settings.save(
        replace(settings.get_settings(), permissions_simulate_admin=True)
    )
    permissions.set_admin_check(lambda: False)
    assert permissions.is_admin() is False

    permissions.set_admin_check(lambda: True)
    assert permissions.is_admin() is True


def test_set_admin_check_none_restores_fallback(_isolated_app_dir: Path) -> None:
    settings.save(
        replace(settings.get_settings(), permissions_simulate_admin=True)
    )
    permissions.set_admin_check(lambda: False)
    assert permissions.is_admin() is False

    permissions.set_admin_check(None)
    assert permissions.is_admin() is True
