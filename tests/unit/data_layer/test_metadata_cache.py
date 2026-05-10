"""Unit tests for ``local_equs_client.data_layer.metadata_cache`` (C1.3, C2.9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import responses

from local_equs_client.data_layer.http import HttpClient
from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import MetadataCache, SensorInfo
from local_equs_client.state import db
from local_equs_client.state.dao import metadata as metadata_dao

_SERVER = "https://equs.example.com"


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


# --- C2.9: server-sourced catalog ----------------------------------------


@pytest.fixture
def server_library(tmp_path: Path):
    """Library + connection + http client all wired together."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    library = LocalLibrary(data_dir, conn)
    http = HttpClient(_SERVER, "client-id-x", version="0.1.0")
    yield library, data_dir, conn, http
    conn.close()


@responses.activate
def test_refresh_sensors_fetches_from_server_and_caches(server_library) -> None:
    library, _data_dir, conn, http = server_library
    payload = [
        {"name": "chamber_pressure", "units": "torr"},
        {"name": "rf_power", "units": "W"},
    ]
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/sensors/etch_a1.json",
        json=payload,
        headers={"ETag": '"v1"'},
    )

    cache = MetadataCache(library, conn=conn, http=http)
    sensors = cache.refresh_sensors("etch_a1")

    assert {s.raw_name for s in sensors} == {"chamber_pressure", "rf_power"}
    cached_payload, cached_etag = metadata_dao.load_sensors(conn, "etch_a1")
    assert cached_payload == payload
    assert cached_etag == '"v1"'


@responses.activate
def test_refresh_sensors_uses_cached_etag_on_304(server_library) -> None:
    library, _data_dir, conn, http = server_library
    initial = [{"name": "chamber_pressure", "units": "torr"}]
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/sensors/etch_a1.json",
        json=initial,
        headers={"ETag": '"v1"'},
    )

    cache = MetadataCache(library, conn=conn, http=http)
    cache.refresh_sensors("etch_a1")

    responses.add(
        responses.GET, f"{_SERVER}/v1/sensors/etch_a1.json", status=304
    )
    sensors = cache.refresh_sensors("etch_a1")

    assert [s.raw_name for s in sensors] == ["chamber_pressure"]
    assert responses.calls[1].request.headers.get("If-None-Match") == '"v1"'


@responses.activate
def test_sensors_for_prefers_cache_over_parquet(server_library) -> None:
    library, data_dir, conn, http = server_library
    # Parquet has 'rf_power' and 'chamber_pressure'.
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None, "rf_power": None},
    )
    library.scan()
    # But the server says only 'chamber_pressure' is canonical.
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/sensors/etch_a1.json",
        json=[{"name": "chamber_pressure", "units": "torr"}],
        headers={"ETag": '"v1"'},
    )

    cache = MetadataCache(library, conn=conn, http=http)
    cache.refresh_sensors("etch_a1")
    cache.invalidate()  # drop in-memory layer; verify DB cache wins

    sensors = cache.sensors_for("etch_a1")
    assert [s.raw_name for s in sensors] == ["chamber_pressure"]


@responses.activate
def test_refresh_falls_back_to_cache_when_server_unreachable(server_library) -> None:
    library, _data_dir, conn, http = server_library
    payload = [{"name": "chamber_pressure", "units": "torr"}]
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/sensors/etch_a1.json",
        json=payload,
        headers={"ETag": '"v1"'},
    )
    cache = MetadataCache(library, conn=conn, http=http)
    cache.refresh_sensors("etch_a1")
    cache.invalidate()

    responses.replace(
        responses.GET,
        f"{_SERVER}/v1/sensors/etch_a1.json",
        body=responses.ConnectionError(),
    )

    sensors = cache.refresh_sensors("etch_a1")
    assert [s.raw_name for s in sensors] == ["chamber_pressure"]


