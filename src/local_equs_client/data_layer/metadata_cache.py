"""Caches sensor catalog, canonical sensors, categories, mappings (C1.3, C2.9, C3.1).

C1.3 read raw sensor names from parquet schemas. C2.9 layered the server's
``/v1/sensors/{tool_id}.json`` payload over that with a parquet fallback.

C3.1 (this revision) adds the metadata that the picker tree mode and the
mapping editor need:

- ``canonical_sensors(prc_group_id)`` — the server's normalized catalog for a
  process group.
- ``category_tree()`` — the global category hierarchy.
- ``mapping(tool_id, canonical_name)`` — the cross-tool lookup that lets the
  Query Planner expand a canonical selection into per-tool raw column names.

Every accessor is a pure cache read; ``refresh_*`` methods are the only
network calls. All caches use ETag for conditional GETs and fall back to the
last good payload (with a stale-cache log) when the server is offline.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from local_equs_client.data_layer.http import HttpClient, HttpError
from local_equs_client.data_layer.local_library import TIMESTAMP_COLUMN, LocalLibrary
from local_equs_client.state.dao import metadata as metadata_dao

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SensorInfo:
    """One sensor as known to the M1/M2 raw-name catalog."""

    raw_name: str
    units: str | None


@dataclass(frozen=True, slots=True)
class CanonicalSensor:
    """One canonical sensor in a process group's catalog."""

    name: str
    description: str | None
    units: str | None
    category_id: str | None


@dataclass(frozen=True, slots=True)
class Category:
    """One node in the global category tree. ``parent_id`` is None for roots."""

    id: str
    name: str
    parent_id: str | None


_UNITS_KEYS = (b"units", b"unit")


