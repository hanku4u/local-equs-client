"""Caches sensor catalog, canonical sensors, categories, mappings (C1.3, C2.9, C3.1).

Source priority (M2):

1. The cached sensor payload from the server in ``cached_sensors``. Refreshed by
   :meth:`refresh_sensors`, which sends ``If-None-Match`` for conditional GETs.
2. The local parquet schema (the C1.3 fallback) when nothing is cached or the
   server isn't configured.

:meth:`sensors_for` is the read path — it never goes to the network. The picker
calls it on every refresh; explicit network refresh happens through
:meth:`refresh_sensors`, typically driven by the rescan menu action.
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
    """One sensor as known to the M1/M2 catalog."""

    raw_name: str
    units: str | None


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
        self._memo: dict[str, list[SensorInfo]] = {}

    # --- Public read path -------------------------------------------------

    def sensors_for(self, tool_id: str) -> list[SensorInfo]:
        """Return sensors for ``tool_id`` from the cache or local parquet."""
        cached = self._memo.get(tool_id)
        if cached is not None:
            return cached

        if self._conn is not None:
            payload, _etag = metadata_dao.load_sensors(self._conn, tool_id)
            if payload is not None:
                sensors = _parse_payload(payload)
                self._memo[tool_id] = sensors
                return sensors

        sensors = self._build_from_parquet(tool_id)
        self._memo[tool_id] = sensors
        return sensors

    def invalidate(self) -> None:
        self._memo.clear()

    # --- Network refresh --------------------------------------------------

    def refresh_sensors(self, tool_id: str) -> list[SensorInfo]:
        """Fetch the canonical sensor list from the server, ETag-aware.

        Falls back to the cached payload (with a stale-cache log) when the
        server can't be reached, and finally to the local parquet schema.
        """
        if self._http is None or self._conn is None:
            return self._refresh_from_parquet(tool_id)

        cached_payload, cached_etag = metadata_dao.load_sensors(self._conn, tool_id)
        headers = {"If-None-Match": cached_etag} if cached_etag else None
        path = f"/v1/sensors/{tool_id}.json"

        try:
            resp = self._http.get(path, headers=headers)
        except HttpError as exc:
            logger.warning("Sensors refresh failed for %s: %s", tool_id, exc)
            if cached_payload is not None:
                sensors = _parse_payload(cached_payload)
            else:
                sensors = self._build_from_parquet(tool_id)
            self._memo[tool_id] = sensors
            return sensors

        if resp.status_code == 304:
            if cached_payload is not None:
                sensors = _parse_payload(cached_payload)
                self._memo[tool_id] = sensors
                return sensors
            # 304 without a cache — refetch unconditionally.
            resp = self._http.get(path)

        payload = resp.json()
        new_etag = resp.headers.get("ETag")
        metadata_dao.store_sensors(self._conn, tool_id, payload, new_etag)
        sensors = _parse_payload(payload)
        self._memo[tool_id] = sensors
        return sensors

    # --- Parquet fallback -------------------------------------------------

    def _refresh_from_parquet(self, tool_id: str) -> list[SensorInfo]:
        sensors = self._build_from_parquet(tool_id)
        self._memo[tool_id] = sensors
        return sensors

    def _build_from_parquet(self, tool_id: str) -> list[SensorInfo]:
        for file in self._library.all_files():
            if file.tool_id == tool_id and not file.archived:
                return _read_columns(file.path)
        return []


def _parse_payload(payload: Any) -> list[SensorInfo]:
    """Translate the server's JSON sensor list into :class:`SensorInfo`."""
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
