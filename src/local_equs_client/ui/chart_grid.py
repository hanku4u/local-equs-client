"""PyQtGraph-based linked chart grid (C1.10–C1.13, C4.4, C4.6–C4.8, C4.10).

One ``pg.PlotWidget`` per ``(tool_id, raw_column)`` pair in standard / focus
mode, stacked vertically inside a ``QScrollArea`` so the grid overflows when
many plots are selected. Avg drawn as a solid line, min/max as a faint fill
band. All x-axes linked through the anchor plot and a synchronized vertical
crosshair tracks the mouse across every visible plot. Updates flow through
``setData()`` so the existing curves keep their identity.

C1.11: pan/zoom fires :attr:`rangeChangedByUser`.
C1.12: ``QueryError`` payloads render a red "Tool error" overlay.
C1.13: empty / all-NaN results render "No data in range".

C4.4: ``on_plan_ready(plan)`` lays out placeholder frames before any DuckDB
query runs; ``on_tool_complete(plan, tool_id, result)`` fills them as
results land. Progress label shows ``Loading X / Y tools…``.

C4.6: ``MAX_VISIBLE_PLOTS`` caps simultaneous standard-mode plots; the
banner above the grid steers excess into overview mode.

C4.7: a parallel :class:`Sparkline` grid renders in overview mode. Each
sparkline is a click-to-promote button that flips back to focus mode.

C4.8: focus mode caps at four enlarged charts; each plot title carries a
``min/mean/max`` statistics strip computed from the visible range.

C4.10: the cap banner carries a "Switch to Overview" button and escalates
to an "Are you sure?" message above ``OVERVIEW_ESCALATION_THRESHOLD``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pyarrow as pa
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.query_engine import QueryError, ToolResult
from local_equs_client.data_layer.query_planner import QueryPlan
from local_equs_client.selection.types import TimeRange, ViewMode
from local_equs_client.ui.sparkline import Sparkline

_BAND_COLOR = (80, 120, 200, 60)
_LINE_COLOR = (80, 120, 200)
_GRID_ALPHA = 0.3
_PLOT_HEIGHT_HINT = 200
_FOCUS_HEIGHT_HINT = 380
_NO_DATA_TEXT = "No data in range"
_LOADING_TEXT = "Loading…"
_ERROR_PREFIX = "Tool error: "
_BANNER_TEMPLATE = (
    "Showing {shown} of {total} charts. Overview mode renders the full grid."
)
_BANNER_ESCALATION_TEMPLATE = (
    "Are you sure? {total} charts may overwhelm the standard grid — "
    "only {shown} are drawn here."
)
_BANNER_WARNING_STYLE = (
    "background-color: rgb(60, 50, 20); color: rgb(255, 220, 140);"
    "padding: 6px 8px;"
)
_BANNER_ESCALATION_STYLE = (
    "background-color: rgb(80, 30, 25); color: rgb(255, 200, 190);"
    "padding: 6px 8px;"
)
MAX_VISIBLE_PLOTS = 50
OVERVIEW_ESCALATION_THRESHOLD = 200  # C4.10
FOCUS_MAX_PLOTS = 4  # C4.8 cap
_OVERVIEW_COLUMNS = 5

_STACK_GRAPHICS = 0
_STACK_OVERVIEW = 1


@dataclass(slots=True)
class _Plot:
    plot_widget: pg.PlotWidget
    plot_item: pg.PlotItem
    avg_curve: pg.PlotDataItem
    low_curve: pg.PlotDataItem
    high_curve: pg.PlotDataItem
    fill: pg.FillBetweenItem
    vline: pg.InfiniteLine
    overlay: pg.TextItem
    range_signal_connected: bool = field(default=False)


class ChartGrid(QWidget):
    """Linked grid of plots (standard / focus) + parallel sparkline grid (overview)."""

    rangeChangedByUser = Signal(object)  # emits a TimeRange
    visibleToolsChanged = Signal(object)  # C4.5: list[str] of tool ids currently visible
    promoteRequested = Signal(str, str)  # C4.7: (tool_id, sensor) clicked in overview
    switchToOverviewRequested = Signal()  # C4.10: banner "Switch to Overview" button

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._progress_label = QLabel("")
        self._progress_label.setVisible(False)
        self._progress_label.setStyleSheet(
            "color: rgb(180, 180, 180); padding: 4px 6px;"
        )
        layout.addWidget(self._progress_label)

        self._cap_banner = QFrame()
        self._cap_banner.setVisible(False)
        self._cap_banner.setStyleSheet(_BANNER_WARNING_STYLE)
        banner_layout = QHBoxLayout(self._cap_banner)
        banner_layout.setContentsMargins(0, 0, 0, 0)
        banner_layout.setSpacing(8)
        self._cap_banner_label = QLabel("")
        self._cap_banner_label.setWordWrap(True)
        banner_layout.addWidget(self._cap_banner_label, stretch=1)
        self._cap_banner_button = QPushButton("Switch to Overview")
        self._cap_banner_button.clicked.connect(self.switchToOverviewRequested.emit)
        banner_layout.addWidget(self._cap_banner_button)
        layout.addWidget(self._cap_banner)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, stretch=1)

        # Standard / focus mode: each plot is its own pg.PlotWidget stacked in
        # a QVBoxLayout inside a QScrollArea. This gives every plot its honest
        # minimum height and lets the QScrollArea overflow naturally when the
        # stack outgrows the viewport.
        self._graphics = QScrollArea()
        self._graphics.setWidgetResizable(True)
        self._graphics_inner = QWidget()
        self._graphics_layout = QVBoxLayout(self._graphics_inner)
        self._graphics_layout.setContentsMargins(0, 0, 0, 0)
        self._graphics_layout.setSpacing(4)
        self._graphics_layout.addStretch(1)  # keep plots top-aligned
        self._graphics.setWidget(self._graphics_inner)
        self._stack.addWidget(self._graphics)  # index 0
        # Plain wheel scrolls the chart stack; Ctrl+wheel falls through so
        # pyqtgraph's ViewBox can zoom the time axis. The filter is installed
        # on each PlotWidget's viewport in _create_plot — that is where Qt
        # delivers wheel events from a cursor over a plot.
        self._graphics_wheel_filter = _WheelToScrollFilter(
            self._graphics.verticalScrollBar(), parent=self
        )

        self._overview_container = QScrollArea()
        self._overview_container.setWidgetResizable(True)
        self._overview_inner = QWidget()
        self._overview_layout = QGridLayout(self._overview_inner)
        self._overview_layout.setContentsMargins(4, 4, 4, 4)
        self._overview_layout.setSpacing(6)
        self._overview_container.setWidget(self._overview_inner)
        self._stack.addWidget(self._overview_container)  # index 1
        self._overview_wheel_filter = _WheelToScrollFilter(
            self._overview_container.verticalScrollBar(), parent=self
        )
        self._overview_container.viewport().installEventFilter(self._overview_wheel_filter)

        self._mode: ViewMode = "standard"
        self._plots: dict[tuple[str, str], _Plot] = {}
        self._sparklines: dict[tuple[str, str], Sparkline] = {}
        self._anchor_plot: pg.PlotItem | None = None
        self._suppress_range_signal = False
        self._pending_tools: set[str] = set()
        self._planned_tools: set[str] = set()
        self._last_plan: QueryPlan | None = None
        self._last_results: dict[str, ToolResult] = {}

        self._graphics.verticalScrollBar().valueChanged.connect(self._emit_visible_tools)

    # --- Public API -------------------------------------------------------

    def set_mode(self, mode: ViewMode) -> None:
        """Switch between overview / standard / focus rendering."""
        if mode == self._mode:
            return
        self._mode = mode
        self._stack.setCurrentIndex(
            _STACK_OVERVIEW if mode == "overview" else _STACK_GRAPHICS
        )
        self._update_cap_banner(self._last_plan)
        if self._last_plan is not None:
            self._rerender_from_cache()

    def render_to_pixmap(self, *, scale: int = 3) -> QPixmap:
        """Render the full stacked plot list to a ``QPixmap`` at ``scale``× DPR.

        Captures the inner widget (every plot, including ones scrolled off
        screen) rather than the scroll viewport, so the output PNG covers
        the entire chart regardless of the current scroll position.
        """
        inner = (
            self._overview_inner
            if self._mode == "overview"
            else self._graphics_inner
        )
        inner.adjustSize()
        size = inner.size()
        if size.width() < 10 or size.height() < 10:
            # Inner widget hasn't been laid out (e.g., no plots yet); fall
            # back to the visible grid size so we still produce something
            # reasonable.
            size = self.size()
        if size.width() < 10 or size.height() < 10:
            size = inner.sizeHint()
        target = QSize(max(1, size.width() * scale), max(1, size.height() * scale))
        pixmap = QPixmap(target)
        pixmap.fill(Qt.GlobalColor.white)
        painter = QPainter(pixmap)
        try:
            painter.scale(scale, scale)
            inner.render(painter, QPoint())
        finally:
            painter.end()
        return pixmap

    def on_plan_ready(self, plan: QueryPlan) -> None:
        """C4.4: lay out placeholder frames the moment a plan is built."""
        self._last_plan = plan
        self._last_results = {}
        self._planned_tools = {q.tool_id for q in plan.per_tool_queries}
        self._pending_tools = set(self._planned_tools)
        self._update_cap_banner(plan)
        self._update_progress()
        if self._mode == "overview":
            self._layout_overview(plan, fill_loading=True)
        else:
            self._layout_graphics(plan, fill_loading=True)
        self._emit_visible_tools()

    def on_tool_complete(
        self, plan: QueryPlan, tool_id: str, result: ToolResult
    ) -> None:
        """C4.4: fill in the slot for one tool as its result arrives."""
        self._last_plan = plan
        self._last_results[tool_id] = result
        if self._mode == "overview":
            self._fill_overview_tool(plan, tool_id, result)
        else:
            self._fill_graphics_tool(plan, tool_id, result)
        self._pending_tools.discard(tool_id)
        self._update_progress()

    def update_from_results(
        self, plan: QueryPlan, results: dict[str, ToolResult]
    ) -> None:
        """Apply a completed query plan + per-tool results to the grid."""
        self._last_plan = plan
        self._last_results = dict(results)
        self._planned_tools = {q.tool_id for q in plan.per_tool_queries}
        self._pending_tools.clear()
        self._update_cap_banner(plan)
        self._update_progress()
        if self._mode == "overview":
            self._layout_overview(plan, fill_loading=False)
        else:
            self._layout_graphics(plan, fill_loading=False)

    def clear(self) -> None:
        self._clear_graphics()
        self._clear_overview()
        self._pending_tools.clear()
        self._planned_tools.clear()
        self._last_plan = None
        self._last_results = {}
        self._update_progress()
        self._update_cap_banner(None)

    def visible_tool_ids(self) -> list[str]:
        """Return the tool ids whose plots are in the current viewport."""
        if self._mode == "overview":
            return sorted({k[0] for k in self._sparklines})
        if not self._plots:
            return []
        viewport = self._graphics.viewport()
        viewport_rect = viewport.rect()
        ordered: list[str] = []
        seen: set[str] = set()
        for (tool_id, _col), plot in self._plots.items():
            widget = plot.plot_widget
            try:
                top_left = widget.mapTo(viewport, QPoint(0, 0))
            except RuntimeError:
                continue
            widget_rect = QRect(top_left, widget.size())
            if not widget_rect.intersects(viewport_rect):
                continue
            if tool_id in seen:
                continue
            seen.add(tool_id)
            ordered.append(tool_id)
        return ordered

    # --- Re-render after a mode switch -----------------------------------

    def _rerender_from_cache(self) -> None:
        if self._last_plan is None:
            return
        if self._mode == "overview":
            self._layout_overview(self._last_plan, fill_loading=False)
        else:
            self._layout_graphics(self._last_plan, fill_loading=False)

    # --- Layout helpers --------------------------------------------------

    def _wanted_keys(self, plan: QueryPlan) -> list[tuple[str, str]]:
        all_keys = [
            (q.tool_id, col) for q in plan.per_tool_queries for col in q.raw_columns
        ]
        if self._mode == "focus":
            return all_keys[:FOCUS_MAX_PLOTS]
        if self._mode == "overview":
            return all_keys
        if len(all_keys) <= MAX_VISIBLE_PLOTS:
            return all_keys
        return all_keys[:MAX_VISIBLE_PLOTS]

    def _update_progress(self) -> None:
        if not self._planned_tools or not self._pending_tools:
            self._progress_label.setVisible(False)
            return
        done = len(self._planned_tools) - len(self._pending_tools)
        total = len(self._planned_tools)
        self._progress_label.setText(f"Loading {done} / {total} tools…")
        self._progress_label.setVisible(True)

    def _update_cap_banner(self, plan: QueryPlan | None) -> None:
        if plan is None or self._mode == "overview":
            self._cap_banner.setVisible(False)
            return
        total = sum(len(q.raw_columns) for q in plan.per_tool_queries)
        cap_active = (
            FOCUS_MAX_PLOTS if self._mode == "focus" else MAX_VISIBLE_PLOTS
        )
        if total <= cap_active:
            self._cap_banner.setVisible(False)
            return
        if total > OVERVIEW_ESCALATION_THRESHOLD:
            self._cap_banner_label.setText(
                _BANNER_ESCALATION_TEMPLATE.format(shown=cap_active, total=total)
            )
            self._cap_banner.setStyleSheet(_BANNER_ESCALATION_STYLE)
        else:
            self._cap_banner_label.setText(
                _BANNER_TEMPLATE.format(shown=cap_active, total=total)
            )
            self._cap_banner.setStyleSheet(_BANNER_WARNING_STYLE)
        self._cap_banner.setVisible(True)

    def _emit_visible_tools(self) -> None:
        self.visibleToolsChanged.emit(self.visible_tool_ids())

    # --- Standard / focus graphics rendering -----------------------------

    def _layout_graphics(self, plan: QueryPlan, *, fill_loading: bool) -> None:
        wanted = self._wanted_keys(plan)
        wanted_set = set(wanted)
        self._clear_overview()

        self._suppress_range_signal = True
        try:
            for key in list(self._plots):
                if key not in wanted_set:
                    self._remove_plot(key)
            for key in wanted:
                tool_id, _col = key
                if fill_loading:
                    plot = self._upsert_plot(key, None)
                    self._show_loading(plot)
                else:
                    self._upsert_plot(key, self._last_results.get(tool_id))

            if plan.per_tool_queries and self._anchor_plot is not None:
                tr = plan.per_tool_queries[0].time_range
                self._anchor_plot.setXRange(
                    tr.start.timestamp(), tr.end.timestamp(), padding=0
                )
        finally:
            self._suppress_range_signal = False

    def _fill_graphics_tool(
        self, plan: QueryPlan, tool_id: str, result: ToolResult
    ) -> None:
        for q in plan.per_tool_queries:
            if q.tool_id != tool_id:
                continue
            for col in q.raw_columns:
                key = (tool_id, col)
                if key in self._plots:
                    self._apply_data(self._plots[key], result, key)

    def _clear_graphics(self) -> None:
        for key in list(self._plots):
            self._remove_plot(key)

    def _upsert_plot(
        self, key: tuple[str, str], result: ToolResult | None
    ) -> _Plot:
        plot = self._plots.get(key) or self._create_plot(key)
        self._plots[key] = plot
        self._apply_data(plot, result, key)
        return plot

    def _create_plot(self, key: tuple[str, str]) -> _Plot:
        tool_id, col = key
        plot_widget = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
        )
        plot_widget.setMinimumHeight(
            _FOCUS_HEIGHT_HINT if self._mode == "focus" else _PLOT_HEIGHT_HINT
        )
        plot_widget.viewport().installEventFilter(self._graphics_wheel_filter)
        plot_item = plot_widget.getPlotItem()
        plot_item.setTitle(f"{tool_id} — {col}")
        plot_item.showGrid(x=True, y=True, alpha=_GRID_ALPHA)
        plot_item.setMouseEnabled(x=True, y=False)
        plot_item.disableAutoRange(axis="x")

        if self._anchor_plot is None:
            self._anchor_plot = plot_item
            self._connect_range_signal(plot_item)
        else:
            plot_item.setXLink(self._anchor_plot)

        low = pg.PlotDataItem(pen=None)
        high = pg.PlotDataItem(pen=None)
        fill = pg.FillBetweenItem(low, high, brush=_BAND_COLOR)
        avg = pg.PlotDataItem(pen=pg.mkPen(_LINE_COLOR, width=2))

        plot_item.addItem(fill)
        plot_item.addItem(low)
        plot_item.addItem(high)
        plot_item.addItem(avg)

        vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("w", width=1))
        vline.setVisible(False)
        plot_item.addItem(vline, ignoreBounds=True)

        overlay = pg.TextItem("", anchor=(0.5, 0.5), color=(180, 180, 180))
        overlay.setVisible(False)
        plot_item.addItem(overlay, ignoreBounds=True)

        # Each PlotWidget has its own scene; bind the source plot key into
        # the slot so the crosshair can map scene coords to view coords.
        plot_widget.scene().sigMouseMoved.connect(
            lambda pos, k=key: self._on_mouse_moved(k, pos)
        )

        # Insert before the trailing stretch so plots stay top-aligned.
        self._graphics_layout.insertWidget(
            self._graphics_layout.count() - 1, plot_widget
        )

        return _Plot(
            plot_widget=plot_widget,
            plot_item=plot_item,
            avg_curve=avg,
            low_curve=low,
            high_curve=high,
            fill=fill,
            vline=vline,
            overlay=overlay,
        )

    def _connect_range_signal(self, plot_item: pg.PlotItem) -> None:
        plot_item.getViewBox().sigXRangeChanged.connect(self._on_x_range_changed)

    def _disconnect_range_signal(self, plot_item: pg.PlotItem) -> None:
        try:
            plot_item.getViewBox().sigXRangeChanged.disconnect(self._on_x_range_changed)
        except (RuntimeError, TypeError):
            pass

    def _remove_plot(self, key: tuple[str, str]) -> None:
        plot = self._plots.pop(key, None)
        if plot is None:
            return
        if plot.plot_item is self._anchor_plot:
            self._disconnect_range_signal(plot.plot_item)
            self._anchor_plot = None
        self._graphics_layout.removeWidget(plot.plot_widget)
        plot.plot_widget.setParent(None)
        plot.plot_widget.deleteLater()
        if self._anchor_plot is None and self._plots:
            new_anchor = next(iter(self._plots.values())).plot_item
            self._anchor_plot = new_anchor
            self._connect_range_signal(new_anchor)
            for p in self._plots.values():
                if p.plot_item is not new_anchor:
                    p.plot_item.setXLink(new_anchor)

    def _apply_data(
        self,
        plot: _Plot,
        result: ToolResult | None,
        key: tuple[str, str],
    ) -> None:
        tool_id, col = key

        if isinstance(result, QueryError):
            self._show_overlay(plot, f"{_ERROR_PREFIX}{result.message}", error=True)
            plot.plot_item.setTitle(f"{tool_id} — {col}")
            return

        if result is None or result.num_rows == 0:
            self._show_overlay(plot, _NO_DATA_TEXT)
            plot.plot_item.setTitle(f"{tool_id} — {col}")
            return

        try:
            ts = _arrow_to_seconds(result.column("bucket"))
            avg = result.column(f"{col}_avg").to_numpy(zero_copy_only=False)
            low = result.column(f"{col}_min").to_numpy(zero_copy_only=False)
            high = result.column(f"{col}_max").to_numpy(zero_copy_only=False)
        except (KeyError, pa.ArrowInvalid):
            self._show_overlay(plot, _NO_DATA_TEXT)
            plot.plot_item.setTitle(f"{tool_id} — {col}")
            return

        finite_mask = np.isfinite(avg) if avg.dtype.kind == "f" else None
        if finite_mask is None or not finite_mask.any():
            self._show_overlay(plot, _NO_DATA_TEXT)
            plot.plot_item.setTitle(f"{tool_id} — {col}")
            return

        self._hide_overlay(plot)
        plot.avg_curve.setData(ts, avg)
        plot.low_curve.setData(ts, low)
        plot.high_curve.setData(ts, high)

        if self._mode == "focus":
            plot.plot_item.setTitle(_focus_title(tool_id, col, low, avg, high, finite_mask))
        else:
            plot.plot_item.setTitle(f"{tool_id} — {col}")

    def _show_overlay(self, plot: _Plot, text: str, *, error: bool = False) -> None:
        plot.avg_curve.setData([], [])
        plot.low_curve.setData([], [])
        plot.high_curve.setData([], [])
        color = (220, 100, 100) if error else (180, 180, 180)
        plot.overlay.setText(text, color=color)
        view_range = plot.plot_item.viewRange()
        cx = (view_range[0][0] + view_range[0][1]) / 2
        cy = (view_range[1][0] + view_range[1][1]) / 2
        plot.overlay.setPos(cx, cy)
        plot.overlay.setVisible(True)

    def _show_loading(self, plot: _Plot) -> None:
        self._show_overlay(plot, _LOADING_TEXT, error=False)

    def _hide_overlay(self, plot: _Plot) -> None:
        plot.overlay.setVisible(False)

    # --- Overview / sparkline rendering ----------------------------------

    def _layout_overview(self, plan: QueryPlan, *, fill_loading: bool) -> None:
        wanted = self._wanted_keys(plan)
        wanted_set = set(wanted)
        self._clear_graphics()

        for key in list(self._sparklines):
            if key not in wanted_set:
                stale = self._sparklines.pop(key)
                self._overview_layout.removeWidget(stale)
                stale.deleteLater()

        for idx, key in enumerate(wanted):
            existing = self._sparklines.get(key)
            if existing is None:
                tool_id, sensor = key
                sp = Sparkline(tool_id, sensor)
                sp.clicked.connect(self._on_sparkline_clicked)
                self._sparklines[key] = sp
            else:
                sp = existing
            row = idx // _OVERVIEW_COLUMNS
            col = idx % _OVERVIEW_COLUMNS
            self._overview_layout.addWidget(sp, row, col)
            if fill_loading:
                sp.show_message("Loading…")
            else:
                tool_id, _col = key
                self._apply_sparkline_data(sp, key, self._last_results.get(tool_id))

    def _fill_overview_tool(
        self, plan: QueryPlan, tool_id: str, result: ToolResult
    ) -> None:
        for q in plan.per_tool_queries:
            if q.tool_id != tool_id:
                continue
            for col in q.raw_columns:
                key = (tool_id, col)
                sp = self._sparklines.get(key)
                if sp is not None:
                    self._apply_sparkline_data(sp, key, result)

    def _apply_sparkline_data(
        self,
        sp: Sparkline,
        key: tuple[str, str],
        result: ToolResult | None,
    ) -> None:
        _tool_id, col = key
        if isinstance(result, QueryError):
            sp.show_message(_ERROR_PREFIX + result.message[:40])
            return
        if result is None or result.num_rows == 0:
            sp.show_message(_NO_DATA_TEXT)
            return
        try:
            ts = _arrow_to_seconds(result.column("bucket"))
            avg = result.column(f"{col}_avg").to_numpy(zero_copy_only=False)
        except (KeyError, pa.ArrowInvalid):
            sp.show_message(_NO_DATA_TEXT)
            return
        sp.set_data(ts, avg)

    def _clear_overview(self) -> None:
        for key in list(self._sparklines):
            sp = self._sparklines.pop(key)
            self._overview_layout.removeWidget(sp)
            sp.deleteLater()

    def _on_sparkline_clicked(self, tool_id: str, sensor: str) -> None:
        self.promoteRequested.emit(tool_id, sensor)

    # --- Range / crosshair -----------------------------------------------

    def _on_x_range_changed(self, _viewbox: object, range_pair: object) -> None:
        if self._suppress_range_signal:
            return
        try:
            start_ts = float(range_pair[0])  # type: ignore[index]
            end_ts = float(range_pair[1])  # type: ignore[index]
        except (TypeError, ValueError, IndexError):
            return
        if end_ts <= start_ts:
            return
        new_range = TimeRange(
            start=datetime.fromtimestamp(start_ts, tz=UTC),
            end=datetime.fromtimestamp(end_ts, tz=UTC),
        )
        self.rangeChangedByUser.emit(new_range)

    def _on_mouse_moved(
        self, source_key: tuple[str, str], scene_pos: QPointF
    ) -> None:
        source = self._plots.get(source_key)
        if source is None or not self._plots:
            return
        if not source.plot_item.sceneBoundingRect().contains(scene_pos):
            for plot in self._plots.values():
                plot.vline.setVisible(False)
            return
        view_pos = source.plot_item.vb.mapSceneToView(scene_pos)
        x = view_pos.x()
        for plot in self._plots.values():
            plot.vline.setPos(x)
            plot.vline.setVisible(True)


def _arrow_to_seconds(column: pa.ChunkedArray) -> np.ndarray:
    """Convert an Arrow timestamp column to float seconds since epoch."""
    np_array: np.ndarray = column.to_numpy(zero_copy_only=False)
    if np_array.dtype.kind == "M":
        seconds: np.ndarray = np_array.astype("datetime64[ns]").astype("int64") / 1e9
        return seconds
    return np_array.astype(float)


class _WheelToScrollFilter(QObject):
    """Plain wheel scrolls a target ``QScrollBar``; Ctrl+wheel falls through.

    Pyqtgraph's ``ViewBox.wheelEvent`` claims the wheel for x-axis zoom, which
    blocks the chart grid's vertical scrolling. Each ``PlotWidget`` is its own
    ``QAbstractScrollArea``, so this filter is installed on every
    ``PlotWidget.viewport()`` — that is where Qt delivers wheel events from a
    cursor over the plot. Plain wheel forwards to ``scrollbar.setValue()`` and
    accepts the event; Ctrl-modified events return ``False`` so they continue
    on to the ViewBox for zoom.
    """

    def __init__(self, scrollbar: QScrollBar, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scrollbar = scrollbar

    def eventFilter(self, _obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel or not isinstance(event, QWheelEvent):
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta == 0:
            return False
        self._scrollbar.setValue(self._scrollbar.value() - delta)
        event.accept()
        return True


def _focus_title(
    tool_id: str,
    col: str,
    low: np.ndarray,
    avg: np.ndarray,
    high: np.ndarray,
    finite_mask: np.ndarray,
) -> str:
    """Append a min / mean / max statistics strip to focus-mode plot titles."""
    finite_low = low[finite_mask] if low.dtype.kind == "f" else low
    finite_avg = avg[finite_mask]
    finite_high = high[finite_mask] if high.dtype.kind == "f" else high
    if finite_avg.size == 0:
        return f"{tool_id} — {col}"
    return (
        f"{tool_id} — {col}    "
        f"min={float(finite_low.min()):.3g}  "
        f"mean={float(finite_avg.mean()):.3g}  "
        f"max={float(finite_high.max()):.3g}"
    )


__all__ = ["ChartGrid", "MAX_VISIBLE_PLOTS", "FOCUS_MAX_PLOTS"]
