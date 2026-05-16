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