@responses.activate
def test_refresh_falls_back_to_parquet_when_offline_no_cache(server_library) -> None:
    library, data_dir, conn, http = server_library
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None},
    )
    library.scan()

    responses.add(
        responses.GET,
        f"{_SERVER}/v1/sensors/etch_a1.json",
        body=responses.ConnectionError(),
    )

    cache = MetadataCache(library, conn=conn, http=http)
    sensors = cache.refresh_sensors("etch_a1")
    assert [s.raw_name for s in sensors] == ["chamber_pressure"]


def test_sensors_for_falls_back_to_parquet_when_no_http(library) -> None:
    """Without conn/http, behaves exactly like the C1.3 path (covered by earlier tests)."""
    lib, data_dir = library
    _write_parquet(
        data_dir / "etch_a1.parquet",
        sensor_columns={"chamber_pressure": None},
    )
    lib.scan()

    sensors = MetadataCache(lib).sensors_for("etch_a1")
    assert [s.raw_name for s in sensors] == ["chamber_pressure"]


def test_parse_payload_rejects_malformed_entries(library) -> None:
    """Server payload that's not a list-of-dicts shouldn't crash sensors_for."""
    from local_equs_client.data_layer.metadata_cache import _parse_sensor_payload

    assert _parse_sensor_payload(None) == []
    assert _parse_sensor_payload({"oops": "not a list"}) == []
    assert _parse_sensor_payload([1, "x", {"no_name_key": "x"}]) == []
    assert _parse_sensor_payload([{"name": "ok"}]) == [
        SensorInfo(raw_name="ok", units=None)
    ]


# --- C3.1: canonical sensors / categories / mappings ----------------------


@responses.activate
def test_refresh_canonical_sensors_caches_payload(server_library) -> None:
    library, _data_dir, conn, http = server_library
    payload = {
        "prc_group_id": "etcher",
        "sensors": [
            {
                "name": "chamber_pressure",
                "description": "Process chamber pressure",
                "units": "torr",
                "category_id": "process",
            },
            {"name": "rf_power"},
        ],
    }
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/process-groups/etcher/canonical-sensors.json",
        json=payload,
        headers={"ETag": '"v1"'},
    )

    cache = MetadataCache(library, conn=conn, http=http)
    sensors = cache.refresh_canonical_sensors("etcher")

    assert [s.name for s in sensors] == ["chamber_pressure", "rf_power"]
    assert sensors[0].units == "torr"
    assert sensors[0].category_id == "process"
    assert sensors[1].description is None
    assert sensors[1].category_id is None


@responses.activate
def test_canonical_sensors_reads_from_cache_when_offline(server_library) -> None:
    library, _data_dir, conn, http = server_library
    metadata_dao.store_canonical_sensors(
        conn,
        "etcher",
        {"sensors": [{"name": "chamber_pressure"}]},
        '"v1"',
    )

    cache = MetadataCache(library, conn=conn, http=http)
    sensors = cache.canonical_sensors("etcher")

    assert [s.name for s in sensors] == ["chamber_pressure"]


@responses.activate
def test_refresh_categories_caches_payload(server_library) -> None:
    library, _data_dir, conn, http = server_library
    payload = {
        "categories": [
            {"id": "process", "name": "Process", "parent_id": None},
            {"id": "vacuum", "name": "Vacuum", "parent_id": "process"},
        ],
    }
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/categories.json",
        json=payload,
        headers={"ETag": '"v1"'},
    )

    cache = MetadataCache(library, conn=conn, http=http)
    cats = cache.refresh_categories()

    by_id = {c.id: c for c in cats}
    assert set(by_id) == {"process", "vacuum"}
    assert by_id["vacuum"].parent_id == "process"
    assert by_id["process"].parent_id is None


@responses.activate
def test_refresh_categories_uses_etag_on_304(server_library) -> None:
    library, _data_dir, conn, http = server_library
    initial = {"categories": [{"id": "x", "name": "X", "parent_id": None}]}
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/categories.json",
        json=initial,
        headers={"ETag": '"v1"'},
    )

    cache = MetadataCache(library, conn=conn, http=http)
    cache.refresh_categories()

    responses.add(responses.GET, f"{_SERVER}/v1/categories.json", status=304)
    cache.invalidate()
    cats = cache.refresh_categories()

    assert [c.id for c in cats] == ["x"]
    assert responses.calls[1].request.headers.get("If-None-Match") == '"v1"'


