"""Unit tests for ``local_equs_client.ui.data_table`` (C5.2)."""

from __future__ import annotations

import pyarrow as pa
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt  # noqa: E402

from local_equs_client.ui.data_table import _PagedRawValuesModel  # noqa: E402


def test_model_starts_empty(qapp) -> None:
    model = _PagedRawValuesModel()
    assert model.rowCount() == 0
    assert model.columnCount() == 0


def test_set_columns_sets_column_count_and_headers(qapp) -> None:
    model = _PagedRawValuesModel()
    model.set_columns(("tool_id", "ts", "chamber_pressure"))
    assert model.columnCount() == 3
    h = Qt.Orientation.Horizontal
    d = Qt.ItemDataRole.DisplayRole
    assert model.headerData(0, h, d) == "tool_id"
    assert model.headerData(2, h, d) == "chamber_pressure"


def test_set_total_count_updates_row_count(qapp) -> None:
    model = _PagedRawValuesModel()
    model.set_columns(("tool_id", "ts"))
    model.set_total_count(1234)
    assert model.rowCount() == 1234


def _table(rows: list[dict[str, object]]) -> pa.Table:
    by_col: dict[str, list[object]] = {}
    for r in rows:
        for k, v in r.items():
            by_col.setdefault(k, []).append(v)
    return pa.Table.from_pydict(by_col)


def test_data_inside_loaded_page_returns_cell(qapp) -> None:
    model = _PagedRawValuesModel()
    model.set_columns(("tool_id", "ts", "chamber_pressure"))
    model.set_total_count(500)
    model.set_page(
        offset=0,
        page=_table(
            [
                {"tool_id": "a", "ts": "2026-01-01 00:00:00", "chamber_pressure": 1.5},
                {"tool_id": "a", "ts": "2026-01-01 00:00:01", "chamber_pressure": 2.5},
            ]
        ),
    )
    idx = model.index(0, 2)
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "1.5"


def test_data_outside_loaded_page_returns_placeholder(qapp) -> None:
    model = _PagedRawValuesModel()
    model.set_columns(("tool_id", "ts"))
    model.set_total_count(500)
    model.set_page(
        offset=0,
        page=_table([{"tool_id": "a", "ts": "2026-01-01 00:00:00"}]),
    )
    idx = model.index(400, 0)  # outside the loaded page
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "…"


def test_set_page_emits_dataChanged_for_page_range(qapp) -> None:
    model = _PagedRawValuesModel()
    model.set_columns(("tool_id", "ts"))
    model.set_total_count(500)
    captured: list[tuple[int, int]] = []
    model.dataChanged.connect(
        lambda tl, br, _roles=None: captured.append((tl.row(), br.row()))
    )
    model.set_page(
        offset=200,
        page=_table(
            [
                {"tool_id": "a", "ts": f"2026-01-01 00:00:{i:02d}"} for i in range(50)
            ]
        ),
    )
    assert captured == [(200, 249)]


from local_equs_client.ui.data_table import DataTableView  # noqa: E402


def test_view_starts_with_empty_selection_status(qapp) -> None:
    view = DataTableView()
    assert "Empty selection" in view.status_text()


def test_view_show_message_red_uses_error_style(qapp) -> None:
    view = DataTableView()
    view.show_error("Query failed: boom")
    assert "Query failed: boom" in view.status_text()
    style = view.status_label_style()
    # Any red-ish color is fine; we just check the error style was applied.
    assert "color" in style.lower()


from datetime import UTC, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery  # noqa: E402
from local_equs_client.selection.types import TimeRange  # noqa: E402


def _plan(per_tool: list[tuple[str, tuple[str, ...]]]) -> QueryPlan:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return QueryPlan(
        per_tool_queries=[
            ToolQuery(
                tool_id=t,
                file_paths=(Path(f"{t}.parquet"),),
                raw_columns=cols,
                time_range=TimeRange(start=start, end=start + timedelta(seconds=60)),
            )
            for t, cols in per_tool
        ],
        target_resolution=timedelta(seconds=1),
        partial_data_warnings=[],
    )


class _FakeEngine:
    def __init__(
        self, *, count_value: int = 0, page_table: pa.Table | None = None
    ) -> None:
        self.count_value = count_value
        self.page_table = page_table or pa.table({})
        self.count_calls: list[QueryPlan] = []
        self.fetch_calls: list[tuple[QueryPlan, int, int, str]] = []

    def count(self, plan, *, cancelled=None) -> int:
        self.count_calls.append(plan)
        return self.count_value

    def fetch_page(self, plan, *, offset, limit, order="asc", cancelled=None):
        self.fetch_calls.append((plan, offset, limit, order))
        return self.page_table


def test_set_plan_empty_keeps_empty_status(qapp) -> None:
    engine = _FakeEngine()
    view = DataTableView(engine=engine)
    view.set_plan(_plan([]))
    assert "Empty selection" in view.status_text()
    assert engine.count_calls == []
    assert engine.fetch_calls == []


def test_set_plan_non_empty_calls_count_then_first_page(qapp) -> None:
    page = pa.Table.from_pydict(
        {
            "tool_id": ["a"] * 3,
            "ts": ["2026-01-01"] * 3,
            "chamber_pressure": [1.0, 2.0, 3.0],
        }
    )
    engine = _FakeEngine(count_value=12345, page_table=page)
    view = DataTableView(engine=engine)
    view.set_plan(_plan([("a", ("chamber_pressure",))]))

    assert len(engine.count_calls) == 1
    assert engine.fetch_calls == [(engine.count_calls[0], 0, 200, "asc")]
    assert "Showing 1–" in view.status_text() or "Showing 1-" in view.status_text()
    assert "12,345" in view.status_text()


def test_set_plan_no_mapped_sensors_shows_friendly_status(qapp) -> None:
    engine = _FakeEngine()
    view = DataTableView(engine=engine)
    view.set_plan(_plan([("a", ())]))  # tool selected but no raw columns
    assert "No mapped sensors" in view.status_text()
    assert engine.count_calls == []
