"""Unit tests for ``local_equs_client.ui.chart_grid`` (C1.10, C1.11, C1.12, C1.13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt  # noqa: E402

from local_equs_client.data_layer.query_engine import QueryError  # noqa: E402
from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery  # noqa: E402
from local_equs_client.selection.types import TimeRange  # noqa: E402
from local_equs_client.ui.chart_grid import ChartGrid  # noqa: E402


def _make_plan(per_tool: list[tuple[str, tuple[str, ...]]]) -> QueryPlan:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return QueryPlan(
        per_tool_queries=[
            ToolQuery(
                tool_id=tool_id,
                file_paths=(Path(f"{tool_id}.parquet"),),
                raw_columns=raw,
                time_range=TimeRange(start=start, end=start + timedelta(seconds=60)),
            )
            for tool_id, raw in per_tool
        ],
        target_resolution=timedelta(seconds=10),
        partial_data_warnings=[],
    )


def _make_results(*, columns: tuple[str, ...]) -> pa.Table:
    naive_start = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    timestamps = [naive_start + timedelta(seconds=i * 10) for i in range(6)]
    rng = np.random.default_rng(seed=1)
    arrays: dict[str, pa.Array] = {
        "bucket": pa.array(timestamps, type=pa.timestamp("ns")),
    }
    for col in columns:
        arrays[f"{col}_avg"] = pa.array(rng.random(6), type=pa.float64())
        arrays[f"{col}_min"] = pa.array(rng.random(6), type=pa.float64())
        arrays[f"{col}_max"] = pa.array(rng.random(6), type=pa.float64())
    return pa.Table.from_pydict(arrays)


def test_empty_grid(qapp) -> None:
    grid = ChartGrid()
    assert grid._plots == {}  # noqa: SLF001


def test_creates_one_plot_per_tool_sensor(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("chamber_pressure", "rf_power"))])
    results = {"etch_a1": _make_results(columns=("chamber_pressure", "rf_power"))}
    grid.update_from_results(plan, results)
    assert set(grid._plots) == {  # noqa: SLF001
        ("etch_a1", "chamber_pressure"),
        ("etch_a1", "rf_power"),
    }


def test_removes_plots_no_longer_in_plan(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("etch_a1", ("chamber_pressure", "rf_power"))]),
        {"etch_a1": _make_results(columns=("chamber_pressure", "rf_power"))},
    )

    grid.update_from_results(
        _make_plan([("etch_a1", ("chamber_pressure",))]),
        {"etch_a1": _make_results(columns=("chamber_pressure",))},
    )
    assert set(grid._plots) == {("etch_a1", "chamber_pressure")}  # noqa: SLF001


def test_handles_missing_tool_results_gracefully(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("chamber_pressure",))])
    grid.update_from_results(plan, {})  # no tool data
    plot = grid._plots[("etch_a1", "chamber_pressure")]  # noqa: SLF001
    x, _y = plot.avg_curve.getData()
    assert x is None or len(x) == 0


def test_clear_drops_every_plot(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("etch_a1", ("chamber_pressure",))]),
        {"etch_a1": _make_results(columns=("chamber_pressure",))},
    )
    grid.clear()
    assert grid._plots == {}  # noqa: SLF001


def test_x_axes_link_to_first_plot(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("a", ("c",)), ("b", ("c",))]),
        {
            "a": _make_results(columns=("c",)),
            "b": _make_results(columns=("c",)),
        },
    )
    anchor = grid._anchor_plot  # noqa: SLF001
    assert anchor is not None
    other_keys = [k for k in grid._plots if grid._plots[k].plot_item is not anchor]  # noqa: SLF001
    assert other_keys, "expected at least one non-anchor plot"
    other = grid._plots[other_keys[0]].plot_item  # noqa: SLF001
    assert other.getViewBox().linkedView(0) is anchor.getViewBox()


# --- C1.11: zoom-driven re-query --------------------------------------------


def test_user_range_change_emits_signal(qapp) -> None:
    grid = ChartGrid()
    grid.update_from_results(
        _make_plan([("etch_a1", ("c",))]),
        {"etch_a1": _make_results(columns=("c",))},
    )
    received: list[TimeRange] = []
    grid.rangeChangedByUser.connect(received.append, Qt.DirectConnection)

    grid._on_x_range_changed(None, (1778262200.0, 1778262300.0))  # noqa: SLF001

    assert len(received) == 1
    assert received[0].start.timestamp() == 1778262200.0
    assert received[0].end.timestamp() == 1778262300.0


def test_programmatic_range_change_does_not_emit(qapp) -> None:
    grid = ChartGrid()
    received: list[TimeRange] = []
    grid.rangeChangedByUser.connect(received.append, Qt.DirectConnection)

    grid.update_from_results(
        _make_plan([("etch_a1", ("c",))]),
        {"etch_a1": _make_results(columns=("c",))},
    )

    assert received == []


def test_invalid_range_pair_does_not_emit(qapp) -> None:
    grid = ChartGrid()
    received: list[TimeRange] = []
    grid.rangeChangedByUser.connect(received.append, Qt.DirectConnection)

    grid._on_x_range_changed(None, (100.0, 100.0))  # zero-width  # noqa: SLF001
    grid._on_x_range_changed(None, (200.0, 100.0))  # inverted  # noqa: SLF001

    assert received == []


# --- C1.12: per-tool error -------------------------------------------------


def test_query_error_shows_overlay_no_curves(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("c",))])
    grid.update_from_results(
        plan, {"etch_a1": QueryError(tool_id="etch_a1", message="bad parquet")}
    )
    plot = grid._plots[("etch_a1", "c")]  # noqa: SLF001
    assert plot.overlay.isVisible()
    x, _y = plot.avg_curve.getData()
    assert x is None or len(x) == 0


def test_one_tool_error_does_not_affect_other_tool(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("good", ("c",)), ("bad", ("c",))])
    grid.update_from_results(
        plan,
        {
            "good": _make_results(columns=("c",)),
            "bad": QueryError(tool_id="bad", message="oh no"),
        },
    )
    good_plot = grid._plots[("good", "c")]  # noqa: SLF001
    bad_plot = grid._plots[("bad", "c")]  # noqa: SLF001
    good_x, _ = good_plot.avg_curve.getData()
    assert good_x is not None and len(good_x) > 0
    assert not good_plot.overlay.isVisible()
    assert bad_plot.overlay.isVisible()


# --- C1.13: no data overlay ------------------------------------------------


def test_empty_table_shows_no_data_overlay(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("c",))])
    empty_table = pa.Table.from_pydict(
        {
            "bucket": pa.array([], type=pa.timestamp("ns")),
            "c_avg": pa.array([], type=pa.float64()),
            "c_min": pa.array([], type=pa.float64()),
            "c_max": pa.array([], type=pa.float64()),
        }
    )
    grid.update_from_results(plan, {"etch_a1": empty_table})

    plot = grid._plots[("etch_a1", "c")]  # noqa: SLF001
    assert plot.overlay.isVisible()
    assert "No data" in plot.overlay.toPlainText()


def test_all_null_avg_shows_no_data_overlay(qapp) -> None:
    """Result has rows but every avg is NaN — common when union_by_name fills missing column."""
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("c",))])
    nan = float("nan")
    naive_start = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    timestamps = [naive_start + timedelta(seconds=i * 10) for i in range(6)]
    table = pa.Table.from_pydict(
        {
            "bucket": pa.array(timestamps, type=pa.timestamp("ns")),
            "c_avg": pa.array([nan] * 6, type=pa.float64()),
            "c_min": pa.array([nan] * 6, type=pa.float64()),
            "c_max": pa.array([nan] * 6, type=pa.float64()),
        }
    )
    grid.update_from_results(plan, {"etch_a1": table})

    plot = grid._plots[("etch_a1", "c")]  # noqa: SLF001
    assert plot.overlay.isVisible()
    assert "No data" in plot.overlay.toPlainText()


def test_overlay_hidden_when_data_returns(qapp) -> None:
    grid = ChartGrid()
    plan = _make_plan([("etch_a1", ("c",))])
    grid.update_from_results(plan, {"etch_a1": QueryError(tool_id="etch_a1", message="x")})
    plot = grid._plots[("etch_a1", "c")]  # noqa: SLF001
    assert plot.overlay.isVisible()

    grid.update_from_results(plan, {"etch_a1": _make_results(columns=("c",))})
    assert not plot.overlay.isVisible()
