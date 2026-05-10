"""Unit tests for ``local_equs_client.data_layer.query_engine`` (C1.8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from local_equs_client.data_layer.query_engine import QueryEngine
from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery
from local_equs_client.selection.types import TimeRange


def _write_parquet(
    path: Path,
    *,
    start: datetime,
    n_rows: int = 600,
    hz: int = 10,
    sensor_columns: tuple[str, ...] = ("chamber_pressure", "rf_power"),
) -> tuple[datetime, datetime]:
    path.parent.mkdir(parents=True, exist_ok=True)
    naive_start = start.astimezone(UTC).replace(tzinfo=None)
    timestamps = [naive_start + timedelta(seconds=i / hz) for i in range(n_rows)]
    rng = np.random.default_rng(seed=1)
    columns: dict[str, pa.Array] = {
        "ts": pa.array(timestamps, type=pa.timestamp("ns")),
    }
    for name in sensor_columns:
        columns[name] = pa.array(rng.random(n_rows), type=pa.float64())
    pq.write_table(pa.Table.from_pydict(columns), path)
    return start, start + timedelta(seconds=(n_rows - 1) / hz)


def _make_plan(
    *,
    queries: list[ToolQuery],
    resolution: timedelta = timedelta(seconds=1),
) -> QueryPlan:
    return QueryPlan(
        per_tool_queries=queries,
        target_resolution=resolution,
        partial_data_warnings=[],
    )


def test_empty_plan_returns_empty_dict() -> None:
    engine = QueryEngine()
    assert engine.execute(_make_plan(queries=[])) == {}


def test_single_tool_returns_aggregated_table(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, 8, tzinfo=UTC)
    parquet = tmp_path / "etch_a1.parquet"
    _write_parquet(parquet, start=start, n_rows=600, hz=10)

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="etch_a1",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(
                    start=start,
                    end=start + timedelta(seconds=60),
                ),
            )
        ],
        resolution=timedelta(seconds=10),
    )

    engine = QueryEngine()
    results = engine.execute(plan)

    assert set(results) == {"etch_a1"}
    table = results["etch_a1"]
    column_names = set(table.column_names)
    assert "bucket" in column_names
    assert {"chamber_pressure_avg", "chamber_pressure_min", "chamber_pressure_max"} <= column_names
    # 60 seconds / 10s buckets ≈ 6 rows
    assert 5 <= table.num_rows <= 7


def test_multiple_tools_run_in_parallel(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    p1 = tmp_path / "a.parquet"
    p2 = tmp_path / "b.parquet"
    _write_parquet(p1, start=start, n_rows=120)
    _write_parquet(p2, start=start, n_rows=120)

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="a",
                file_paths=(p1,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=12)),
            ),
            ToolQuery(
                tool_id="b",
                file_paths=(p2,),
                raw_columns=("rf_power",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=12)),
            ),
        ],
        resolution=timedelta(seconds=1),
    )

    results = QueryEngine().execute(plan)
    assert set(results) == {"a", "b"}
    assert "chamber_pressure_avg" in results["a"].column_names
    assert "rf_power_avg" in results["b"].column_names


def test_time_range_filters_rows(tmp_path: Path) -> None:
    """Window covering only part of the file should bucket only that slice."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "etch_a1.parquet"
    _write_parquet(parquet, start=start, n_rows=1200, hz=10)  # 120 seconds total

    window_start = start + timedelta(seconds=30)
    window_end = start + timedelta(seconds=60)

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="etch_a1",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=window_start, end=window_end),
            )
        ],
        resolution=timedelta(seconds=10),
    )
    table = QueryEngine().execute(plan)["etch_a1"]
    # 30s window / 10s buckets = 3 rows
    assert 2 <= table.num_rows <= 4


def test_multiple_columns_each_get_avg_min_max(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "etch_a1.parquet"
    _write_parquet(
        parquet,
        start=start,
        n_rows=300,
        sensor_columns=("chamber_pressure", "rf_power", "wall_temp"),
    )

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="etch_a1",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure", "rf_power"),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=30)),
            )
        ],
        resolution=timedelta(seconds=5),
    )
    table = QueryEngine().execute(plan)["etch_a1"]
    cols = set(table.column_names)
    for sensor in ("chamber_pressure", "rf_power"):
        for kind in ("avg", "min", "max"):
            assert f"{sensor}_{kind}" in cols
    assert "wall_temp_avg" not in cols  # not requested


def test_missing_column_in_one_file_uses_union_by_name(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    full = tmp_path / "full.parquet"
    partial = tmp_path / "partial.parquet"
    _write_parquet(full, start=start, n_rows=120, sensor_columns=("chamber_pressure", "rf_power"))
    _write_parquet(partial, start=start, n_rows=120, sensor_columns=("chamber_pressure",))

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="etch_a1",
                file_paths=(full, partial),
                raw_columns=("chamber_pressure", "rf_power"),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=12)),
            )
        ],
        resolution=timedelta(seconds=1),
    )
    table = QueryEngine().execute(plan)["etch_a1"]
    assert "rf_power_avg" in table.column_names
    assert "chamber_pressure_avg" in table.column_names


def test_no_columns_returns_empty_table(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "etch_a1.parquet"
    _write_parquet(parquet, start=start, n_rows=120)

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="etch_a1",
                file_paths=(parquet,),
                raw_columns=(),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=12)),
            )
        ]
    )
    table = QueryEngine().execute(plan)["etch_a1"]
    assert table.num_columns == 0


def test_no_files_returns_empty_table(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="etch_a1",
                file_paths=(),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=12)),
            )
        ]
    )
    table = QueryEngine().execute(plan)["etch_a1"]
    assert table.num_columns == 0


def test_per_tool_error_isolated(tmp_path: Path) -> None:
    """A bad path for one tool yields QueryError; the other tool's table flows through."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    good = tmp_path / "good.parquet"
    _write_parquet(good, start=start, n_rows=120)
    bogus = tmp_path / "missing.parquet"  # never created → bad path

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="good_tool",
                file_paths=(good,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=12)),
            ),
            ToolQuery(
                tool_id="bad_tool",
                file_paths=(bogus,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=12)),
            ),
        ],
    )

    from local_equs_client.data_layer.query_engine import QueryError

    results = QueryEngine().execute(plan)
    assert set(results) == {"good_tool", "bad_tool"}
    assert isinstance(results["good_tool"], pa.Table)
    assert isinstance(results["bad_tool"], QueryError)
    assert results["bad_tool"].tool_id == "bad_tool"
    assert results["bad_tool"].message  # non-empty


def test_cancellation_raises_query_cancelled(tmp_path: Path) -> None:
    """Cancellation polled before any future completes."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "etch_a1.parquet"
    _write_parquet(parquet, start=start, n_rows=60_000, hz=100)  # ~600s

    plan = _make_plan(
        queries=[
            ToolQuery(
                tool_id="etch_a1",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=600)),
            )
        ],
        resolution=timedelta(seconds=1),
    )

    from local_equs_client.data_layer.query_engine import QueryCancelled

    cancelled_flag = [True]  # cancel immediately
    with pytest.raises(QueryCancelled):
        QueryEngine().execute(plan, cancelled=lambda: cancelled_flag[0])
