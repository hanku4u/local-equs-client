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


def test_tool_without_metadata_falls_back_to_raw_sensors(qapp, picker_env) -> None:
    """Offline mode: when no canonical metadata exists, list raw parquet columns."""
    library, cache, _conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_unknown.parquet")
    library.scan()

    picker = SensorPicker(SelectionModel(), library, cache)
    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    # Tool item now shows a (0/N) count because the raw fallback added leaves.
    assert tool_item.text(0).startswith("etch_unknown (0/")
    # Single synthetic category with the raw parquet columns underneath.
    assert tool_item.childCount() == 1
    cat = tool_item.child(0)
    assert cat is not None
    assert cat.text(0) == "All sensors (raw)"
    leaf_names = {
        cat.child(i).text(0)  # type: ignore[union-attr]
        for i in range(cat.childCount())
    }
    # _write_parquet creates one sensor column "PCham_torr"; ts is filtered out.
    assert any("PCham_torr" in n for n in leaf_names)


def test_raw_leaf_check_pushes_to_sensors_raw_not_canonical(
    qapp, picker_env
) -> None:
    library, cache, _conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_unknown.parquet")
    library.scan()

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)

    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    cat = tool_item.child(0)
    assert cat is not None
    leaf = cat.child(0)
    assert leaf is not None
    leaf.setCheckState(0, Qt.CheckState.Checked)

    assert model.tools == ("etch_unknown",)
    assert model.sensors_raw == ("PCham_torr",)
    assert model.sensors_canonical == ()


def test_external_sensors_raw_change_checks_raw_leaves(qapp, picker_env) -> None:
    library, cache, _conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_unknown.parquet")
    library.scan()

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)
    model.set_tools(("etch_unknown",))
    model.set_sensors_raw(("PCham_torr",))

    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    cat = tool_item.child(0)
    assert cat is not None
    leaf = cat.child(0)
    assert leaf is not None
    assert leaf.checkState(0) == Qt.CheckState.Checked


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


# --- C3.4: flat search results ---------------------------------------------


