"""Unit tests for ``local_equs_client.data_layer.metadata_cache`` (C1.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import MetadataCache, SensorInfo
from local_equs_client.state import db


def _write_parquet(
    path: Path,
    *,
    sensor_columns: dict[str, str | None],
    n_rows: int = 50,
) -> None:
    """Write a parquet with a ``ts`` column and the given sensors. ``units`` may be None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    naive_start = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    timestamps = [naive_start + timedelta(seconds=i) for i in range(n_rows)]
    rng = np.random.default_rng(seed=1)

    fields: list[pa.Field] = [pa.field("ts", pa.timestamp("ns"))]
    arrays: list[pa.Array] = [pa.array(timestamps, type=pa.timestamp("ns"))]
    for name, units in sensor_columns.items():
        metadata = {b"units": units.encode("utf-8")} if units else None
        fields.append(pa.field(name, pa.float64(), metadata=metadata))
        arrays.append(pa.array(rng.random(n_rows), type=pa.float64()))

    schema = pa.schema(fields)
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), path)


@pytest.fixture
def library(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    yield LocalLibrary(data_dir, conn), data_dir
    conn.close()


def test_sensors_for_returns_columns_excluding_ts(library) -> None:
    lib, data_dir = library
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": "torr", "rf_power": "W"},
    )
    lib.scan()

    cache = MetadataCache(lib)
    sensors = cache.sensors_for("etch_a1")

    by_name = {s.raw_name: s for s in sensors}
    assert set(by_name) == {"chamber_pressure", "rf_power"}
    assert by_name["chamber_pressure"].units == "torr"
    assert by_name["rf_power"].units == "W"


def test_sensors_for_units_none_when_metadata_absent(library) -> None:
    lib, data_dir = library
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None},
    )
    lib.scan()

    cache = MetadataCache(lib)
    sensors = cache.sensors_for("etch_a1")
    assert sensors == [SensorInfo(raw_name="chamber_pressure", units=None)]


def test_sensors_for_unknown_tool_returns_empty(library) -> None:
    lib, _ = library
    cache = MetadataCache(lib)
    assert cache.sensors_for("does_not_exist") == []


def test_cache_returns_same_list_on_hit(library) -> None:
    lib, data_dir = library
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None},
    )
    lib.scan()

    cache = MetadataCache(lib)
    first = cache.sensors_for("etch_a1")
    second = cache.sensors_for("etch_a1")
    assert first is second


def test_invalidate_clears_cache(library) -> None:
    lib, data_dir = library
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None},
    )
    lib.scan()

    cache = MetadataCache(lib)
    first = cache.sensors_for("etch_a1")
    cache.invalidate()
    second = cache.sensors_for("etch_a1")

    assert first is not second
    assert first == second


def test_invalidate_picks_up_new_sensors(library) -> None:
    lib, data_dir = library
    parquet = data_dir / "etch_a1.parquet"
    _write_parquet(parquet, sensor_columns={"chamber_pressure": None})
    lib.scan()

    cache = MetadataCache(lib)
    assert {s.raw_name for s in cache.sensors_for("etch_a1")} == {"chamber_pressure"}

    parquet.unlink()
    _write_parquet(
        parquet,
        sensor_columns={"chamber_pressure": None, "rf_power": "W"},
    )
    lib.scan()
    cache.invalidate()

    assert {s.raw_name for s in cache.sensors_for("etch_a1")} == {
        "chamber_pressure",
        "rf_power",
    }