class MetadataCache:
    """In-memory cache layered over the SQLite cache + local parquet schema."""

    def __init__(
        self,
        library: LocalLibrary,
        conn: sqlite3.Connection | None = None,
        http: HttpClient | None = None,
    ) -> None:
        self._library = library
        self._conn = conn
        self._http = http
        self._memo_sensors: dict[str, list[SensorInfo]] = {}
        self._memo_canonical: dict[str, list[CanonicalSensor]] = {}
        self._memo_categories: list[Category] | None = None
        # (tool_id, canonical_name) -> raw_name. Lazily rebuilt from cache.
        self._memo_mapping: dict[tuple[str, str], str] | None = None

    # --- C2.9 raw sensor catalog -----------------------------------------

    def sensors_for(self, tool_id: str) -> list[SensorInfo]:
        """Return raw sensors for ``tool_id`` from the cache or local parquet."""
        cached = self._memo_sensors.get(tool_id)
        if cached is not None:
            return cached

        if self._conn is not None:
            payload, _etag = metadata_dao.load_sensors(self._conn, tool_id)
            if payload is not None:
                sensors = _parse_sensor_payload(payload)
                self._memo_sensors[tool_id] = sensors
                return sensors

        sensors = self._build_from_parquet(tool_id)
        self._memo_sensors[tool_id] = sensors
        return sensors

    def refresh_sensors(self, tool_id: str) -> list[SensorInfo]:
        """Fetch the raw sensor list for ``tool_id`` from the server."""
        if self._http is None or self._conn is None:
            return self._refresh_from_parquet(tool_id)

        cached_payload, cached_etag = metadata_dao.load_sensors(self._conn, tool_id)
        path = f"/v1/sensors/{tool_id}.json"
        payload = self._conditional_get(path, cached_etag, cached_payload)
        if payload is None:
            # Offline: prefer the last cached payload, then fall back to parquet.
            if cached_payload is not None:
                sensors = _parse_sensor_payload(cached_payload)
            else:
                sensors = self._build_from_parquet(tool_id)
            self._memo_sensors[tool_id] = sensors
            return sensors

        if payload is _USE_CACHED:
            sensors = _parse_sensor_payload(cached_payload)
        else:
            metadata_dao.store_sensors(self._conn, tool_id, payload, self._last_etag)
            sensors = _parse_sensor_payload(payload)

        self._memo_sensors[tool_id] = sensors
        return sensors

    def invalidate(self) -> None:
        """Drop every in-memory layer. Disk caches survive."""
        self._memo_sensors.clear()
        self._memo_canonical.clear()
        self._memo_categories = None
        self._memo_mapping = None

    # --- C3.1 canonical sensors ------------------------------------------

    def canonical_sensors(self, prc_group_id: str) -> list[CanonicalSensor]:
        cached = self._memo_canonical.get(prc_group_id)
        if cached is not None:
            return cached
        if self._conn is None:
            return []
        payload, _etag = metadata_dao.load_canonical_sensors(self._conn, prc_group_id)
        sensors = _parse_canonical_payload(payload)
        self._memo_canonical[prc_group_id] = sensors
        return sensors

    def refresh_canonical_sensors(self, prc_group_id: str) -> list[CanonicalSensor]:
        if self._http is None or self._conn is None:
            return self.canonical_sensors(prc_group_id)

        cached_payload, cached_etag = metadata_dao.load_canonical_sensors(
            self._conn, prc_group_id
        )
        path = f"/v1/process-groups/{prc_group_id}/canonical-sensors.json"
        payload = self._conditional_get(path, cached_etag, cached_payload)
        if payload is None:
            return self.canonical_sensors(prc_group_id)

        if payload is _USE_CACHED:
            sensors = _parse_canonical_payload(cached_payload)
        else:
            metadata_dao.store_canonical_sensors(
                self._conn, prc_group_id, payload, self._last_etag
            )
            sensors = _parse_canonical_payload(payload)

        self._memo_canonical[prc_group_id] = sensors
        return sensors

    # --- C3.1 categories -------------------------------------------------

    def category_tree(self) -> list[Category]:
        if self._memo_categories is not None:
            return self._memo_categories
        if self._conn is None:
            return []
        payload, _etag = metadata_dao.load_categories(self._conn)
        categories = _parse_categories_payload(payload)
        self._memo_categories = categories
        return categories

    def refresh_categories(self) -> list[Category]:
        if self._http is None or self._conn is None:
            return self.category_tree()

        cached_payload, cached_etag = metadata_dao.load_categories(self._conn)
        payload = self._conditional_get("/v1/categories.json", cached_etag, cached_payload)
        if payload is None:
            return self.category_tree()

        if payload is _USE_CACHED:
            categories = _parse_categories_payload(cached_payload)
        else:
            metadata_dao.store_categories(self._conn, payload, self._last_etag)
            categories = _parse_categories_payload(payload)

        self._memo_categories = categories
        return categories

    # --- C3.1 mappings ---------------------------------------------------

    def mapping(self, tool_id: str, canonical_name: str) -> str | None:
        index = self._mapping_index()
        return index.get((tool_id, canonical_name))

    def refresh_mappings(self, prc_group_id: str) -> Any:
        """Refresh the per-prc-group mappings payload. Returns the parsed body."""
        if self._http is None or self._conn is None:
            return None

        cached_payload, cached_etag = metadata_dao.load_mappings(self._conn, prc_group_id)
        path = f"/v1/process-groups/{prc_group_id}/mappings.json"
        payload = self._conditional_get(path, cached_etag, cached_payload)
        if payload is None:
            return cached_payload

        if payload is _USE_CACHED:
            result = cached_payload
        else:
            metadata_dao.store_mappings(self._conn, prc_group_id, payload, self._last_etag)
            result = payload

        self._memo_mapping = None  # rebuild on next read
        return result

    # --- Conditional GET helper ------------------------------------------

    def _conditional_get(
        self, path: str, cached_etag: str | None, cached_payload: Any
    ) -> Any:
        """Return parsed body, ``_USE_CACHED`` for 304, or ``None`` on offline."""
        assert self._http is not None
        headers = {"If-None-Match": cached_etag} if cached_etag else None
        try:
            resp = self._http.get(path, headers=headers)
        except HttpError as exc:
            logger.warning("Refresh failed for %s: %s", path, exc)
            self._last_etag = None
            return None

        if resp.status_code == 304:
            if cached_payload is None:
                # Server bug: 304 with no cache. Refetch unconditionally.
                resp = self._http.get(path)
            else:
                self._last_etag = cached_etag
                return _USE_CACHED

        body = resp.json()
        self._last_etag = resp.headers.get("ETag")
        return body

    # --- Parquet fallback (C1.3 / C2.9) ----------------------------------

    def _refresh_from_parquet(self, tool_id: str) -> list[SensorInfo]:
        sensors = self._build_from_parquet(tool_id)
        self._memo_sensors[tool_id] = sensors
        return sensors

    def _build_from_parquet(self, tool_id: str) -> list[SensorInfo]:
        for file in self._library.all_files():
            if file.tool_id == tool_id and not file.archived:
                return _read_columns(file.path)
        return []

    # --- Mapping index ---------------------------------------------------

    def _mapping_index(self) -> dict[tuple[str, str], str]:
        if self._memo_mapping is not None:
            return self._memo_mapping
        if self._conn is None:
            self._memo_mapping = {}
            return self._memo_mapping
        index: dict[tuple[str, str], str] = {}
        for payload in metadata_dao.all_mapping_payloads(self._conn):
            for entry in _parse_mapping_payload(payload):
                tool_id, canonical, raw = entry
                index[(tool_id, canonical)] = raw
        self._memo_mapping = index
        return index


