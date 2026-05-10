"""Shared QObject Selection model: tools, sensors, time range (C0.6, C1.6).

C0.6 froze the public contract; C1.6 implements it. The model is process-wide
and read-only outside of the ``set_*`` / ``clear`` methods. Reads are guarded
by an internal ``RLock`` so worker threads can take consistent snapshots while
the UI thread is mutating.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock

from PySide6.QtCore import QObject, Signal

from local_equs_client.selection.types import Selection, TimeRange

_DEFAULT_RANGE_HOURS = 24


def _default_time_range() -> TimeRange:
    """Initial / post-clear time range — the last 24 hours up to now (UTC)."""
    now = datetime.now(UTC)
    return TimeRange(start=now - timedelta(hours=_DEFAULT_RANGE_HOURS), end=now)


class SelectionModel(QObject):
    """Process-wide observable holding the current selection."""

    selectionChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._lock = RLock()
        self._tools: tuple[str, ...] = ()
        self._sensors_canonical: tuple[str, ...] = ()
        self._sensors_raw: tuple[str, ...] = ()
        self._time_range: TimeRange = _default_time_range()

    @property
    def tools(self) -> tuple[str, ...]:
        with self._lock:
            return self._tools

    @property
    def sensors_canonical(self) -> tuple[str, ...]:
        with self._lock:
            return self._sensors_canonical

    @property
    def sensors_raw(self) -> tuple[str, ...]:
        with self._lock:
            return self._sensors_raw

    @property
    def time_range(self) -> TimeRange:
        with self._lock:
            return self._time_range

    def set_tools(self, tools: tuple[str, ...]) -> None:
        new = tuple(tools)
        with self._lock:
            if new == self._tools:
                return
            self._tools = new
        self.selectionChanged.emit()

    def set_sensors_canonical(self, sensors: tuple[str, ...]) -> None:
        new = tuple(sensors)
        with self._lock:
            if new == self._sensors_canonical:
                return
            self._sensors_canonical = new
        self.selectionChanged.emit()

    def set_sensors_raw(self, sensors: tuple[str, ...]) -> None:
        new = tuple(sensors)
        with self._lock:
            if new == self._sensors_raw:
                return
            self._sensors_raw = new
        self.selectionChanged.emit()

    def set_time_range(self, time_range: TimeRange) -> None:
        with self._lock:
            if time_range == self._time_range:
                return
            self._time_range = time_range
        self.selectionChanged.emit()

    def clear(self) -> None:
        with self._lock:
            had_selection = bool(
                self._tools or self._sensors_canonical or self._sensors_raw
            )
            self._tools = ()
            self._sensors_canonical = ()
            self._sensors_raw = ()
            self._time_range = _default_time_range()
        if had_selection:
            self.selectionChanged.emit()

    def snapshot(self) -> Selection:
        with self._lock:
            return Selection(
                tools=self._tools,
                sensors_canonical=self._sensors_canonical,
                sensors_raw=self._sensors_raw,
                time_range=self._time_range,
            )
