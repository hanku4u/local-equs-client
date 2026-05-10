"""Unit tests for ``local_equs_client.ui.chart_grid`` (C1.10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

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
