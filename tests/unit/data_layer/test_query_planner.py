"""Unit tests for ``local_equs_client.data_layer.query_planner`` (C1.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.query_planner import QueryPlanner
from local_equs_client.selection.types import Selection, TimeRange
from local_equs_client.state import db


def _write_parquet(path: Path, start: datetime, n_rows: int = 50, hz: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    naive = start.astimezone(UTC).replace(tzinfo=None)
    timestamps = [naive + timedelta(seconds=i / hz) for i in range(n_rows)]
    table = pa.Table.from_pydict(
        {
            "ts": pa.array(timestamps, type=pa.timestamp("ns")),
            "chamber_pressure": pa.array(np.zeros(n_rows), type=pa.float64()),
        }
    )
    pq.write_table(table, path)


@pytest.fixture
def library_with_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    yield (data_dir, conn)
    conn.close()


def _make_selection(
    tools: tuple[str, ...] = (),
    sensors_raw: tuple[str, ...] = (),
    *,
    start: datetime,
    end: datetime,
) -> Selection:
    return Selection(
        tools=tools,
        sensors_canonical=(),
        sensors_raw=sensors_raw,
        time_range=TimeRange(start=start, end=end),
    )


# --- Bucket selection ------------------------------------------------------


@pytest.mark.parametrize(
    ("range_seconds", "expected_bucket"),
    [
        (60, timedelta(seconds=1)),  # 60 / 2000 = 0.03 → 1s
        (1800, timedelta(seconds=1)),  # 30 min → ~0.9 → 1s
        (3600, timedelta(seconds=10)),  # 1 hour → 1.8 → 10s
        (12 * 3600, timedelta(minutes=1)),  # 12h → 21.6s → 1min
        (24 * 3600, timedelta(minutes=1)),  # 1 day → 43.2s → 1min
        (7 * 24 * 3600, timedelta(hours=1)),  # 1 week → 302s → 1h (next clean step >= 302)
        (30 * 24 * 3600, timedelta(hours=1)),  # 1 month → 1296s → 1h
        (365 * 24 * 3600, timedelta(days=1)),  # 1 year → 15768s → 1d
    ],
)
def test_bucket_selection_for_typical_ranges(
    library_with_files, range_seconds: int, expected_bucket: timedelta
) -> None:
    data_dir, conn = library_with_files
    library = LocalLibrary(data_dir, conn)
    planner = QueryPlanner(library)

    start = datetime(2026, 1, 1, tzinfo=UTC)
    selection = _make_selection(start=start, end=start + timedelta(seconds=range_seconds))

    plan = planner.plan(selection, mode="standard", viewport_width_px=1920)
    assert plan.target_resolution == expected_bucket


# --- Per-tool query construction ------------------------------------------


def test_no_tools_yields_empty_per_tool_queries(library_with_files) -> None:
    data_dir, conn = library_with_files
    library = LocalLibrary(data_dir, conn)
    planner = QueryPlanner(library)

    plan = planner.plan(
        _make_selection(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 2, tzinfo=UTC),
        ),
        mode="standard",
        viewport_width_px=1920,
    )
    assert plan.per_tool_queries == []
    assert plan.partial_data_warnings == []


def test_single_tool_query_attaches_files_and_columns(library_with_files) -> None:
    data_dir, conn = library_with_files
    start = datetime(2026, 1, 1, 8, tzinfo=UTC)
    _write_parquet(data_dir / "etch_a1.parquet", start, n_rows=600)

    library = LocalLibrary(data_dir, conn)
    library.scan()
    planner = QueryPlanner(library)

    selection = _make_selection(
        tools=("etch_a1",),
        sensors_raw=("chamber_pressure",),
        start=start,
        end=start + timedelta(minutes=10),
    )
    plan = planner.plan(selection, mode="standard", viewport_width_px=1920)

    assert len(plan.per_tool_queries) == 1
    q = plan.per_tool_queries[0]
    assert q.tool_id == "etch_a1"
    assert q.raw_columns == ("chamber_pressure",)
    assert q.file_paths == (data_dir / "etch_a1.parquet",)
    assert q.time_range == selection.time_range


def test_multiple_tools_each_get_their_own_query(library_with_files) -> None:
    data_dir, conn = library_with_files
    start = datetime(2026, 1, 1, tzinfo=UTC)
    _write_parquet(data_dir / "etch_a1.parquet", start, n_rows=600)
    _write_parquet(data_dir / "etch_b2.parquet", start, n_rows=600)

    library = LocalLibrary(data_dir, conn)
    library.scan()
    planner = QueryPlanner(library)

    selection = _make_selection(
        tools=("etch_a1", "etch_b2"),
        sensors_raw=("chamber_pressure",),
        start=start,
        end=start + timedelta(minutes=10),
    )
    plan = planner.plan(selection, mode="standard", viewport_width_px=1920)

    by_tool = {q.tool_id: q for q in plan.per_tool_queries}
    assert set(by_tool) == {"etch_a1", "etch_b2"}
    assert len(by_tool["etch_a1"].file_paths) == 1
    assert len(by_tool["etch_b2"].file_paths) == 1


# --- Partial-data warnings ------------------------------------------------


def test_warning_when_no_files_for_tool(library_with_files) -> None:
    data_dir, conn = library_with_files
    library = LocalLibrary(data_dir, conn)
    planner = QueryPlanner(library)

    selection = _make_selection(
        tools=("etch_a1",),
        sensors_raw=("chamber_pressure",),
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    plan = planner.plan(selection, mode="standard", viewport_width_px=1920)

    assert any("etch_a1" in w and "No local data" in w for w in plan.partial_data_warnings)


def test_warning_when_range_extends_before_local_data(library_with_files) -> None:
    data_dir, conn = library_with_files
    file_start = datetime(2026, 1, 1, 12, tzinfo=UTC)
    _write_parquet(data_dir / "etch_a1.parquet", file_start, n_rows=600)

    library = LocalLibrary(data_dir, conn)
    library.scan()
    planner = QueryPlanner(library)

    selection = _make_selection(
        tools=("etch_a1",),
        sensors_raw=("chamber_pressure",),
        start=file_start - timedelta(hours=2),
        end=file_start + timedelta(minutes=20),
    )
    plan = planner.plan(selection, mode="standard", viewport_width_px=1920)

    assert any("starts before local data" in w for w in plan.partial_data_warnings)


def test_no_warnings_when_fully_covered(library_with_files) -> None:
    data_dir, conn = library_with_files
    file_start = datetime(2026, 1, 1, 0, tzinfo=UTC)
    _write_parquet(data_dir / "etch_a1.parquet", file_start, n_rows=3600, hz=1)

    library = LocalLibrary(data_dir, conn)
    library.scan()
    planner = QueryPlanner(library)

    selection = _make_selection(
        tools=("etch_a1",),
        sensors_raw=("chamber_pressure",),
        start=file_start + timedelta(minutes=5),
        end=file_start + timedelta(minutes=10),
    )
    plan = planner.plan(selection, mode="standard", viewport_width_px=1920)

    assert plan.partial_data_warnings == []
