"""Unit tests for ``local_equs_client.ui.view_mode_bar`` (C4.9)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from local_equs_client.selection.view_controller import ViewController  # noqa: E402
from local_equs_client.ui.view_mode_bar import ViewModeBar  # noqa: E402


def test_radios_reflect_initial_controller_state(qapp) -> None:
    vc = ViewController(mode="overview", group_by="tool")
    bar = ViewModeBar(vc)
    assert bar._mode_buttons["overview"].isChecked()  # noqa: SLF001
    assert bar._group_buttons["tool"].isChecked()  # noqa: SLF001


def test_clicking_radio_pushes_to_controller(qapp) -> None:
    vc = ViewController()
    bar = ViewModeBar(vc)
    bar._mode_buttons["focus"].setChecked(True)  # noqa: SLF001
    assert vc.mode == "focus"


def test_external_controller_change_syncs_radios(qapp) -> None:
    vc = ViewController()
    bar = ViewModeBar(vc)
    vc.set_mode("focus")
    assert bar._mode_buttons["focus"].isChecked()  # noqa: SLF001
    assert not bar._mode_buttons["standard"].isChecked()  # noqa: SLF001


def test_group_by_radios_work_independently(qapp) -> None:
    vc = ViewController()
    bar = ViewModeBar(vc)
    bar._group_buttons["both"].setChecked(True)  # noqa: SLF001
    assert vc.group_by == "both"
    # Mode untouched.
    assert vc.mode == "standard"
