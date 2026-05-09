"""C0.1 performance spike: parquet -> DuckDB -> PyQtGraph render measurement.

Run:
    pip install -e ".[spike]"
    python spike/spike_render.py            # measures and writes results.md, exits
    python spike/spike_render.py --show     # also keeps the window open

Targets (from issue #1):
    initial render (8 charts ~2000 points)  < 1.5 s
    zoom re-query                            < 500 ms
    peak RSS                                 < 500 MB

If any target is missed, stop M0 and reconsider the stack before C0.2.
"""

from __future__ import annotations

import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import psutil
import pyarrow as pa
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

# Local module
sys.path.insert(0, str(Path(__file__).parent))
from generate_data import ensure_dataset  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"
RESULTS_PATH = Path(__file__).parent / "results.md"
TARGET_POINTS = 2000

# 8 (tool, sensor) pairs spread across all 5 tools
SELECTIONS: list[tuple[str, str]] = [
    ("etch_a1", "chamber_pressure"),
    ("etch_a2", "rf_forward_power"),
    ("etch_b1", "mfc_cf4_actual"),
    ("etch_b2", "substrate_temp_zone1"),
    ("etch_c1", "esc_voltage"),
    ("etch_a1", "he_pressure_inner"),
    ("etch_b1", "oes_435nm"),
    ("etch_c1", "turbo_pump_speed"),
]


# --- Query helpers ----------------------------------------------------------

def _bucket_seconds(span_seconds: float, target_points: int) -> int:
    return max(1, int(round(span_seconds / target_points)))


def query_full_range(con: duckdb.DuckDBPyConnection, parquet_path: Path,
                     sensor: str, target_points: int = TARGET_POINTS) -> pa.Table:
    """Bucketed mean/min/max for `sensor` across the full extent of `parquet_path`."""
    t_min, t_max = con.execute(
        f"SELECT MIN(ts), MAX(ts) FROM read_parquet('{parquet_path.as_posix()}')"
    ).fetchone()
    span = (t_max - t_min).total_seconds()
    bucket = _bucket_seconds(span, target_points)
    sql = f"""
        SELECT
            time_bucket(INTERVAL '{bucket} seconds', ts) AS bucket,
            AVG("{sensor}") AS avg_val,
            MIN("{sensor}") AS min_val,
            MAX("{sensor}") AS max_val
        FROM read_parquet('{parquet_path.as_posix()}')
        GROUP BY bucket
        ORDER BY bucket
    """
    return con.execute(sql).to_arrow_table()


def query_in_range(con: duckdb.DuckDBPyConnection, parquet_path: Path, sensor: str,
                   t_start: datetime, t_end: datetime,
                   target_points: int = TARGET_POINTS) -> pa.Table:
    """Bucketed mean/min/max for `sensor` clipped to [t_start, t_end)."""
    span = (t_end - t_start).total_seconds()
    bucket = _bucket_seconds(span, target_points)
    sql = f"""
        SELECT
            time_bucket(INTERVAL '{bucket} seconds', ts) AS bucket,
            AVG("{sensor}") AS avg_val,
            MIN("{sensor}") AS min_val,
            MAX("{sensor}") AS max_val
        FROM read_parquet('{parquet_path.as_posix()}')
        WHERE ts >= ? AND ts < ?
        GROUP BY bucket
        ORDER BY bucket
    """
    # Pass tz-naive datetimes; parquet schema is TIMESTAMP_NS (no zone)
    return con.execute(
        sql, [t_start.replace(tzinfo=None), t_end.replace(tzinfo=None)]
    ).to_arrow_table()


