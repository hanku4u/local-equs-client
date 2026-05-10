"""PyQtGraph-based linked chart grid (C1.10, C1.11, C1.12, C1.13).

One PlotItem per ``(tool_id, raw_column)`` pair. Avg drawn as a solid line,
min/max as a faint fill band. All x-axes linked through the anchor plot and a
synchronized vertical crosshair tracks the mouse across the whole grid. Updates
flow through ``setData()`` so the existing curves keep their identity.

C1.11: pan/zoom on any chart fires :attr:`rangeChangedByUser` (the controller
listens and updates ``SelectionModel.time_range``). The widget suppresses that
signal while applying its own programmatic range to avoid feedback loops.

C1.12: when a tool's query came back as :class:`QueryError`, every plot for
that tool shows a red error label instead of a curve.

C1.13: when the result is empty (range outside local data, or column missing
from every file in range), the plot shows "No data in range" centered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pyarrow as pa
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from local_equs_client.data_layer.query_engine import QueryError, ToolResult
from local_equs_client.data_layer.query_planner import QueryPlan
from local_equs_client.selection.types import TimeRange

_BAND_COLOR = (80, 120, 200, 60)
_LINE_COLOR = (80, 120, 200)
_GRID_ALPHA = 0.3
_PLOT_HEIGHT_HINT = 200
_NO_DATA_TEXT = "No data in range"
_ERROR_PREFIX = "Tool error: "


@dataclass(slots=True)
class _Plot:
    plot_item: pg.PlotItem
    avg_curve: pg.PlotDataItem
    low_curve: pg.PlotDataItem
    high_curve: pg.PlotDataItem
    fill: pg.FillBetweenItem
    vline: pg.InfiniteLine
    overlay: pg.TextItem
    range_signal_connected: bool = field(default=False)


class ChartGrid(QWidget):
    """Linked grid of plots, one per (tool, raw sensor) pair."""

    rangeChangedByUser = Signal(object)  # emits a TimeRange

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self._graphics)

        self._plots: dict[tuple[str, str], _Plot] = {}
        self._anchor_plot: pg.PlotItem | None = None
        self._suppress_range_signal = False

        self._graphics.scene().sigMouseMoved.connect(self._on_mouse_moved)

    # --- Public API -------------------------------------------------------

    def update_from_results(
        self, plan: QueryPlan, results: dict[str, ToolResult]
    ) -> None:
        """Apply a completed query plan + per-tool results to the grid."""
        wanted: list[tuple[str, str]] = [
            (q.tool_id, col) for q in plan.per_tool_queries for col in q.raw_columns
        ]
        wanted_set = set(wanted)

        self._suppress_range_signal = True
        try:
            for key in list(self._plots):
                if key not in wanted_set:
                    self._remove_plot(key)

            for key in wanted:
                tool_id, _col = key
                self._upsert_plot(key, results.get(tool_id))

            if plan.per_tool_queries and self._anchor_plot is not None:
                tr = plan.per_tool_queries[0].time_range
                self._anchor_plot.setXRange(
                    tr.start.timestamp(), tr.end.timestamp(), padding=0
                )
        finally:
            self._suppress_range_signal = False

    def clear(self) -> None:
        for key in list(self._plots):
            self._remove_plot(key)

    # --- Plot management --------------------------------------------------

    def _upsert_plot(self, key: tuple[str, str], result: ToolResult | None) -> None:
        plot = self._plots.get(key) or self._create_plot(key)
        self._plots[key] = plot
        self._apply_data(plot, result, key)

    def _create_plot(self, key: tuple[str, str]) -> _Plot:
        tool_id, col = key
        row_index = len(self._plots)
        plot_item = self._graphics.addPlot(
            row=row_index,
            col=0,
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")},
            title=f"{tool_id} — {col}",
        )
        plot_item.showGrid(x=True, y=True, alpha=_GRID_ALPHA)
        plot_item.setMinimumHeight(_PLOT_HEIGHT_HINT)
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

        return _Plot(
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
        self._graphics.removeItem(plot.plot_item)
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
        _tool_id, col = key

        if isinstance(result, QueryError):
            self._show_overlay(plot, f"{_ERROR_PREFIX}{result.message}", error=True)
            return

        if result is None or result.num_rows == 0:
            self._show_overlay(plot, _NO_DATA_TEXT)
            return

        try:
            ts = _arrow_to_seconds(result.column("bucket"))
            avg = result.column(f"{col}_avg").to_numpy(zero_copy_only=False)
            low = result.column(f"{col}_min").to_numpy(zero_copy_only=False)
            high = result.column(f"{col}_max").to_numpy(zero_copy_only=False)
        except (KeyError, pa.ArrowInvalid):
            self._show_overlay(plot, _NO_DATA_TEXT)
            return

        finite_mask = np.isfinite(avg) if avg.dtype.kind == "f" else None
        if finite_mask is None or not finite_mask.any():
            self._show_overlay(plot, _NO_DATA_TEXT)
            return

        self._hide_overlay(plot)
        plot.avg_curve.setData(ts, avg)
        plot.low_curve.setData(ts, low)
        plot.high_curve.setData(ts, high)

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

    def _hide_overlay(self, plot: _Plot) -> None:
        plot.overlay.setVisible(False)

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

    def _on_mouse_moved(self, scene_pos: QPointF) -> None:
        if not self._plots:
            return
        anchor = self._anchor_plot
        if anchor is None:
            return
        if not anchor.sceneBoundingRect().contains(scene_pos):
            for plot in self._plots.values():
                plot.vline.setVisible(False)
            return
        view_pos = anchor.vb.mapSceneToView(scene_pos)
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


__all__ = ["ChartGrid"]
