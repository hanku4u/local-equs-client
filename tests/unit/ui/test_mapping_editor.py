"""Unit tests for ``local_equs_client.ui.mapping_editor`` (C3.8, C3.9)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtGui import QColor  # noqa: E402

from local_equs_client.data_layer.local_library import LocalLibrary  # noqa: E402
from local_equs_client.data_layer.metadata_cache import MetadataCache  # noqa: E402
from local_equs_client.state import db  # noqa: E402
from local_equs_client.state.dao import metadata as metadata_dao  # noqa: E402
from local_equs_client.ui.mapping_editor import MappingEditor  # noqa: E402

_EMPTY_BG = QColor(255, 220, 220)


def _seed(conn, prc_group: str = "etcher") -> None:
    metadata_dao.store_canonical_sensors(
        conn,
        prc_group,
        {
            "sensors": [
                {
                    "name": "chamber_pressure",
                    "description": "Process chamber absolute pressure",
                    "units": "torr",
                    "category_id": "process",
                },
                {"name": "rf_power", "units": "W", "category_id": "rf"},
            ]
        },
        '"v1"',
    )
    metadata_dao.store_mappings(
        conn,
        prc_group,
        {
            "prc_group_id": prc_group,
            "mappings": [
                {
                    "tool_id": "etch_a1",
                    "canonical_name": "chamber_pressure",
                    "raw_name": "PCham_torr",
                },
                {
                    "tool_id": "etch_a2",
                    "canonical_name": "chamber_pressure",
                    "raw_name": "PCham_v2",
                },
                # etch_a2 has no rf_power mapping → empty cell
                {
                    "tool_id": "etch_a1",
                    "canonical_name": "rf_power",
                    "raw_name": "RFwd",
                },
            ],
        },
        '"v1"',
    )
    metadata_dao.store_categories(
        conn,
        {
            "categories": [
                {"id": "process", "name": "Process", "parent_id": None},
                {"id": "rf", "name": "RF", "parent_id": None},
            ]
        },
        '"v1"',
    )


@pytest.fixture
def editor_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    library = LocalLibrary(data_dir, conn)
    cache = MetadataCache(library, conn=conn)
    yield cache, conn
    conn.close()


# --- Matrix view ----------------------------------------------------------


def test_no_metadata_shows_disabled_combo(qapp, editor_env) -> None:
    cache, _conn = editor_env
    editor = MappingEditor(cache)
    assert not editor._prc_group_combo.isEnabled()  # noqa: SLF001
    assert editor._table.rowCount() == 0  # noqa: SLF001


def test_matrix_lists_canonicals_as_rows_and_tools_as_columns(qapp, editor_env) -> None:
    cache, conn = editor_env
    _seed(conn)

    editor = MappingEditor(cache)
    row_labels = [
        editor._table.verticalHeaderItem(r).text() for r in range(editor._table.rowCount())  # noqa: SLF001
    ]
    col_labels = [
        editor._table.horizontalHeaderItem(c).text() for c in range(editor._table.columnCount())  # noqa: SLF001
    ]
    assert set(row_labels) == {"chamber_pressure", "rf_power"}
    assert set(col_labels) == {"etch_a1", "etch_a2"}


def test_cells_contain_raw_names_and_em_dash_for_missing(qapp, editor_env) -> None:
    cache, conn = editor_env
    _seed(conn)

    editor = MappingEditor(cache)
    # Find chamber_pressure row and etch_a1 col.
    row_labels = [
        editor._table.verticalHeaderItem(r).text() for r in range(editor._table.rowCount())  # noqa: SLF001
    ]
    col_labels = [
        editor._table.horizontalHeaderItem(c).text() for c in range(editor._table.columnCount())  # noqa: SLF001
    ]
    r_pcham = row_labels.index("chamber_pressure")
    r_rf = row_labels.index("rf_power")
    c_a1 = col_labels.index("etch_a1")
    c_a2 = col_labels.index("etch_a2")

    assert editor._table.item(r_pcham, c_a1).text() == "PCham_torr"  # noqa: SLF001
    assert editor._table.item(r_pcham, c_a2).text() == "PCham_v2"  # noqa: SLF001

    rf_a2_item = editor._table.item(r_rf, c_a2)  # noqa: SLF001 — empty cell
    assert rf_a2_item.text() == "—"
    assert rf_a2_item.background().color() == _EMPTY_BG


def test_cells_are_not_editable_in_m3(qapp, editor_env) -> None:
    cache, conn = editor_env
    _seed(conn)
    editor = MappingEditor(cache)
    from PySide6.QtCore import Qt

    item = editor._table.item(0, 0)  # noqa: SLF001
    assert not bool(item.flags() & Qt.ItemFlag.ItemIsEditable)


# --- Detail pane ---------------------------------------------------------


def test_selecting_row_populates_detail(qapp, editor_env) -> None:
    cache, conn = editor_env
    _seed(conn)

    editor = MappingEditor(cache)
    # Find chamber_pressure row index
    row_labels = [
        editor._table.verticalHeaderItem(r).text() for r in range(editor._table.rowCount())  # noqa: SLF001
    ]
    r = row_labels.index("chamber_pressure")
    editor._table.selectRow(r)  # noqa: SLF001

    assert editor._detail_name.text() == "chamber_pressure"  # noqa: SLF001
    assert "absolute pressure" in editor._detail_description.text()  # noqa: SLF001
    assert editor._detail_units.text() == "torr"  # noqa: SLF001
    body = editor._detail_mappings.text()  # noqa: SLF001
    assert "etch_a1 → PCham_torr" in body
    assert "etch_a2 → PCham_v2" in body


def test_categories_tab_is_disabled(qapp, editor_env) -> None:
    cache, _conn = editor_env
    editor = MappingEditor(cache)
    assert not editor._tabs.isTabEnabled(1)  # noqa: SLF001
    assert editor._tabs.tabText(1) == "Categories"  # noqa: SLF001


def test_audit_history_shows_m5_placeholder(qapp, editor_env) -> None:
    cache, _conn = editor_env
    editor = MappingEditor(cache)
    assert "M5" in editor._audit_label.text()  # noqa: SLF001