def _unpack(table: pa.Table) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Convert an arrow query result to numpy x (seconds-since-epoch) + avg/min/max."""
    bucket = table.column("bucket").to_numpy(zero_copy_only=False)
    # bucket comes as datetime64; reinterpret as int64 ns then to float seconds
    x = bucket.astype("datetime64[ns]").astype(np.int64).astype(np.float64) / 1e9
    return (
        x,
        table.column("avg_val").to_numpy(zero_copy_only=False),
        table.column("min_val").to_numpy(zero_copy_only=False),
        table.column("max_val").to_numpy(zero_copy_only=False),
    )


# --- Spike orchestration ----------------------------------------------------

def _rss_mb(proc: psutil.Process) -> float:
    return proc.memory_info().rss / (1024 * 1024)


def _build_window() -> tuple[QtWidgets.QApplication, pg.GraphicsLayoutWidget]:
    pg.setConfigOptions(antialias=False, background="k", foreground="w")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = pg.GraphicsLayoutWidget(show=False, title="Local EQUS — C0.1 perf spike")
    win.resize(1600, 1000)
    return app, win


def run_spike() -> dict[str, Any]:
    proc = psutil.Process()
    rss_baseline = _rss_mb(proc)

    # ----- Step 1: ensure dataset --------------------------------------------------
    print("[1/4] Ensuring dataset ...")
    t0 = time.perf_counter()
    dataset_info = ensure_dataset(DATA_DIR)
    gen_seconds = time.perf_counter() - t0
    total_rows = sum(int(s["rows"]) for s in dataset_info)
    total_mb = sum(float(s["size_mb"]) for s in dataset_info)
    print(f"      {total_rows:,} rows across {len(dataset_info)} tools, "
          f"{total_mb:.1f} MB on disk ({gen_seconds:.1f}s)")

    # ----- Step 2: build window ---------------------------------------------------
    print("[2/4] Building Qt window ...")
    app, win = _build_window()

    con = duckdb.connect()

    # ----- Step 3: initial render -------------------------------------------------
    print(f"[3/4] Querying + plotting {len(SELECTIONS)} charts ...")
    win.show()
    app.processEvents()

    plots: list[pg.PlotItem] = []
    curve_handles: list[tuple[Any, Any, Any, str, str]] = []

    t_render_start = time.perf_counter()

    for i, (tool, sensor) in enumerate(SELECTIONS):
        row, col = divmod(i, 2)
        plot = win.addPlot(row=row, col=col, title=f"{tool} / {sensor}")
        plot.setLabel("bottom", "Time (s since epoch)")
        plot.showGrid(x=True, y=True, alpha=0.3)
        plot.enableAutoRange("y")

        path = DATA_DIR / f"{tool}.parquet"
        arrow_tbl = query_full_range(con, path, sensor)
        x, avg, mn, mx = _unpack(arrow_tbl)

        avg_curve = plot.plot(x, avg, pen=pg.mkPen("w", width=1.5))
        min_curve = plot.plot(x, mn, pen=pg.mkPen((180, 180, 220, 90), width=0.6))
        max_curve = plot.plot(x, mx, pen=pg.mkPen((180, 180, 220, 90), width=0.6))
        band = pg.FillBetweenItem(min_curve, max_curve, brush=(80, 120, 220, 40))
        plot.addItem(band)

        plots.append(plot)
        curve_handles.append((avg_curve, min_curve, max_curve, tool, sensor))

    # Link x-axes
    for p in plots[1:]:
        p.setXLink(plots[0])

    # Force the paint pass so timing reflects what the user actually sees
    app.processEvents()
    initial_render_ms = (time.perf_counter() - t_render_start) * 1000.0
    rss_after_initial = _rss_mb(proc)

    print(f"      initial render: {initial_render_ms:.1f} ms, RSS: {rss_after_initial:.1f} MB")

    # ----- Step 4: zoom re-query --------------------------------------------------
    print("[4/4] Simulating zoom (middle 20% of range) ...")
    full_x_range = plots[0].viewRange()[0]
    span = full_x_range[1] - full_x_range[0]
    zoom_x = (full_x_range[0] + span * 0.4, full_x_range[0] + span * 0.6)
    t_start_dt = datetime.fromtimestamp(zoom_x[0], tz=timezone.utc)
    t_end_dt = datetime.fromtimestamp(zoom_x[1], tz=timezone.utc)

    t_zoom_start = time.perf_counter()

    for plot, (avg_curve, min_curve, max_curve, tool, sensor) in zip(plots, curve_handles):
        plot.setXRange(*zoom_x, padding=0)
        path = DATA_DIR / f"{tool}.parquet"
        arrow_tbl = query_in_range(con, path, sensor, t_start_dt, t_end_dt)
        x, avg, mn, mx = _unpack(arrow_tbl)
        if x.size == 0:
            # No data in zoomed range (between lots) — keep prior data
            continue
        avg_curve.setData(x, avg)
        min_curve.setData(x, mn)
        max_curve.setData(x, mx)

    app.processEvents()
    zoom_ms = (time.perf_counter() - t_zoom_start) * 1000.0
    rss_after_zoom = _rss_mb(proc)
    peak_rss = max(rss_baseline, rss_after_initial, rss_after_zoom)

    print(f"      zoom re-query: {zoom_ms:.1f} ms, RSS: {rss_after_zoom:.1f} MB")
    print(f"      peak RSS: {peak_rss:.1f} MB")

    return {
        "dataset_info": dataset_info,
        "total_rows": total_rows,
        "total_mb": total_mb,
        "initial_render_ms": initial_render_ms,
        "zoom_ms": zoom_ms,
        "rss_baseline_mb": rss_baseline,
        "rss_after_initial_mb": rss_after_initial,
        "rss_after_zoom_mb": rss_after_zoom,
        "peak_rss_mb": peak_rss,
        "_app": app,
        "_window": win,
    }


# --- Results writer ---------------------------------------------------------

def _verdict(measured: float, target: float) -> str:
    return "✓ pass" if measured < target else "✗ fail"


def write_results(out: dict[str, Any]) -> None:
    initial_pass = out["initial_render_ms"] < 1500.0
    zoom_pass = out["zoom_ms"] < 500.0
    rss_pass = out["peak_rss_mb"] < 500.0
    overall_pass = initial_pass and zoom_pass and rss_pass

    lines: list[str] = []
    lines.append("# C0.1 — Performance Spike Results")
    lines.append("")
    lines.append(f"_Run: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"**{'PASS — proceed with C0.2' if overall_pass else 'FAIL — stop and reconsider stack before C0.2'}**")
    lines.append("")
    lines.append("## Measurements")
    lines.append("")
    lines.append("| Metric | Target | Measured | Result |")
    lines.append("| --- | --- | --- | --- |")
    lines.append(f"| Initial render (8 charts × ~2000 points) | < 1.5 s | "
                 f"{out['initial_render_ms']:.1f} ms | {_verdict(out['initial_render_ms'], 1500.0)} |")
    lines.append(f"| Zoom re-query (middle 20%) | < 500 ms | "
                 f"{out['zoom_ms']:.1f} ms | {_verdict(out['zoom_ms'], 500.0)} |")
    lines.append(f"| Peak RSS | < 500 MB | "
                 f"{out['peak_rss_mb']:.1f} MB | {_verdict(out['peak_rss_mb'], 500.0)} |")
    lines.append("")
    lines.append("## Memory profile")
    lines.append("")
    lines.append(f"- Baseline (before any work): {out['rss_baseline_mb']:.1f} MB")
    lines.append(f"- After initial render: {out['rss_after_initial_mb']:.1f} MB")
    lines.append(f"- After zoom re-query: {out['rss_after_zoom_mb']:.1f} MB")
    lines.append("")
    lines.append("## Dataset")
    lines.append("")
    lines.append(f"- Total rows: {out['total_rows']:,}")
    lines.append(f"- Total size on disk: {out['total_mb']:.1f} MB")
    lines.append("")
    lines.append("| Tool | Wafers | Rows | Size (MB) |")
    lines.append("| --- | --- | --- | --- |")
    for s in out["dataset_info"]:
        lines.append(f"| {s['tool_id']} | {s.get('wafers', '?')} | {int(s['rows']):,} | {s['size_mb']} |")
    lines.append("")
    lines.append("## Environment")
    lines.append("")
    lines.append(f"- Platform: {platform.platform()}")
    lines.append(f"- CPU: {platform.processor() or 'unknown'}")
    lines.append(f"- Python: {sys.version.split()[0]}")
    lines.append(f"- DuckDB: {duckdb.__version__}")
    lines.append(f"- pyarrow: {pa.__version__}")
    lines.append(f"- numpy: {np.__version__}")
    lines.append(f"- PyQtGraph: {pg.__version__}")
    lines.append("")

    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {RESULTS_PATH}")


def main() -> int:
    out = run_spike()
    write_results(out)

    if "--show" in sys.argv:
        # Keep the window open for visual inspection
        QtCore.QTimer.singleShot(0, lambda: print("Window open. Close it to exit."))
        return out["_app"].exec()

    return 0


if __name__ == "__main__":
    sys.exit(main())
