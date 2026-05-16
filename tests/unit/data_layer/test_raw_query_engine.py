"""Unit tests for ``local_equs_client.data_layer.raw_query_engine`` (C5.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery
from local_equs_client.data_layer.raw_query_engine import RawQueryEngine
from local_equs_client.selection.types import TimeRange


def _write_parquet(
    path: Path,
    *,
    start: datetime,
    n_rows: int = 100,
    hz: int = 10,
    sensor_columns: tuple[str, ...] = ("chamber_pressure", "rf_power"),
) -> None:
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


def _make_plan(queries: list[ToolQuery]) -> QueryPlan:
    return QueryPlan(
        per_tool_queries=queries,
        target_resolution=timedelta(seconds=1),  # ignored by RawQueryEngine
        partial_data_warnings=[],
    )


def test_count_empty_plan_returns_zero() -> None:
    engine = RawQueryEngine()
    assert engine.count(_make_plan([])) == 0


def test_count_single_tool_in_range(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "a.parquet"
    _write_parquet(parquet, start=start, n_rows=100, hz=10)

    engine = RawQueryEngine()
    plan = _make_plan(
        [
            ToolQuery(
                tool_id="a",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=5)),
            )
        ]
    )
    # 5 seconds at 10 Hz = rows whose ts is in [start, start+5s) → 50 rows
    assert engine.count(plan) == 50


def test_count_multi_tool_sums_across_tools(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    p1 = tmp_path / "a.parquet"
    p2 = tmp_path / "b.parquet"
    _write_parquet(p1, start=start, n_rows=100, hz=10)
    _write_parquet(p2, start=start, n_rows=100, hz=10)

    engine = RawQueryEngine()
    plan = _make_plan(
        [
            ToolQuery(
                tool_id="a",
                file_paths=(p1,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=5)),
            ),
            ToolQuery(
                tool_id="b",
                file_paths=(p2,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=5)),
            ),
        ]
    )
    assert engine.count(plan) == 100  # 50 per tool


def test_fetch_page_returns_rows_ordered_asc(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "a.parquet"
    _write_parquet(parquet, start=start, n_rows=100, hz=10)

    engine = RawQueryEngine()
    plan = _make_plan(
        [
            ToolQuery(
                tool_id="a",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=5)),
            )
        ]
    )
    table = engine.fetch_page(plan, offset=0, limit=10)
    assert table.num_rows == 10
    assert table.column_names == ["tool_id", "ts", "chamber_pressure"]
    ts_seconds = table.column("ts").to_pylist()
    assert ts_seconds == sorted(ts_seconds)  # ASC


def test_fetch_page_offset_returns_correct_slice(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "a.parquet"
    _write_parquet(parquet, start=start, n_rows=100, hz=10)

    engine = RawQueryEngine()
    plan = _make_plan(
        [
            ToolQuery(
                tool_id="a",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=10)),
            )
        ]
    )
    page0 = engine.fetch_page(plan, offset=0, limit=20).to_pylist()
    page1 = engine.fetch_page(plan, offset=20, limit=20).to_pylist()
    # Last row of page 0 strictly before the first row of page 1 by ts.
    assert page0[-1]["ts"] < page1[0]["ts"]
    assert len(page0) == 20
    assert len(page1) == 20


def test_fetch_page_desc_reverses_order(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "a.parquet"
    _write_parquet(parquet, start=start, n_rows=50, hz=10)

    engine = RawQueryEngine()
    plan = _make_plan(
        [
            ToolQuery(
                tool_id="a",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=5)),
            )
        ]
    )
    table = engine.fetch_page(plan, offset=0, limit=10, order="desc")
    ts = table.column("ts").to_pylist()
    assert ts == sorted(ts, reverse=True)


def test_fetch_page_multi_tool_with_null_padding(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    p_a = tmp_path / "a.parquet"
    p_b = tmp_path / "b.parquet"
    _write_parquet(p_a, start=start, n_rows=20, hz=10, sensor_columns=("chamber_pressure",))
    _write_parquet(
        p_b,
        start=start,
        n_rows=20,
        hz=10,
        sensor_columns=("chamber_pressure", "rf_power"),
    )

    engine = RawQueryEngine()
    plan = _make_plan(
        [
            ToolQuery(
                tool_id="a",
                file_paths=(p_a,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=5)),
            ),
            ToolQuery(
                tool_id="b",
                file_paths=(p_b,),
                raw_columns=("chamber_pressure", "rf_power"),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=5)),
            ),
        ]
    )
    table = engine.fetch_page(plan, offset=0, limit=100)
    assert table.column_names == ["tool_id", "ts", "chamber_pressure", "rf_power"]
    rows = table.to_pylist()
    a_rows = [r for r in rows if r["tool_id"] == "a"]
    b_rows = [r for r in rows if r["tool_id"] == "b"]
    assert a_rows and b_rows
    assert all(r["rf_power"] is None for r in a_rows)
    assert all(r["rf_power"] is not None for r in b_rows)