# Sentinel for "use the cached payload as-is" — distinct from None which means
# "the server is unreachable; caller should fall back further".
_USE_CACHED: Any = object()


# --- Payload parsers --------------------------------------------------------


def _parse_sensor_payload(payload: Any) -> list[SensorInfo]:
    if not isinstance(payload, list):
        return []
    sensors: list[SensorInfo] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        units_raw = entry.get("units")
        units = units_raw if isinstance(units_raw, str) and units_raw else None
        sensors.append(SensorInfo(raw_name=name, units=units))
    return sensors


def _parse_canonical_payload(payload: Any) -> list[CanonicalSensor]:
    if not isinstance(payload, dict):
        return []
    raw_sensors = payload.get("sensors")
    if not isinstance(raw_sensors, list):
        return []
    out: list[CanonicalSensor] = []
    for entry in raw_sensors:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        out.append(
            CanonicalSensor(
                name=name,
                description=_optional_str(entry.get("description")),
                units=_optional_str(entry.get("units")),
                category_id=_optional_str(entry.get("category_id")),
            )
        )
    return out


def _parse_categories_payload(payload: Any) -> list[Category]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("categories")
    if not isinstance(raw, list):
        return []
    out: list[Category] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        name = entry.get("name")
        if not isinstance(cid, str) or not isinstance(name, str):
            continue
        out.append(Category(id=cid, name=name, parent_id=_optional_str(entry.get("parent_id"))))
    return out


def _parse_mapping_payload(payload: Any) -> list[tuple[str, str, str]]:
    """Translate one prc_group mapping payload into ``(tool_id, canonical, raw)`` tuples."""
    if not isinstance(payload, dict):
        return []
    entries = payload.get("mappings")
    if not isinstance(entries, list):
        return []
    out: list[tuple[str, str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tool_id = entry.get("tool_id")
        canonical = entry.get("canonical_name")
        raw = entry.get("raw_name")
        if not isinstance(tool_id, str) or not isinstance(canonical, str):
            continue
        if not isinstance(raw, str) or not raw:
            continue
        out.append((tool_id, canonical, raw))
    return out


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


# --- Parquet-side helpers (C1.3) -------------------------------------------


def _read_columns(path: Path) -> list[SensorInfo]:
    schema = pq.read_schema(str(path))  # type: ignore[no-untyped-call]
    sensors: list[SensorInfo] = []
    for i in range(len(schema)):
        field = schema.field(i)
        if field.name == TIMESTAMP_COLUMN:
            continue
        sensors.append(SensorInfo(raw_name=field.name, units=_extract_units(field)))
    return sensors


def _extract_units(field: pa.Field) -> str | None:
    metadata = field.metadata
    if not metadata:
        return None
    for key in _UNITS_KEYS:
        if key in metadata:
            value: str = metadata[key].decode("utf-8")
            return value
    return None
