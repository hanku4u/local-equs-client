"""Unit tests for ``local_equs_client.ui.sensor_picker`` (C1.4 → C3.3 tree mode)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt  # noqa: E402

from local_equs_client.data_layer.local_library import LocalLibrary  # noqa: E402
from local_equs_client.data_layer.metadata_cache import MetadataCache  # noqa: E402
from local_equs_client.selection.selection_model import SelectionModel  # noqa: E402
from local_equs_client.state import db  # noqa: E402
from local_equs_client.state.dao import metadata as metadata_dao  # noqa: E402
from local_equs_client.ui.sensor_picker import SensorPicker  # noqa: E402


def _write_parquet(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    naive = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    timestamps = [naive + timedelta(seconds=i) for i in range(50)]
    rng = np.random.default_rng(seed=1)
    table = pa.Table.from_pydict(
        {
            "ts": pa.array(timestamps, type=pa.timestamp("ns")),
            "PCham_torr": pa.array(rng.random(50), type=pa.float64()),
        }
    )
    pq.write_table(table, path)


@pytest.fixture
def picker_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    library = LocalLibrary(data_dir, conn)
    cache = MetadataCache(library, conn=conn)
    yield library, cache, conn, data_dir
    conn.close()


def _seed_metadata(
    conn,
    *,
    prc_group: str = "etcher",
    canonicals: list[dict] | None = None,
    mappings: list[dict] | None = None,
    categories: list[dict] | None = None,
) -> None:
    if canonicals is None:
        canonicals = [
            {"name": "chamber_pressure", "units": "torr", "category_id": "process"},
            {"name": "rf_power", "units": "W", "category_id": "rf"},
        ]
    if mappings is None:
        mappings = [
            {
                "tool_id": "etch_a1",
                "canonical_name": "chamber_pressure",
                "raw_name": "PCham_torr",
            },
            {"tool_id": "etch_a1", "canonical_name": "rf_power", "raw_name": "RFwd"},
        ]
    if categories is None:
        categories = [
            {"id": "process", "name": "Process", "parent_id": None},
            {"id": "rf", "name": "RF", "parent_id": None},
        ]
    metadata_dao.store_canonical_sensors(
        conn, prc_group, {"sensors": canonicals}, '"v1"'
    )
    metadata_dao.store_mappings(
        conn,
        prc_group,
        {"prc_group_id": prc_group, "mappings": mappings},
        '"v1"',
    )
    metadata_dao.store_categories(conn, {"categories": categories}, '"v1"')


# --- Tree structure --------------------------------------------------------


def test_empty_library_shows_no_tools(qapp, picker_env) -> None:
    library, cache, _conn, _data_dir = picker_env
    picker = SensorPicker(SelectionModel(), library, cache)
    assert picker._tree.topLevelItemCount() == 0  # noqa: SLF001


def test_tool_with_metadata_renders_categories_and_sensors(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    picker = SensorPicker(SelectionModel(), library, cache)
    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    assert tool_item.text(0).startswith("etch_a1 (0/2)")

    category_names = {tool_item.child(i).text(0) for i in range(tool_item.childCount())}
    assert category_names == {"Process", "RF"}


def test_tool_without_metadata_appears_with_no_children(qapp, picker_env) -> None:
    library, cache, _conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_unknown.parquet")
    library.scan()

    picker = SensorPicker(SelectionModel(), library, cache)
    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    assert tool_item.text(0) == "etch_unknown"
    assert tool_item.childCount() == 0


# --- Selection sync --------------------------------------------------------


def _checkable_leaf(picker: SensorPicker, tool_idx: int, cat_idx: int, leaf_idx: int):
    tool_item = picker._tree.topLevelItem(tool_idx)  # noqa: SLF001
    assert tool_item is not None
    cat_item = tool_item.child(cat_idx)
    assert cat_item is not None
    leaf = cat_item.child(leaf_idx)
    assert leaf is not None
    return leaf


def test_check_leaf_pushes_to_selection_model(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)

    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    process_idx = next(
        i
        for i in range(tool_item.childCount())
        if tool_item.child(i) is not None and tool_item.child(i).text(0) == "Process"
    )
    leaf = _checkable_leaf(picker, 0, process_idx, 0)
    leaf.setCheckState(0, Qt.CheckState.Checked)

    assert model.tools == ("etch_a1",)
    assert model.sensors_canonical == ("chamber_pressure",)


def test_check_tool_propagates_to_all_descendants(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)
    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    tool_item.setCheckState(0, Qt.CheckState.Checked)

    assert model.tools == ("etch_a1",)
    assert set(model.sensors_canonical) == {"chamber_pressure", "rf_power"}


def test_clear_button_resets_selection(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)
    picker._tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)  # noqa: SLF001
    picker._on_clear()  # noqa: SLF001

    assert model.tools == ()
    assert model.sensors_canonical == ()


def test_external_model_change_checks_matching_leaves(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)

    model.set_tools(("etch_a1",))
    model.set_sensors_canonical(("rf_power",))

    rf_leaf = None
    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    for c_idx in range(tool_item.childCount()):
        cat = tool_item.child(c_idx)
        assert cat is not None
        if cat.text(0) == "RF":
            rf_leaf = cat.child(0)
    assert rf_leaf is not None
    assert rf_leaf.checkState(0) == Qt.CheckState.Checked


# --- Counts ---------------------------------------------------------------


def test_tool_count_reflects_selected_leaves(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)
    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None

    assert tool_item.text(0) == "etch_a1 (0/2)"

    # Check one leaf
    process_idx = next(
        i
        for i in range(tool_item.childCount())
        if tool_item.child(i).text(0) == "Process"
    )
    leaf = _checkable_leaf(picker, 0, process_idx, 0)
    leaf.setCheckState(0, Qt.CheckState.Checked)

    assert tool_item.text(0) == "etch_a1 (1/2)"


# --- Filter ----------------------------------------------------------------


def test_filter_hides_non_matching_branches(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    picker = SensorPicker(SelectionModel(), library, cache)
    picker._filter_edit.setText("rf")  # noqa: SLF001
    picker._apply_filter()  # noqa: SLF001

    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    visible_categories: list[str] = []
    for c_idx in range(tool_item.childCount()):
        cat = tool_item.child(c_idx)
        if cat is not None and not cat.isHidden():
            visible_categories.append(cat.text(0))

    assert visible_categories == ["RF"]
