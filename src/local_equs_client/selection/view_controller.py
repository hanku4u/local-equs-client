"""Routes selection + ViewMode to the chart grid and query pipeline (C4.1).

The View Controller is a small ``QObject`` holding the current ``ViewMode``
(``overview`` | ``standard`` | ``focus``) and ``GroupBy``
(``sensor`` | ``tool`` | ``both``). The :class:`QueryController` watches
``modeChanged`` and ``groupByChanged`` so a view-mode flip drives a new
query plan; C4.7-C4.9 add the matching UI affordances.

C4.1 doesn't change what queries run — it just hoists the mode state out of
``QueryController``'s constructor default into a shared, observable model so
the picker, the view-mode bar, and any future panel agree on what mode the
app is in.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from local_equs_client.selection.types import GroupBy, ViewMode


class ViewController(QObject):
    """Shared view-mode state for the chart grid and query pipeline."""

    modeChanged = Signal(str)  # ViewMode
    groupByChanged = Signal(str)  # GroupBy

    def __init__(
        self,
        mode: ViewMode = "standard",
        group_by: GroupBy = "sensor",
    ) -> None:
        super().__init__()
        self._mode: ViewMode = mode
        self._group_by: GroupBy = group_by

    @property
    def mode(self) -> ViewMode:
        return self._mode

    def set_mode(self, mode: ViewMode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self.modeChanged.emit(mode)

    @property
    def group_by(self) -> GroupBy:
        return self._group_by

    def set_group_by(self, group_by: GroupBy) -> None:
        if group_by == self._group_by:
            return
        self._group_by = group_by
        self.groupByChanged.emit(group_by)


__all__ = ["ViewController"]
