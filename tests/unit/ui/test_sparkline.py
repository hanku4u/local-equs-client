"""Unit tests for ``local_equs_client.ui.sparkline`` (C4.7)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from local_equs_client.ui.sparkline import Sparkline  # noqa: E402


def test_initial_caption_carries_tool_and_sensor(qapp) -> None:
    sp = Sparkline("etch_a1", "chamber_pressure", units="torr")
    assert "etch_a1" in sp._caption.text()  # noqa: SLF001
    assert "chamber_pressure" in sp._caption.text()  # noqa: SLF001


def test_set_data_populates_curve_and_latest_value(qapp) -> None:
    sp = Sparkline("etch_a1", "chamber_pressure", units="torr")
    x = np.arange(5, dtype=float)
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.5])
    sp.set_data(x, y)
    assert "5.5" in sp._value_label.text()  # noqa: SLF001
    assert "torr" in sp._value_label.text()  # noqa: SLF001


def test_empty_data_shows_no_data_message(qapp) -> None:
    sp = Sparkline("etch_a1", "chamber_pressure")
    sp.set_data(np.array([]), np.array([]))
    assert "no data" in sp._value_label.text().lower()  # noqa: SLF001


def test_all_nan_data_shows_no_data_message(qapp) -> None:
    sp = Sparkline("etch_a1", "chamber_pressure")
    sp.set_data(np.arange(3, dtype=float), np.array([float("nan")] * 3))
    assert "no data" in sp._value_label.text().lower()  # noqa: SLF001


def test_click_emits_promote_signal(qapp) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    sp = Sparkline("etch_a1", "chamber_pressure")
    captured: list[tuple[str, str]] = []
    sp.clicked.connect(lambda t, s: captured.append((t, s)), Qt.DirectConnection)

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPoint(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    sp.mousePressEvent(event)

    assert captured == [("etch_a1", "chamber_pressure")]
