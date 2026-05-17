"""Unit tests for ``local_equs_client.ui.export`` (C5.8 CSV export)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from local_equs_client.data_layer.query_engine import QueryError
from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery
from local_equs_client.data_layer.raw_query_engine import RawQueryEngine
from local_equs_client.selection.types import TimeRange
from local_equs_client.ui.export import write_chart_csv, write_table_csv


def _chart_table(rows: list[tuple[datetime, float, float]]) -> pa.Table:
    # Chart pipeline uses ``bucket`` for the time column (time_bucket()),
    # and naive timestamps interpreted as UTC.
    naive = [r[0].replace(tzinfo=None) for r in rows]
    return pa.Table.from_pydict(
        {
            "bucket": pa.array(naive, type=pa.timestamp("us")),
            "chamber_pressure": pa.array([r[1] for r in rows], type=pa.float64()),
            "rf_power": pa.array([r[2] for r in rows], type=pa.float64()),
        }
    )


def test_write_chart_csv_single_tool(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    table = _chart_table(
        [
            (t0, 1.5, 100.0),
            (t0 + timedelta(seconds=1), 2.5, 200.0),
        ]
    )
    out = tmp_path / "chart.csv"
    write_chart_csv(out, {"tool_a": table})

    text = out.read_text(encoding="utf-8")
    lines = text.strip().split("\n")
    assert lines[0] == "ts,tool_a.chamber_pressure,tool_a.rf_power"
    assert lines[1] == "2026-01-01T00:00:00+00:00,1.5,100.0"
    assert lines[2] == "2026-01-01T00:00:01+00:00,2.5,200.0"


def test_write_chart_csv_multi_tool_aligns_on_ts(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    a = pa.Table.from_pydict(
        {
            "bucket": pa.array([t0, t0 + timedelta(seconds=1)], type=pa.timestamp("us")),
            "chamber_pressure": pa.array([1.0, 2.0], type=pa.float64()),
        }
    )
    b = pa.Table.from_pydict(
        {
            "bucket": pa.array(
                [t0 + timedelta(seconds=1), t0 + timedelta(seconds=2)],
                type=pa.timestamp("us"),
            ),
            "rf_power": pa.array([10.0, 20.0], type=pa.float64()),
        }
    )
    out = tmp_path / "chart.csv"
    write_chart_csv(out, {"tool_a": a, "tool_b": b})

    text = out.read_text(encoding="utf-8")
    lines = text.strip().split("\n")
    assert lines[0] == "ts,tool_a.chamber_pressure,tool_b.rf_power"
    assert lines[1] == "2026-01-01T00:00:00+00:00,1.0,"
    assert lines[2] == "2026-01-01T00:00:01+00:00,2.0,10.0"
    assert lines[3] == "2026-01-01T00:00:02+00:00,,20.0"


def test_write_chart_csv_skips_query_errors(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    good = _chart_table([(t0, 1.0, 2.0)])
    out = tmp_path / "chart.csv"
    write_chart_csv(out, {"tool_a": good, "tool_b": QueryError(tool_id="tool_b", message="boom")})

    text = out.read_text(encoding="utf-8")
    assert "tool_b" not in text
    assert "tool_a.chamber_pressure" in text


def test_write_chart_csv_empty_results_writes_only_ts_header(tmp_path: Path) -> None:
    out = tmp_path / "chart.csv"
    write_chart_csv(out, {})
    assert out.read_text(encoding="utf-8") == "ts\n"


def _write_parquet(
    path: Path,
    *,
    start: datetime,
    n_rows: int,
    hz: int = 10,
) -> None:
    naive_start = start.astimezone(UTC).replace(tzinfo=None)
    timestamps = [naive_start + timedelta(seconds=i / hz) for i in range(n_rows)]
    rng = np.random.default_rng(seed=1)
    pq.write_table(
        pa.Table.from_pydict(
            {
                "ts": pa.array(timestamps, type=pa.timestamp("ns")),
                "chamber_pressure": pa.array(rng.random(n_rows), type=pa.float64()),
            }
        ),
        path,
    )


def _table_plan(parquet: Path, start: datetime, seconds: int = 60) -> QueryPlan:
    return QueryPlan(
        per_tool_queries=[
            ToolQuery(
                tool_id="a",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=seconds)),
            )
        ],
        target_resolution=timedelta(seconds=1),
        partial_data_warnings=[],
    )


def test_write_table_csv_streams_full_result(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "a.parquet"
    _write_parquet(parquet, start=start, n_rows=600, hz=10)
    plan = _table_plan(parquet, start)

    out = tmp_path / "table.csv"
    write_table_csv(out, plan, RawQueryEngine(), page_size=250)

    text = out.read_text(encoding="utf-8")
    lines = text.strip().split("\n")
    assert lines[0] == "tool_id,ts,chamber_pressure"
    assert len(lines) == 601  # header + 600 rows
    assert lines[1].startswith("a,2026-01-01T00:00:00")
    assert lines[-1].startswith("a,2026-01-01T00:00:59.9")


def test_write_table_csv_empty_plan_writes_header_only(tmp_path: Path) -> None:
    out = tmp_path / "table.csv"
    plan = QueryPlan(
        per_tool_queries=[],
        target_resolution=timedelta(seconds=1),
        partial_data_warnings=[],
    )
    write_table_csv(out, plan, RawQueryEngine())
    text = out.read_text(encoding="utf-8")
    assert text == "tool_id,ts\n"


def test_write_table_csv_header_is_alphabetically_sorted_union(tmp_path: Path) -> None:
    # Two tools, overlapping sensor sets; header must include the alphabetical
    # union of raw_columns and start with the fixed tool_id, ts columns.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    parquet = tmp_path / "a.parquet"
    _write_parquet(parquet, start=start, n_rows=5, hz=1)
    plan = QueryPlan(
        per_tool_queries=[
            ToolQuery(
                tool_id="a",
                file_paths=(parquet,),
                raw_columns=("chamber_pressure",),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=60)),
            ),
            ToolQuery(
                tool_id="b",
                file_paths=(),
                raw_columns=("z_sensor", "alpha_sensor"),
                time_range=TimeRange(start=start, end=start + timedelta(seconds=60)),
            ),
        ],
        target_resolution=timedelta(seconds=1),
        partial_data_warnings=[],
    )
    out = tmp_path / "table.csv"
    write_table_csv(out, plan, RawQueryEngine())
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert header == "tool_id,ts,alpha_sensor,chamber_pressure,z_sensor"


pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


def test_main_window_export_csv_action_is_present(qapp, tmp_path: Path, monkeypatch) -> None:  # noqa: E501
    from local_equs_client.data_layer.local_library import LocalLibrary
    from local_equs_client.data_layer.metadata_cache import MetadataCache
    from local_equs_client.data_layer.query_controller import QueryController
    from local_equs_client.data_layer.query_engine import QueryEngine
    from local_equs_client.data_layer.query_planner import QueryPlanner
    from local_equs_client.selection.selection_model import SelectionModel
    from local_equs_client.state import db
    from local_equs_client.ui.main_window import MainWindow

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    library = LocalLibrary(data_dir, conn)
    cache = MetadataCache(library)
    model = SelectionModel()
    controller = QueryController(model, QueryPlanner(library), QueryEngine())

    window = MainWindow(model, library, cache, controller)
    actions: list[str] = []
    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if menu is None:
            continue
        for a in menu.actions():
            actions.append(a.text())
    assert any("Export CSV" in t for t in actions)
    conn.close()
