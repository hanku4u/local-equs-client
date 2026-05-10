"""Unit tests for ``local_equs_client.ui.sensor_picker`` (C1.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from local_equs_client.data_layer.local_library import LocalLibrary  # noqa: E402
from local_equs_client.data_layer.metadata_cache import MetadataCache  # noqa: E402
from local_equs_client.selection.selection_model import SelectionModel  # noqa: E402
from local_equs_client.state import db  # noqa: E402
from local_equs_client.ui.sensor_picker import SensorPicker  # noqa: E402


def _write_parquet(
    path: Path,
    *,
    sensor_columns: dict[str, str | None],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    naive = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    timestamps = [naive + timedelta(seconds=i) for i in range(50)]
    fields: list[pa.Field] = [pa.field("ts", pa.timestamp("ns"))]
    arrays: list[pa.Array] = [pa.array(timestamps, type=pa.timestamp("ns"))]
    rng = np.random.default_rng(seed=1)
    for name, units in sensor_columns.items():
        meta = {b"units": units.encode("utf-8")} if units else None
        fields.append(pa.field(name, pa.float64(), metadata=meta))
        arrays.append(pa.array(rng.random(50), type=pa.float64()))
    pq.write_table(pa.Table.from_arrays(arrays, schema=pa.schema(fields)), path)


@pytest.fixture
def picker_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    library = LocalLibrary(data_dir, conn)
    yield library, MetadataCache(library), data_dir
    conn.close()


def test_empty_library_shows_empty_list(qapp, picker_env) -> None:
    library, cache, _ = picker_env
    picker = SensorPicker(SelectionModel(), library, cache)
    assert picker._list.count() == 0  # noqa: SLF001


def test_populates_from_library(qapp, picker_env) -> None:
    library, cache, data_dir = picker_env
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": "torr", "rf_power": None},
    )
    library.scan()

    picker = SensorPicker(SelectionModel(), library, cache)
    labels = [picker._list.item(i).text() for i in range(picker._list.count())]  # noqa: SLF001
    assert "etch_a1: chamber_pressure (torr)" in labels
    assert "etch_a1: rf_power" in labels


def test_filter_hides_non_matching_items(qapp, picker_env) -> None:
    library, cache, data_dir = picker_env
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None, "rf_power": None},
    )
    library.scan()

    picker = SensorPicker(SelectionModel(), library, cache)
    picker._filter_edit.setText("rf")  # noqa: SLF001
    picker._apply_filter()  # noqa: SLF001 — bypass debounce

    visible = [
        picker._list.item(i).text()  # noqa: SLF001
        for i in range(picker._list.count())  # noqa: SLF001
        if not picker._list.item(i).isHidden()  # noqa: SLF001
    ]
    assert visible == ["etch_a1: rf_power"]


def test_selection_pushes_to_model(qapp, picker_env) -> None:
    library, cache, data_dir = picker_env
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None},
    )
    library.scan()

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)
    picker._list.item(0).setSelected(True)  # noqa: SLF001

    assert model.tools == ("etch_a1",)
    assert model.sensors_raw == ("chamber_pressure",)


def test_clear_button_empties_selection(qapp, picker_env) -> None:
    library, cache, data_dir = picker_env
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None},
    )
    library.scan()

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)
    picker._list.item(0).setSelected(True)  # noqa: SLF001
    picker._on_clear()  # noqa: SLF001

    assert model.tools == ()
    assert model.sensors_raw == ()


def test_external_model_change_syncs_selection(qapp, picker_env) -> None:
    library, cache, data_dir = picker_env
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None, "rf_power": None},
    )
    library.scan()

    model = SelectionModel()
    picker = SensorPicker(model, library, cache)

    model.set_tools(("etch_a1",))
    model.set_sensors_raw(("rf_power",))

    selected = [it.text() for it in picker._list.selectedItems()]  # noqa: SLF001
    assert selected == ["etch_a1: rf_power"]
