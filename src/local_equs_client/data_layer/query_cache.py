"""LRU cache of (tool, sensors, range, resolution) -> ArrowTable (C4.3).

Sits between the :class:`QueryController` and :class:`QueryEngine`. The engine
consults the cache before running DuckDB; a hit short-circuits the SQL.
Entries are evicted least-recently-used first once the cumulative
``pyarrow.Table.nbytes`` exceeds the configured cap (default 200 MiB).

A mode switch that keeps the same time range but moves to a different
resolution misses on purpose — the cache key includes the resolution so the
new mode runs its own query at the new bucket size. A repeat of the *same*
``(mode, range)`` pair hits.

Thread-safe via an internal ``Lock``; get / put / clear are atomic.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from threading import Lock

import pyarrow as pa

from local_equs_client.data_layer.query_planner import ToolQuery

_DEFAULT_CAP_BYTES = 200 * 1024 * 1024  # 200 MiB


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Identity of one cached per-tool result."""

    tool_id: str
    raw_columns: tuple[str, ...]
    range_start_ts: float
    range_end_ts: float
    resolution_seconds: float

    @classmethod
    def from_tool_query(cls, tool_query: ToolQuery, resolution: timedelta) -> CacheKey:
        return cls(
            tool_id=tool_query.tool_id,
            raw_columns=tuple(tool_query.raw_columns),
            range_start_ts=tool_query.time_range.start.timestamp(),
            range_end_ts=tool_query.time_range.end.timestamp(),
            resolution_seconds=resolution.total_seconds(),
        )


class QueryCache:
    """Thread-safe LRU cache of Arrow tables sized in bytes."""

    def __init__(self, max_bytes: int = _DEFAULT_CAP_BYTES) -> None:
        self._max_bytes = max_bytes
        self._entries: OrderedDict[CacheKey, pa.Table] = OrderedDict()
        self._lock = Lock()
        self._current_bytes = 0

    def get(self, key: CacheKey) -> pa.Table | None:
        """Return the cached table for ``key`` (and refresh its LRU position), or None."""
        with self._lock:
            table = self._entries.get(key)
            if table is None:
                return None
            self._entries.move_to_end(key)
            return table

    def put(self, key: CacheKey, table: pa.Table) -> None:
        """Insert / replace the entry for ``key``. Oversized tables are dropped."""
        size = table.nbytes
        with self._lock:
            if size > self._max_bytes:
                # Caching a single table that exceeds the budget would force an
                # immediate evict; just skip it.
                if key in self._entries:
                    self._current_bytes -= self._entries[key].nbytes
                    del self._entries[key]
                return

            if key in self._entries:
                self._current_bytes -= self._entries[key].nbytes
                del self._entries[key]
            self._entries[key] = table
            self._current_bytes += size
            self._evict_if_needed()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._current_bytes = 0

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self._current_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _evict_if_needed(self) -> None:
        while self._current_bytes > self._max_bytes and self._entries:
            _key, evicted = self._entries.popitem(last=False)
            self._current_bytes -= evicted.nbytes


__all__ = ["CacheKey", "QueryCache"]
