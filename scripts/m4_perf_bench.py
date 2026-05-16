#!/usr/bin/env python3
"""M4 performance benchmark (C4.11): 100 sensors x 8 tools = 800 series.

Generates synthetic parquet files, builds a ``QueryPlan`` through the real
``QueryPlanner`` + ``LocalLibrary``, and times execution through
``QueryEngine``. Reports plan, cold-cache, and warm-cache timings as JSON so
results can be pasted directly into ``docs/m4_performance.md``.

The UI side of the M4 exit criterion (placeholder layout starts within 1.5s
of plan emission; standard mode scrolls without dropped frames) requires a
real display and is verified manually on Windows — see
``docs/m4_performance.md``.

Usage:
    python scripts/m4_perf_bench.py
    python scripts/m4_perf_bench.py --tools 8 --sensors 100 --rows 6000 --runs 3
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.query_cache import QueryCache
from local_equs_client.data_layer.query_engine import QueryEngine
from local_equs_client.data_layer.query_planner import QueryPlanner
from local_equs_client.selection.types import Selection, TimeRange
from local_equs_client.state import db


def _write_parquet(
    path: Path, sensor_names: tuple[str, ...], n_rows: int, start: datetime, seed: int
) -> None:
    rng = np.random.default_rng(seed=seed)
    ts = pa.array(
        [start + timedelta(milliseconds=100 * i) for i in range(n_rows)],
        type=pa.timestamp("ns"),
    )
    columns: dict[str, pa.Array] = {"ts": ts}
    for name in sensor_names:
        columns[name] = pa.array(rng.random(n_rows, dtype=np.float32))
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pydict(columns), path, compression="zstd")


def _stats(samples: list[float]) -> dict[str, float]:
    s = sorted(samples)
    return {
        "min_s": round(s[0], 3),
        "median_s": round(s[len(s) // 2], 3),
        "max_s": round(s[-1], 3),
    }


def run_benchmark(*, tools: int, sensors: int, rows: int, runs: int) -> dict[str, Any]:
    sensor_names = tuple(f"sensor_{s:03d}" for s in range(sensors))
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = start + timedelta(milliseconds=100 * rows)

    with TemporaryDirectory() as raw:
        root = Path(raw)
        data_dir = root / "data"
        data_dir.mkdir()

        gen_t0 = time.perf_counter()
        tool_ids: list[str] = []
        for t in range(tools):
            tool_id = f"tool_{t:02d}"
            tool_ids.append(tool_id)
            file_path = data_dir / tool_id / "2026" / "05" / "01" / "00.parquet"
            _write_parquet(file_path, sensor_names, rows, start, seed=t)
        gen_seconds = time.perf_counter() - gen_t0

        conn = db.connect(root / "state.db")
        db.migrate(conn)
        library = LocalLibrary(data_dir, conn)

        index_t0 = time.perf_counter()
        indexed = library.scan()
        index_seconds = time.perf_counter() - index_t0

        selection = Selection(
            tools=tuple(tool_ids),
            sensors_canonical=(),
            sensors_raw=sensor_names,
            time_range=TimeRange(start=start, end=end),
        )
        planner = QueryPlanner(library)

        plan_samples: list[float] = []
        exec_cold_samples: list[float] = []
        exec_warm_samples: list[float] = []

        for _ in range(runs):
            cache = QueryCache()
            engine = QueryEngine(cache=cache)

            t0 = time.perf_counter()
            plan = planner.plan(selection, mode="overview", viewport_width_px=1400)
            plan_samples.append(time.perf_counter() - t0)

            t0 = time.perf_counter()
            engine.execute(plan)
            exec_cold_samples.append(time.perf_counter() - t0)

            t0 = time.perf_counter()
            engine.execute(plan)
            exec_warm_samples.append(time.perf_counter() - t0)

        total_series = tools * sensors
        return {
            "config": {
                "tools": tools,
                "sensors_per_tool": sensors,
                "rows_per_file": rows,
                "runs": runs,
            },
            "indexed_files": indexed,
            "total_series": total_series,
            "seed_generate_seconds": round(gen_seconds, 3),
            "library_index_seconds": round(index_seconds, 3),
            "plan_seconds": _stats(plan_samples),
            "execute_cold_seconds": _stats(exec_cold_samples),
            "execute_warm_seconds": _stats(exec_warm_samples),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=int, default=8)
    parser.add_argument("--sensors", type=int, default=100)
    parser.add_argument("--rows", type=int, default=6000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    report = run_benchmark(
        tools=args.tools, sensors=args.sensors, rows=args.rows, runs=args.runs
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
