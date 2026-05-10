"""Date pickers + draggable thumbnail strip for the time range (C1.5).

Two ``QDateTimeEdit`` widgets show the current start/end. A pyqtgraph
``LinearRegionItem`` on a thumbnail plot below shows the local data extent
and lets the user drag a region to set the range. Both inputs push to
``SelectionModel.time_range`` debounced 200ms; pulls from the model are
applied immediately when ``selectionChanged`` fires elsewhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pyqtgraph as pg
from PySide6.QtCore import QDate, QDateTime, QTime, QTimer, QTimeZone, Signal
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.selection.types import TimeRange

_DEBOUNCE_MS = 200
_DISPLAY_FORMAT = "yyyy-MM-dd HH:mm:ss"


def _utc_zone() -> QTimeZone:
    """Build a fresh UTC zone every time. Cheap, dodges any module-level init order."""
    return QTimeZone(QTimeZone.Initialization.UTC)


class TimeRangeSelector(QWidget):
    """Start/end pickers + thumbnail region for the current selection's time range."""

    rangeChanged = Signal(object)  # emits the new TimeRange after debounce

    def __init__(
        self,
        selection_model: SelectionModel,
        library: LocalLibrary,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = selection_model
        self._library = library
        self._suppress_push = False

        self._build_ui()
        self._wire_inputs()

        self._model.selectionChanged.connect(self._sync_from_model)
        self._sync_from_model()
        self._refresh_extent()

    # --- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        dt_row = QHBoxLayout()
        self._start_edit = self._make_dt_edit()
        self._end_edit = self._make_dt_edit()
        dt_row.addWidget(QLabel("Start:"))
        dt_row.addWidget(self._start_edit)
        dt_row.addSpacing(12)
        dt_row.addWidget(QLabel("End:"))
        dt_row.addWidget(self._end_edit)
        dt_row.addStretch()
        layout.addLayout(dt_row)

        self._plot = pg.PlotWidget()
        self._plot.setMaximumHeight(60)
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.setMenuEnabled(False)
        self._plot.hideAxis("left")
        self._plot.getAxis("bottom").setStyle(showValues=True)
        self._plot.setAxisItems({"bottom": pg.DateAxisItem(orientation="bottom")})
        self._extent_hint = pg.TextItem("", anchor=(0, 1), color=(140, 140, 140))
        self._plot.addItem(self._extent_hint)

        self._region = pg.LinearRegionItem(values=(0, 0), brush=(80, 120, 200, 60))
        self._region.setZValue(10)
        self._plot.addItem(self._region)

        layout.addWidget(self._plot)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._push_to_model)

    def _make_dt_edit(self) -> QDateTimeEdit:
        edit = QDateTimeEdit()
        edit.setDisplayFormat(_DISPLAY_FORMAT)
        edit.setCalendarPopup(True)
        edit.setTimeZone(_utc_zone())
        return edit

    def _wire_inputs(self) -> None:
        self._start_edit.dateTimeChanged.connect(self._on_input_changed)
        self._end_edit.dateTimeChanged.connect(self._on_input_changed)
        self._region.sigRegionChanged.connect(self._on_region_changed)

    # --- Library extent ---------------------------------------------------

    def refresh_extent(self) -> None:
        """Public hook: callers re-call after a Local Library scan."""
        self._refresh_extent()

    def _refresh_extent(self) -> None:
        files = self._library.all_files()
        self._suppress_push = True
        try:
            if not files:
                self._extent_hint.setText("No local data — pick any range; query will warn.")
                self._extent_hint.setPos(0, 0)
                # No bounds: leave the region at whatever the model says.
                return

            ext_min = min(f.min_ts for f in files).timestamp()
            ext_max = max(f.max_ts for f in files).timestamp()
            self._region.setBounds((ext_min, ext_max))
            self._plot.setXRange(ext_min, ext_max, padding=0.02)
            self._extent_hint.setText("")
            # setBounds may have clamped the region; re-apply the model's range.
            current = self._model.time_range
            self._region.setRegion((current.start.timestamp(), current.end.timestamp()))
        finally:
            self._suppress_push = False

    # --- Push / pull ------------------------------------------------------

    def _on_input_changed(self) -> None:
        if self._suppress_push:
            return
        self._suppress_push = True
        try:
            self._sync_region_from_inputs()
        finally:
            self._suppress_push = False
        self._debounce.start()

    def _on_region_changed(self) -> None:
        if self._suppress_push:
            return
        ts_start, ts_end = self._region.getRegion()
        self._suppress_push = True
        try:
            self._start_edit.setDateTime(_dt_from_epoch(ts_start))
            self._end_edit.setDateTime(_dt_from_epoch(ts_end))
        finally:
            self._suppress_push = False
        self._debounce.start()

    def _push_to_model(self) -> None:
        new_range = TimeRange(
            start=_dt_from_qdt(self._start_edit.dateTime()),
            end=_dt_from_qdt(self._end_edit.dateTime()),
        )
        if new_range.end <= new_range.start:
            return
        self._model.set_time_range(new_range)
        self.rangeChanged.emit(new_range)

    def _sync_from_model(self) -> None:
        current = self._model.time_range
        self._suppress_push = True
        try:
            self._start_edit.setDateTime(_qdt_from_dt(current.start))
            self._end_edit.setDateTime(_qdt_from_dt(current.end))
            self._region.setRegion((current.start.timestamp(), current.end.timestamp()))
        finally:
            self._suppress_push = False

    def _sync_region_from_inputs(self) -> None:
        start_ts = float(self._start_edit.dateTime().toSecsSinceEpoch())
        end_ts = float(self._end_edit.dateTime().toSecsSinceEpoch())
        self._region.setRegion((start_ts, end_ts))


def _qdt_from_dt(value: datetime) -> QDateTime:
    """Build a UTC QDateTime explicitly from a Python datetime (millisecond precision)."""
    utc = value.astimezone(UTC)
    return QDateTime(
        QDate(utc.year, utc.month, utc.day),
        QTime(utc.hour, utc.minute, utc.second, utc.microsecond // 1000),
        _utc_zone(),
    )


def _dt_from_qdt(value: QDateTime) -> datetime:
    return datetime.fromtimestamp(value.toSecsSinceEpoch(), tz=UTC)


def _dt_from_epoch(value: float) -> QDateTime:
    """Build a UTC QDateTime from a unix epoch second."""
    return _qdt_from_dt(datetime.fromtimestamp(int(value), tz=UTC))


__all__ = ["TimeRangeSelector"]
