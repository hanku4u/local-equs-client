"""Tiny sparkline widget for overview mode (C4.7).

~200×60 px button-ish widget showing the trend of one (tool, sensor) result
with no axes, no legend, just the line and a one-line caption with the
canonical name and the latest value. Clicking emits :attr:`clicked` so the
chart grid can promote that pair to focus mode.

Updates flow through :meth:`set_data` so the same widget instance survives
across re-queries and just refreshes its line/value.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

_DEFAULT_SIZE = QSize(220, 70)
_LINE_COLOR = (140, 180, 240)
_BAND_COLOR = (140, 180, 240, 50)
_BG_COLOR = "#202020"


class Sparkline(QFrame):
    """Compact button-ish chart for one ``(tool_id, canonical)`` pair."""

    clicked = Signal(str, str)  # tool_id, sensor

    def __init__(
        self,
        tool_id: str,
        sensor: str,
        *,
        units: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tool_id = tool_id
        self._sensor = sensor
        self._units = units

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(_DEFAULT_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        self._caption = QLabel(f"{tool_id} · {sensor}")
        self._caption.setStyleSheet("font-size: 10px; color: rgb(200, 200, 200);")
        layout.addWidget(self._caption)

        self._plot = pg.PlotWidget(background=_BG_COLOR)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")
        self._plot.setMenuEnabled(False)
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.disableAutoRange()
        layout.addWidget(self._plot, stretch=1)

        self._curve = pg.PlotDataItem(pen=pg.mkPen(_LINE_COLOR, width=1.5))
        self._plot.addItem(self._curve)

        self._value_label = QLabel("—")
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._value_label.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: rgb(200, 220, 240);"
        )
        layout.addWidget(self._value_label)

    # --- API -------------------------------------------------------------

    @property
    def tool_id(self) -> str:
        return self._tool_id

    @property
    def sensor(self) -> str:
        return self._sensor

    def set_data(self, x: np.ndarray, y: np.ndarray) -> None:
        if x.size == 0 or y.size == 0:
            self._curve.setData([], [])
            self._value_label.setText("(no data)")
            return
        finite = np.isfinite(y) if y.dtype.kind == "f" else None
        if finite is not None and not finite.any():
            self._curve.setData([], [])
            self._value_label.setText("(no data)")
            return

        self._curve.setData(x, y)
        # Tighten Y range to data extent so the line fills the frame.
        finite_y = y[finite] if finite is not None else y
        ymin = float(finite_y.min())
        ymax = float(finite_y.max())
        if math.isclose(ymin, ymax):
            pad = max(abs(ymin), 1.0) * 0.05
            ymin -= pad
            ymax += pad
        self._plot.setYRange(ymin, ymax, padding=0)
        self._plot.setXRange(float(x[0]), float(x[-1]), padding=0)

        latest = float(finite_y[-1])
        units = f" {self._units}" if self._units else ""
        self._value_label.setText(f"{latest:.3g}{units}")

    def show_message(self, text: str) -> None:
        """Display an info/error message instead of a curve."""
        self._curve.setData([], [])
        self._value_label.setText(text)

    # --- Click → promote -------------------------------------------------

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._tool_id, self._sensor)
        super().mousePressEvent(event)


__all__ = ["Sparkline"]