@responses.activate
def test_refresh_mappings_lookup_by_tool_and_canonical(server_library) -> None:
    library, _data_dir, conn, http = server_library
    payload = {
        "prc_group_id": "etcher",
        "mappings": [
            {
                "tool_id": "etch_a1",
                "canonical_name": "chamber_pressure",
                "raw_name": "ChamberPressure_torr",
            },
            {
                "tool_id": "etch_a2",
                "canonical_name": "chamber_pressure",
                "raw_name": "PCham_torr",
            },
        ],
    }
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/process-groups/etcher/mappings.json",
        json=payload,
        headers={"ETag": '"v1"'},
    )

    cache = MetadataCache(library, conn=conn, http=http)
    cache.refresh_mappings("etcher")

    assert cache.mapping("etch_a1", "chamber_pressure") == "ChamberPressure_torr"
    assert cache.mapping("etch_a2", "chamber_pressure") == "PCham_torr"
    assert cache.mapping("etch_unknown", "chamber_pressure") is None
    assert cache.mapping("etch_a1", "no_such_canonical") is None


def test_mapping_index_reads_every_cached_prc_group(server_library) -> None:
    """A multi-prc_group install should pool every cached mapping into one lookup."""
    library, _data_dir, conn, http = server_library
    metadata_dao.store_mappings(
        conn,
        "etcher",
        {
            "mappings": [
                {
                    "tool_id": "etch_a1",
                    "canonical_name": "chamber_pressure",
                    "raw_name": "PCham",
                }
            ]
        },
        '"v1"',
    )
    metadata_dao.store_mappings(
        conn,
        "depo",
        {
            "mappings": [
                {
                    "tool_id": "depo_b1",
                    "canonical_name": "wall_temp",
                    "raw_name": "TWall",
                }
            ]
        },
        '"v1"',
    )

    cache = MetadataCache(library, conn=conn, http=http)
    assert cache.mapping("etch_a1", "chamber_pressure") == "PCham"
    assert cache.mapping("depo_b1", "wall_temp") == "TWall"


@responses.activate
def test_refresh_mappings_invalidates_in_memory_index(server_library) -> None:
    library, _data_dir, conn, http = server_library
    # Seed cache with one mapping.
    metadata_dao.store_mappings(
        conn,
        "etcher",
        {
            "mappings": [
                {
                    "tool_id": "etch_a1",
                    "canonical_name": "chamber_pressure",
                    "raw_name": "PCham_v1",
                }
            ]
        },
        '"v1"',
    )

    cache = MetadataCache(library, conn=conn, http=http)
    assert cache.mapping("etch_a1", "chamber_pressure") == "PCham_v1"

    # Server returns a different raw name.
    responses.add(
        responses.GET,
        f"{_SERVER}/v1/process-groups/etcher/mappings.json",
        json={
            "mappings": [
                {
                    "tool_id": "etch_a1",
                    "canonical_name": "chamber_pressure",
                    "raw_name": "PCham_v2",
                }
            ]
        },
        headers={"ETag": '"v2"'},
    )
    cache.refresh_mappings("etcher")
    assert cache.mapping("etch_a1", "chamber_pressure") == "PCham_v2"


def test_canonical_payload_parser_rejects_garbage() -> None:
    from local_equs_client.data_layer.metadata_cache import _parse_canonical_payload

    assert _parse_canonical_payload(None) == []
    assert _parse_canonical_payload({"sensors": "nope"}) == []
    assert _parse_canonical_payload({"sensors": [{"no_name": True}]}) == []


def test_categories_payload_parser_rejects_garbage() -> None:
    from local_equs_client.data_layer.metadata_cache import _parse_categories_payload

    assert _parse_categories_payload(None) == []
    assert _parse_categories_payload({"categories": "nope"}) == []
    assert _parse_categories_payload({"categories": [{"id": "x"}]}) == []  # missing name
