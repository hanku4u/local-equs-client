"""Overview / Standard / Focus mode toggle and group-by controls (C4.9).

Sits above the chart grid as a horizontal toolbar with two radio groups:

- View mode: Overview · Standard · Focus
- Group by: Sensor · Tool · Sensor × Tool

Selecting a radio writes to the :class:`ViewController`; the controller fans
the change out to the :class:`QueryController` (re-query) and the
:class:`ChartGrid` (re-render). The bar listens to ``modeChanged`` /
``groupByChanged`` so external changes (e.g. a sparkline click promoting to
focus mode) keep the radios in sync.

The group-by toggle is functional through to the view controller; the chart
grid actually consuming it lands in C5/M4-extras.
"""

from __future__ import annotations

from typing import cast

from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QWidget,
)

from local_equs_client.selection.types import GroupBy, ViewMode
from local_equs_client.selection.view_controller import ViewController

_MODE_LABELS: list[tuple[ViewMode, str]] = [
    ("overview", "Overview"),
    ("standard", "Standard"),
    ("focus", "Focus"),
]
_GROUP_LABELS: list[tuple[GroupBy, str]] = [
    ("sensor", "Sensor"),
    ("tool", "Tool"),
    ("both", "Sensor × Tool"),
]


class ViewModeBar(QWidget):
    """Mode + group-by radios bound to a :class:`ViewController`."""

    def __init__(
        self,
        view_controller: ViewController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._vc = view_controller
        self._suppress_push = False
        self._mode_buttons: dict[ViewMode, QRadioButton] = {}
        self._group_buttons: dict[GroupBy, QRadioButton] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        layout.addWidget(QLabel("View:"))
        self._mode_group = QButtonGroup(self)
        for mode_value, label in _MODE_LABELS:
            btn = QRadioButton(label)
            btn.setChecked(mode_value == self._vc.mode)
            btn.toggled.connect(self._make_mode_handler(mode_value))
            self._mode_group.addButton(btn)
            self._mode_buttons[mode_value] = btn
            layout.addWidget(btn)

        layout.addSpacing(24)
        layout.addWidget(QLabel("Group by:"))
        self._group_group = QButtonGroup(self)
        for group_value, label in _GROUP_LABELS:
            btn = QRadioButton(label)
            btn.setChecked(group_value == self._vc.group_by)
            btn.toggled.connect(self._make_group_handler(group_value))
            self._group_group.addButton(btn)
            self._group_buttons[group_value] = btn
            layout.addWidget(btn)

        layout.addStretch()

        self._vc.modeChanged.connect(self._sync_mode)
        self._vc.groupByChanged.connect(self._sync_group_by)

    # --- Handlers / closures --------------------------------------------

    def _make_mode_handler(self, mode: ViewMode):  # type: ignore[no-untyped-def]
        def handler(checked: bool) -> None:
            if not checked or self._suppress_push:
                return
            self._vc.set_mode(mode)

        return handler

    def _make_group_handler(self, group_by: GroupBy):  # type: ignore[no-untyped-def]
        def handler(checked: bool) -> None:
            if not checked or self._suppress_push:
                return
            self._vc.set_group_by(group_by)

        return handler

    # --- Inbound sync ---------------------------------------------------

    def _sync_mode(self, mode: str) -> None:
        btn = self._mode_buttons.get(cast(ViewMode, mode))
        if btn is None or btn.isChecked():
            return
        self._suppress_push = True
        try:
            btn.setChecked(True)
        finally:
            self._suppress_push = False

    def _sync_group_by(self, group_by: str) -> None:
        btn = self._group_buttons.get(cast(GroupBy, group_by))
        if btn is None or btn.isChecked():
            return
        self._suppress_push = True
        try:
            btn.setChecked(True)
        finally:
            self._suppress_push = False


__all__ = ["ViewModeBar"]
