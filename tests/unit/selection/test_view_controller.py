"""Unit tests for ``local_equs_client.selection.view_controller`` (C4.1)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from local_equs_client.selection.view_controller import ViewController


def test_default_mode_is_standard() -> None:
    vc = ViewController()
    assert vc.mode == "standard"
    assert vc.group_by == "sensor"


def test_set_mode_emits_signal() -> None:
    vc = ViewController()
    received: list[str] = []
    vc.modeChanged.connect(received.append, Qt.DirectConnection)

    vc.set_mode("overview")

    assert vc.mode == "overview"
    assert received == ["overview"]


def test_set_mode_with_same_value_does_not_emit() -> None:
    vc = ViewController(mode="focus")
    received: list[str] = []
    vc.modeChanged.connect(received.append, Qt.DirectConnection)

    vc.set_mode("focus")

    assert received == []


def test_set_group_by_emits_signal() -> None:
    vc = ViewController()
    received: list[str] = []
    vc.groupByChanged.connect(received.append, Qt.DirectConnection)

    vc.set_group_by("tool")

    assert vc.group_by == "tool"
    assert received == ["tool"]


def test_set_group_by_with_same_value_does_not_emit() -> None:
    vc = ViewController(group_by="both")
    received: list[str] = []
    vc.groupByChanged.connect(received.append, Qt.DirectConnection)

    vc.set_group_by("both")

    assert received == []
