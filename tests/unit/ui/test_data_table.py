"""Unit tests for ``local_equs_client.ui.data_table`` (C5.2)."""

from __future__ import annotations

import pyarrow as pa  # noqa: F401  # pre-staged for Task 7 set_page tests
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
