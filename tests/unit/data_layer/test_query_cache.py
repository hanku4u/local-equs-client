"""Unit tests for ``local_equs_client.data_layer.query_cache`` (C4.3)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa

from local_equs_client.data_layer.query_cache import CacheKey, QueryCache
from local_equs_client.data_layer.query_planner import ToolQuery
from local_equs_client.selection.types import TimeRange


def _make_table(rows: int = 100) -> pa.Table:
    rng = np.random.default_rng(seed=1)
    return pa.table(
        {
            "bucket": pa.array(np.arange(rows, dtype="int64"), type=pa.int64()),
            "v_avg": pa.array(rng.random(rows), type=pa.float64()),
        }
    )


def _make_key(
    tool: str = "etch_a1",
    columns: tuple[str, ...] = ("chamber_pressure",),
    start: datetime | None = None,
    end: datetime | None = None,
    resolution_s: float = 10.0,
) -> CacheKey:
    s = start or datetime(2026, 1, 1, tzinfo=UTC)
    e = end or s + timedelta(hours=1)
    return CacheKey(
        tool_id=tool,
        raw_columns=columns,
        range_start_ts=s.timestamp(),
        range_end_ts=e.timestamp(),
        resolution_seconds=resolution_s,
    )


# --- get / put -------------------------------------------------------------


def test_miss_returns_none() -> None:
    assert QueryCache().get(_make_key()) is None


def test_put_then_get_returns_same_table() -> None:
    cache = QueryCache()
    key = _make_key()
    table = _make_table()
    cache.put(key, table)
    assert cache.get(key) is table


def test_different_resolution_is_a_different_key() -> None:
    cache = QueryCache()
    table = _make_table()
    cache.put(_make_key(resolution_s=10.0), table)
    assert cache.get(_make_key(resolution_s=60.0)) is None


def test_replacing_same_key_keeps_one_entry() -> None:
    cache = QueryCache()
    key = _make_key()
    cache.put(key, _make_table(rows=50))
    cache.put(key, _make_table(rows=200))
    assert len(cache) == 1


# --- LRU eviction ----------------------------------------------------------


def test_evicts_least_recently_used_when_over_cap() -> None:
    table = _make_table(rows=1000)
    # Cap holds two tables but not three, so the third put forces eviction.
    cap = int(table.nbytes * 2.5)
    cache = QueryCache(max_bytes=cap)

    k1 = _make_key(tool="a")
    k2 = _make_key(tool="b")
    k3 = _make_key(tool="c")

    cache.put(k1, _make_table(rows=1000))
    cache.put(k2, _make_table(rows=1000))
    assert cache.get(k1) is not None  # bump k1 to MRU
    cache.put(k3, _make_table(rows=1000))  # forces eviction

    assert cache.get(k2) is None  # LRU at insertion time → evicted
    assert cache.get(k1) is not None
    assert cache.get(k3) is not None


def test_oversized_entry_is_dropped() -> None:
    cache = QueryCache(max_bytes=10)  # absurdly small
    cache.put(_make_key(), _make_table(rows=100))
    assert len(cache) == 0


def test_clear_drops_everything() -> None:
    cache = QueryCache()
    cache.put(_make_key(tool="a"), _make_table())
    cache.put(_make_key(tool="b"), _make_table())
    cache.clear()
    assert len(cache) == 0
    assert cache.size_bytes == 0


# --- Key construction from ToolQuery --------------------------------------


def test_cache_key_from_tool_query_round_trips() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    tq = ToolQuery(
        tool_id="etch_a1",
        file_paths=(Path("/data/a.parquet"),),
        raw_columns=("chamber_pressure", "rf_power"),
        time_range=TimeRange(start=start, end=end),
    )
    key = CacheKey.from_tool_query(tq, resolution=timedelta(seconds=10))
    assert key.tool_id == "etch_a1"
    assert key.raw_columns == ("chamber_pressure", "rf_power")
    assert key.range_start_ts == start.timestamp()
    assert key.range_end_ts == end.timestamp()
    assert key.resolution_seconds == 10.0


# --- Thread safety smoke ---------------------------------------------------


def test_concurrent_puts_and_gets_dont_corrupt_state() -> None:
    cache = QueryCache(max_bytes=10 * 1024 * 1024)
    stop = threading.Event()

    def writer() -> None:
        for i in range(50):
            if stop.is_set():
                return
            cache.put(_make_key(tool=f"t{i}"), _make_table())

    def reader() -> None:
        while not stop.is_set():
            for i in range(50):
                cache.get(_make_key(tool=f"t{i}"))

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    stop.set()
    t2.join(timeout=5)
    # If size accounting is correct, current_bytes won't be negative or absurd.
    assert 0 <= cache.size_bytes <= cache._max_bytes  # noqa: SLF001
