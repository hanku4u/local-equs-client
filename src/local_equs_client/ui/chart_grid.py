"""PyQtGraph-based linked chart grid (C1.10).

One PlotItem per ``(tool_id, raw_column)`` pair. Avg drawn as a solid line,
min/max as a faint fill band. All x-axes linked through the first plot;
mouse hover anywhere shows a synchronized vertical crosshair across the
whole grid. Updates flow through ``setData()`` so the existing curves get
new arrays rather than being recreated.

Connect this widget's :meth:`update_from_results` to
``QueryController.queryCompleted``; that's the only legitimate write path.
M1 lays out plots in a single column. Viewport-aware virtualization arrives
in C4.6.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from local_equs_client.data_layer.query_planner import QueryPlan

_BAND_COLOR = (80, 120, 200, 60)
_LINE_COLOR = (80, 120, 200)
_GRID_ALPHA = 0.3
_PLOT_HEIGHT_HINT = 200


@dataclass(slots=True)
class _Plot:
    plot_item: pg.PlotItem
    avg_curve: pg.PlotDataItem
    low_curve: pg.PlotDataItem
    high_curve: pg.PlotDataItem
    fill: pg.FillBetweenItem
    vline: pg.InfiniteLine


class ChartGrid(QWidget):
    """Linked grid of plots, one per (tool, raw sensor) pair."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self._graphics)

        self._plots: dict[tuple[str, str], _Plot] = {}
        self._anchor_plot: pg.PlotItem | None = None

        self._graphics.scene().sigMouseMoved.connect(self._on_mouse_moved)

    # --- Public API -------------------------------------------------------

    def update_from_results(
        self, plan: QueryPlan, results: dict[str, pa.Table]
    ) -> None:
        """Apply a completed query plan + results to the grid."""
        wanted: list[tuple[str, str]] = [
            (q.tool_id, col) for q in plan.per_tool_queries for col in q.raw_columns
        ]
        wanted_set = set(wanted)

        for key in list(self._plots):
            if key not in wanted_set:
                self._remove_plot(key)

        for key in wanted:
            tool_id, col = key
            table = results.get(tool_id)
            self._upsert_plot(key, table)

    def clear(self) -> None:
        for key in list(self._plots):
            self._remove_plot(key)

    # --- Plot management --------------------------------------------------

    def _upsert_plot(self, key: tuple[str, str], table: pa.Table | None) -> None:
        plot = self._plots.get(key) or self._create_plot(key)
        self._plots[key] = plot
        self._apply_data(plot, table, key)

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

        if self._anchor_plot is None:
            self._anchor_plot = plot_item
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

        return _Plot(
            plot_item=plot_item,
            avg_curve=avg,
            low_curve=low,
            high_curve=high,
            fill=fill,
            vline=vline,
        )

    def _remove_plot(self, key: tuple[str, str]) -> None:
        plot = self._plots.pop(key, None)
        if plot is None:
            return
        if plot.plot_item is self._anchor_plot:
            self._anchor_plot = None
        self._graphics.removeItem(plot.plot_item)
        if self._anchor_plot is None and self._plots:
            new_anchor = next(iter(self._plots.values())).plot_item
            self._anchor_plot = new_anchor
            for p in self._plots.values():
                if p.plot_item is not new_anchor:
                    p.plot_item.setXLink(new_anchor)

    def _apply_data(
        self,
        plot: _Plot,
        table: pa.Table | None,
        key: tuple[str, str],
    ) -> None:
        _tool_id, col = key
        if table is None or table.num_rows == 0:
            plot.avg_curve.setData([], [])
            plot.low_curve.setData([], [])
            plot.high_curve.setData([], [])
            return

        try:
            ts = _arrow_to_seconds(table.column("bucket"))
            avg = table.column(f"{col}_avg").to_numpy(zero_copy_only=False)
            low = table.column(f"{col}_min").to_numpy(zero_copy_only=False)
            high = table.column(f"{col}_max").to_numpy(zero_copy_only=False)
        except (KeyError, pa.ArrowInvalid):
            plot.avg_curve.setData([], [])
            plot.low_curve.setData([], [])
            plot.high_curve.setData([], [])
            return

        plot.avg_curve.setData(ts, avg)
        plot.low_curve.setData(ts, low)
        plot.high_curve.setData(ts, high)

    # --- Crosshair --------------------------------------------------------

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


# Suppress unused-import: Qt re-exported as a convenience for subclasses.
_ = Qt

__all__ = ["ChartGrid"]
