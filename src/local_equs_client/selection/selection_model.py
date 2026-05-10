"""Shared QObject Selection model: tools, sensors, time range (C0.6, C1.6).

C0.6 freezes the public contract; C1.6 fills in the implementation.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from local_equs_client.selection.types import Selection, TimeRange


class SelectionModel(QObject):
    """Process-wide observable holding the current selection.

    Subscribers connect to :attr:`selectionChanged` and read the current state
    via the typed properties. Mutations go through the ``set_*`` / ``clear``
    methods, which are the only legal way to update the model.
    """

    selectionChanged = Signal()

    def __init__(self) -> None:
        super().__init__()

    @property
    def tools(self) -> tuple[str, ...]:
        """Tool ids currently selected."""
        raise NotImplementedError

    @property
    def sensors_canonical(self) -> tuple[str, ...]:
        """Canonical sensor names selected (post-M3); empty in M1/M2."""
        raise NotImplementedError

    @property
    def sensors_raw(self) -> tuple[str, ...]:
        """Raw per-tool sensor names selected (M1 path)."""
        raise NotImplementedError

    @property
    def time_range(self) -> TimeRange:
        """Currently selected time range."""
        raise NotImplementedError

    def set_tools(self, tools: tuple[str, ...]) -> None:
        """Replace the selected tool ids; emits :attr:`selectionChanged` if changed."""
        raise NotImplementedError

    def set_sensors_canonical(self, sensors: tuple[str, ...]) -> None:
        """Replace the canonical sensor selection; emits :attr:`selectionChanged` if changed."""
        raise NotImplementedError

    def set_sensors_raw(self, sensors: tuple[str, ...]) -> None:
        """Replace the raw sensor selection; emits :attr:`selectionChanged` if changed."""
        raise NotImplementedError

    def set_time_range(self, time_range: TimeRange) -> None:
        """Replace the time range; emits :attr:`selectionChanged` if changed."""
        raise NotImplementedError

    def clear(self) -> None:
        """Reset all fields to empty/defaults; emits :attr:`selectionChanged` if non-empty."""
        raise NotImplementedError

    def snapshot(self) -> Selection:
        """Return a frozen copy of the current state for handing to the Query Planner."""
        raise NotImplementedError
