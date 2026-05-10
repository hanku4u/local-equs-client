"""Caches sensor catalog, canonical sensors, categories, mappings (C1.3, C2.9, C3.1).

C1.3 (this task) is the M1 path: the catalog is built from the columns of the
local parquet files. Raw names only — canonical names land in M3 (C3.1) and
the server-sourced catalog in M2 (C2.9).

Callers must call :meth:`invalidate` after a Local Library scan to discard
stale per-tool entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from local_equs_client.data_layer.local_library import TIMESTAMP_COLUMN, LocalLibrary


@dataclass(frozen=True, slots=True)
class SensorInfo:
    """One sensor as known to the M1 catalog."""

    raw_name: str
    units: str | None


_UNITS_KEYS = (b"units", b"unit")


class MetadataCache:
    """In-memory cache of per-tool sensor lists derived from parquet schemas."""

    def __init__(self, library: LocalLibrary) -> None:
        self._library = library
        self._per_tool: dict[str, list[SensorInfo]] = {}

    def sensors_for(self, tool_id: str) -> list[SensorInfo]:
        """Return the sensors for ``tool_id``. Empty list when the tool has no files."""
        cached = self._per_tool.get(tool_id)
        if cached is not None:
            return cached
        sensors = self._build(tool_id)
        self._per_tool[tool_id] = sensors
        return sensors

    def invalidate(self) -> None:
        """Drop every cached entry. Call after a Local Library scan."""
        self._per_tool.clear()

    def _build(self, tool_id: str) -> list[SensorInfo]:
        for file in self._library.all_files():
            if file.tool_id == tool_id and not file.archived:
                return _read_columns(file.path)
        return []


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