def test_results_list_hidden_when_filter_empty(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    picker = SensorPicker(SelectionModel(), library, cache)
    assert not picker._results_list.isVisible()  # noqa: SLF001
    assert picker._results_list.count() == 0  # noqa: SLF001


def test_filter_populates_results_with_breadcrumbs(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    picker = SensorPicker(SelectionModel(), library, cache)
    picker._filter_edit.setText("chamber")  # noqa: SLF001
    picker._apply_filter()  # noqa: SLF001

    items = [picker._results_list.item(i).text() for i in range(picker._results_list.count())]  # noqa: SLF001
    assert len(items) == 1
    assert "etch_a1" in items[0]
    assert "Process" in items[0]
    assert "chamber_pressure" in items[0]


def test_filter_matches_on_description(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(
        conn,
        canonicals=[
            {
                "name": "chamber_pressure",
                "description": "Process chamber absolute pressure",
                "units": "torr",
                "category_id": "process",
            }
        ],
        mappings=[
            {
                "tool_id": "etch_a1",
                "canonical_name": "chamber_pressure",
                "raw_name": "PCham_torr",
            }
        ],
    )

    picker = SensorPicker(SelectionModel(), library, cache)
    picker._filter_edit.setText("absolute")  # noqa: SLF001
    picker._apply_filter()  # noqa: SLF001

    assert picker._results_list.count() == 1  # noqa: SLF001


def test_clicking_a_result_toggles_tree_leaf(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)
    picker._filter_edit.setText("chamber")  # noqa: SLF001
    picker._apply_filter()  # noqa: SLF001
    result = picker._results_list.item(0)  # noqa: SLF001
    assert result is not None

    picker._on_result_clicked(result)  # noqa: SLF001

    assert model.sensors_canonical == ("chamber_pressure",)


# --- C3.5: hover detail pane ----------------------------------------------


def test_hover_updates_detail_pane(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(
        conn,
        canonicals=[
            {
                "name": "chamber_pressure",
                "description": "Process chamber absolute pressure",
                "units": "torr",
                "category_id": "process",
            }
        ],
        mappings=[
            {
                "tool_id": "etch_a1",
                "canonical_name": "chamber_pressure",
                "raw_name": "PCham_torr",
            }
        ],
    )

    picker = SensorPicker(SelectionModel(), library, cache)
    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    cat = tool_item.child(0)
    assert cat is not None
    leaf = cat.child(0)
    assert leaf is not None

    picker._on_item_hovered(leaf, 0)  # noqa: SLF001

    assert "chamber_pressure" in picker._detail_name.text()  # noqa: SLF001
    assert "etch_a1" in picker._detail_name.text()  # noqa: SLF001
    assert "absolute pressure" in picker._detail_description.text()  # noqa: SLF001
    assert "torr" in picker._detail_units.text()  # noqa: SLF001
    assert "Local files: 1" in picker._detail_files.text()  # noqa: SLF001
    assert "Local range:" in picker._detail_range.text()  # noqa: SLF001


def test_hovering_a_category_does_not_change_detail(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    picker = SensorPicker(SelectionModel(), library, cache)
    initial = picker._detail_name.text()  # noqa: SLF001

    tool_item = picker._tree.topLevelItem(0)  # noqa: SLF001
    assert tool_item is not None
    cat = tool_item.child(0)
    assert cat is not None
    picker._on_item_hovered(cat, 0)  # noqa: SLF001

    assert picker._detail_name.text() == initial  # noqa: SLF001


# --- C3.6: saved sets section -----------------------------------------------


def test_saved_sets_section_visible(qapp, picker_env) -> None:
    library, cache, _conn, _data_dir = picker_env
    picker = SensorPicker(SelectionModel(), library, cache)
    assert picker._saved_sets_box.isVisible() or not picker._saved_sets_box.isHidden()  # noqa: SLF001


# --- C3.7: Selected (N) header -----------------------------------------------


def test_save_as_set_button_enabled(qapp, picker_env) -> None:
    library, cache, _conn, _data_dir = picker_env
    picker = SensorPicker(SelectionModel(), library, cache)
    # Button is enabled — no longer a M5 stub
    assert picker._save_as_set_btn.isEnabled()  # noqa: SLF001


def test_header_count_label_starts_at_zero(qapp, picker_env) -> None:
    library, cache, _conn, _data_dir = picker_env
    picker = SensorPicker(SelectionModel(), library, cache)
    assert picker._count_label.text() == "Selected (0)"  # noqa: SLF001


# --- C5.1: Saved Sets full CRUD -------------------------------------------


def test_saved_set_appears_in_list_after_save(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    model = SelectionModel()
    picker = SensorPicker(model, library, cache, conn=conn)

    # Simulate a selection in the model
    model.set_tools(("etch_a1",))
    model.set_sensors_canonical(("chamber_pressure",))

    # Directly call the internal save method to bypass the dialog
    from local_equs_client.state.dao import saved_sets as dao

    dao.create(conn, "My Set", ("etch_a1",), ("chamber_pressure",), ())
    picker._reload_saved_sets()  # noqa: SLF001

    assert picker._sets_list.count() == 1  # noqa: SLF001
    assert picker._sets_list.item(0).text() == "My Set"  # noqa: SLF001


def test_clicking_set_replaces_selection(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env
    _write_parquet(data_dir / "etch_a1.parquet")
    library.scan()
    _seed_metadata(conn)

    from local_equs_client.state.dao import saved_sets as dao

    dao.create(conn, "Preset", ("etch_a1",), ("rf_power",), ())

    model = SelectionModel()
    model.set_tools(("etch_a1",))
    model.set_sensors_canonical(("chamber_pressure",))

    picker = SensorPicker(model, library, cache, conn=conn)
    item = picker._sets_list.item(0)  # noqa: SLF001
    assert item is not None

    # Simulate plain click (no shift)
    picker._on_set_clicked(item)  # noqa: SLF001

    assert model.sensors_canonical == ("rf_power",)
    assert model.tools == ("etch_a1",)


def test_delete_set_removes_from_list(qapp, picker_env) -> None:
    library, cache, conn, data_dir = picker_env

    from local_equs_client.state.dao import saved_sets as dao

    dao.create(conn, "Temp", ("t1",), (), ())

    model = SelectionModel()
    picker = SensorPicker(model, library, cache, conn=conn)
    assert picker._sets_list.count() == 1  # noqa: SLF001

    set_id = picker._sets_list.item(0).data(  # type: ignore[union-attr]  # noqa: SLF001
        int(Qt.ItemDataRole.UserRole) + 10  # _SET_ID_ROLE
    )
    dao.delete(conn, set_id)
    picker._reload_saved_sets()  # noqa: SLF001

    assert picker._sets_list.count() == 0  # noqa: SLF001


def test_reload_reflects_db_state(qapp, picker_env) -> None:
    library, cache, conn, _data_dir = picker_env

    from local_equs_client.state.dao import saved_sets as dao

    picker = SensorPicker(SelectionModel(), library, cache, conn=conn)
    assert picker._sets_list.count() == 0  # noqa: SLF001

    dao.create(conn, "Late Add", ("t1",), (), ())
    picker._reload_saved_sets()  # noqa: SLF001

    assert picker._sets_list.count() == 1  # noqa: SLF001

