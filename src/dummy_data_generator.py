#!/usr/bin/env python3
"""
Dummy etch-tool sensor data generator for Local EQUS development and testing.

Simulates 20 etch tools (4 prc groups x 5 tools), 100 sensors per tool, sampled
at 10 Hz only while wafers are being processed. Tools are idle between wafers
(~20s) and between lots (~5-45 min), with occasional maintenance windows.

By default writes one parquet file per (tool, hour) using the path layout the
Local EQUS server expects:

    {output}/{tool_id}/YYYY/MM/DD/HH.parquet  (UTC)

Each row is one 10 Hz timestamp; columns are `timestamp` plus 100 sensor
readings as float32. Hours where the tool collected zero samples produce no
file (which is the right semantics — gaps mean the tool wasn't running).

Requirements:
    pip install pyarrow numpy

Usage:
    python generate_dummy_data.py --output ./data --hours 24
    python generate_dummy_data.py --output ./data --start 2026-05-09T00:00:00 --hours 48
    python generate_dummy_data.py --output ./data --hours 4 --tools ETCH_T01,ETCH_T02
    python generate_dummy_data.py --output ./data --hours 8 --single-file   # one file per tool
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    sys.exit("pyarrow is required. Install with: pip install pyarrow")


# ---------------------------------------------------------------------------
# Tool / prc-group configuration
# ---------------------------------------------------------------------------

PRC_GROUPS: dict[str, list[str]] = {
    "etch_oxide":   [f"ETCH_T{i:02d}" for i in range(1, 6)],     # T01-T05
    "etch_metal":   [f"ETCH_T{i:02d}" for i in range(6, 11)],    # T06-T10
    "etch_poly":    [f"ETCH_T{i:02d}" for i in range(11, 16)],   # T11-T15
    "etch_nitride": [f"ETCH_T{i:02d}" for i in range(16, 21)],   # T16-T20
}

ALL_TOOLS: list[str] = [t for tools in PRC_GROUPS.values() for t in tools]
TOOL_PRC_GROUP: dict[str, str] = {t: g for g, ts in PRC_GROUPS.items() for t in ts}

assert len(ALL_TOOLS) == 20, "Expected exactly 20 tools"

SAMPLE_HZ = 10
SAMPLE_PERIOD_US = 100_000  # 100ms = 100,000 microseconds


# ---------------------------------------------------------------------------
# Sensor catalog (100 names per tool — typical etch instrumentation)
# ---------------------------------------------------------------------------

def build_sensor_names() -> list[str]:
    names: list[str] = []

    # Pressures (3)
    names += ["CHM_PRES_TORR", "CHM_PRES_MTORR", "FORELINE_PRES_TORR"]

    # RF generators (8) — two RF sources is common in etch
    for n in (1, 2):
        names += [f"RF{n}_FWD_PWR_W", f"RF{n}_REF_PWR_W",
                  f"RF{n}_VPP_V", f"RF{n}_DC_BIAS_V"]

    # Mass flow controllers (15 process gases)
    gases = ["AR", "O2", "CF4", "SF6", "CHF3", "C4F8", "HBR", "CL2",
             "N2", "HE", "BCL3", "NF3", "CH2F2", "H2", "C2F6"]
    names += [f"MFC_{g}_SCCM" for g in gases]

    # Temperatures (15 zones)
    zones = ["WALL", "LID", "ESC_INNER", "ESC_OUTER", "SHOWERHEAD",
             "LINER", "FOCUS_RING", "GAS_LINE", "PUMP", "CHILLER_IN",
             "CHILLER_OUT", "CATHODE", "ANODE", "DOME", "MAGNET"]
    names += [f"TEMP_{z}_C" for z in zones]

    # ESC + helium backside cooling (5)
    names += ["ESC_HV_V", "ESC_CURRENT_MA", "HE_BS_INNER_TORR",
              "HE_BS_OUTER_TORR", "HE_LEAK_SCCM"]

    # Valves (5)
    names += ["TV_POSITION_PCT", "GATE_VALVE_POS_PCT", "ISO_VALVE_POS_PCT",
              "VENT_VALVE_POS_PCT", "PURGE_VALVE_POS_PCT"]

    # Optical emission spectroscopy (10 wavelengths for endpoint)
    names += [f"OES_WL_{wl}NM" for wl in
              (387, 405, 440, 483, 520, 580, 656, 703, 777, 844)]

    # Plasma diagnostics (5)
    names += ["PLASMA_DENSITY", "ELECTRON_TEMP_EV", "ION_FLUX",
              "ION_ENERGY_EV", "VDC_PROBE_V"]

    # Match network (5)
    names += ["MATCH_LOAD_PCT", "MATCH_TUNE_PCT",
              "MATCH_C1_PF", "MATCH_C2_PF", "MATCH_REFLECTED_PCT"]

    # Recipe / state (4)
    names += ["RECIPE_STEP", "STEP_TIME_S", "WAFER_COUNT", "PROCESS_STATE"]

    # Vacuum (5)
    names += ["TURBO_RPM", "TURBO_CURRENT_A", "ROUGHING_PRES_TORR",
              "PUMP_TEMP_C", "PUMP_VIB_MM_S"]

    # Pad to 100 with auxiliary sensors
    while len(names) < 100:
        names.append(f"AUX_SENSOR_{len(names):03d}")

    return names[:100]


SENSORS: list[str] = build_sensor_names()
assert len(SENSORS) == 100


# ---------------------------------------------------------------------------
# Wafer-run pattern: when each tool is actually collecting data
# ---------------------------------------------------------------------------

def generate_wafer_runs(
    start: datetime,
    end: datetime,
    rng: random.Random,
) -> list[tuple[datetime, datetime]]:
    """
    Build a list of (run_start, run_end) windows representing wafers being
    processed. Tools are idle outside these windows.

    Pattern:
      - Inter-lot gap: 5-45 min normally, occasionally 4-12h (maintenance)
      - Lot of 12-25 wafers
      - Each wafer: 60-120 s of processing
      - 15-30 s transition between wafers (no data)

    Yields ~40-55% utilization on average — representative of a busy etcher
    that isn't running 24/7.
    """
    runs: list[tuple[datetime, datetime]] = []
    cursor = start

    while cursor < end:
        # Inter-lot gap
        if rng.random() < 0.04:
            # Occasional maintenance / extended idle
            cursor += timedelta(hours=rng.uniform(4, 12))
        else:
            cursor += timedelta(minutes=rng.uniform(5, 45))
        if cursor >= end:
            break

        # Process a lot
        lot_size = rng.randint(12, 25)
        for _ in range(lot_size):
            wafer_dur = timedelta(seconds=rng.uniform(60, 120))
            wafer_end = min(cursor + wafer_dur, end)
            runs.append((cursor, wafer_end))
            cursor = wafer_end
            if cursor >= end:
                break
            # Inter-wafer transition (load/unload, no data)
            cursor += timedelta(seconds=rng.uniform(15, 30))
            if cursor >= end:
                break

    return runs


# ---------------------------------------------------------------------------
# Sensor value synthesis
# ---------------------------------------------------------------------------

def synthesize_wafer_trace(
    sensor: str,
    n: int,
    tool_offset: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Plausible wafer-process trace for one sensor across n samples (10 Hz).

    Most signals follow a ramp-up / steady-state / ramp-down envelope keyed
    off the wafer length, with sensor-specific baseline, amplitude, and noise.
    `tool_offset` adds a small per-tool bias so charts of different tools
    don't overlay perfectly.
    """
    if n == 0:
        return np.zeros(0, dtype=np.float32)

    t = np.arange(n)
    # Trapezoidal envelope: ramp up over ~5s, hold, ramp down over ~5s
    ramp = np.minimum(t / 50.0, 1.0) * np.minimum((n - 1 - t) / 50.0, 1.0)
    ramp = np.clip(ramp, 0.0, 1.0)

    # Pressures
    if sensor == "CHM_PRES_TORR":
        return (0.05 + 0.005 * ramp + rng.normal(0, 0.0008, n)
                + tool_offset * 0.001).astype(np.float32)
    if sensor == "CHM_PRES_MTORR":
        return (50.0 + 5.0 * ramp + rng.normal(0, 0.4, n)
                + tool_offset * 1.0).astype(np.float32)
    if "FORELINE" in sensor:
        return (0.5 + 0.05 * ramp + rng.normal(0, 0.01, n)).astype(np.float32)

    # RF generators
    if "FWD_PWR" in sensor:
        peak = 1500.0 if sensor.startswith("RF1") else 800.0
        return (peak * ramp + rng.normal(0, 5.0, n) + tool_offset).astype(np.float32)
    if "REF_PWR" in sensor:
        peak = 30.0 if sensor.startswith("RF1") else 15.0
        return (peak * ramp * rng.uniform(0.3, 0.7) + rng.normal(0, 1.5, n)
                ).astype(np.float32)
    if "VPP" in sensor:
        return (1200.0 * ramp + rng.normal(0, 8.0, n)
                + tool_offset * 5).astype(np.float32)
    if "DC_BIAS" in sensor:
        return (-300.0 * ramp + rng.normal(0, 3.0, n)).astype(np.float32)

    # Mass flow controllers — step on/off following ramp
    if sensor.startswith("MFC_"):
        baseline_on = float(rng.uniform(20, 200))
        on_mask = (ramp > 0.1).astype(np.float32)
        return (on_mask * baseline_on + rng.normal(0, 0.5, n)).astype(np.float32)

    # Temperatures: slow drift around a baseline, small process bump
    if sensor.startswith("TEMP_"):
        zone = sensor[len("TEMP_"):-len("_C")]
        baseline_map = {
            "WALL": 65, "LID": 60, "ESC_INNER": 50, "ESC_OUTER": 50,
            "SHOWERHEAD": 80, "LINER": 70, "FOCUS_RING": 90, "GAS_LINE": 45,
            "PUMP": 55, "CHILLER_IN": 20, "CHILLER_OUT": 22, "CATHODE": 60,
            "ANODE": 65, "DOME": 75, "MAGNET": 35,
        }
        baseline = baseline_map.get(zone, 50.0)
        slow = 0.5 * np.sin(2 * np.pi * t / 600.0)
        return (baseline + slow + ramp * 2.0 + rng.normal(0, 0.1, n)
                + tool_offset).astype(np.float32)

    # ESC / helium backside
    if sensor == "ESC_HV_V":
        return (2500.0 * ramp + rng.normal(0, 5, n)).astype(np.float32)
    if sensor == "ESC_CURRENT_MA":
        return (1.5 * ramp + rng.normal(0, 0.05, n)).astype(np.float32)
    if "HE_BS" in sensor:
        return (10.0 * ramp + rng.normal(0, 0.1, n)).astype(np.float32)
    if "HE_LEAK" in sensor:
        return (0.3 + rng.normal(0, 0.02, n)).astype(np.float32)

    # Valves
    if sensor == "TV_POSITION_PCT":
        return (np.clip(40 + 20 * np.sin(2 * np.pi * t / 100.0)
                        + rng.normal(0, 1, n), 0, 100)).astype(np.float32)
    if "VALVE" in sensor:
        return ((ramp > 0.05).astype(np.float32) * 100
                + rng.normal(0, 0.5, n)).astype(np.float32)

    # OES — sharp drop at endpoint
    if sensor.startswith("OES_"):
        endpoint_idx = int(n * float(rng.uniform(0.55, 0.85)))
        signal = np.where(
            t < endpoint_idx,
            1000.0 + rng.normal(0, 20, n),
            200.0 + rng.normal(0, 10, n),
        )
        return (signal * ramp).astype(np.float32)

    # Plasma diagnostics
    if sensor == "PLASMA_DENSITY":
        return (1e10 * ramp + rng.normal(0, 1e8, n)).astype(np.float32)
    if sensor == "ELECTRON_TEMP_EV":
        return (3.5 * ramp + rng.normal(0, 0.05, n)).astype(np.float32)
    if sensor == "ION_FLUX":
        return (5e15 * ramp + rng.normal(0, 1e14, n)).astype(np.float32)
    if sensor == "ION_ENERGY_EV":
        return (250 * ramp + rng.normal(0, 3, n)).astype(np.float32)
    if sensor == "VDC_PROBE_V":
        return (-280 * ramp + rng.normal(0, 2, n)).astype(np.float32)

    # Match network: continuous adjustment around a mid-scale value
    if "MATCH" in sensor:
        return (np.clip(50 + 10 * np.sin(2 * np.pi * t / 80.0)
                        + rng.normal(0, 1, n), 0, 100)).astype(np.float32)

    # Recipe / state
    if sensor == "RECIPE_STEP":
        # 5 recipe steps progressing through the wafer
        return (np.minimum((t * 5) // max(n, 1), 4) + 1).astype(np.float32)
    if sensor == "STEP_TIME_S":
        return (t / SAMPLE_HZ).astype(np.float32)
    if sensor == "WAFER_COUNT":
        return np.full(n, float(rng.integers(1, 26)), dtype=np.float32)
    if sensor == "PROCESS_STATE":
        # 2 = processing (idle gaps don't appear in the data at all)
        return np.full(n, 2.0, dtype=np.float32)

    # Vacuum / pump
    if sensor == "TURBO_RPM":
        return (27000.0 + rng.normal(0, 30, n)).astype(np.float32)
    if sensor == "TURBO_CURRENT_A":
        return (1.2 + 0.2 * ramp + rng.normal(0, 0.02, n)).astype(np.float32)
    if sensor == "ROUGHING_PRES_TORR":
        return (0.001 + rng.normal(0, 0.0001, n)).astype(np.float32)
    if sensor == "PUMP_TEMP_C":
        return (45.0 + 1.0 * ramp + rng.normal(0, 0.1, n)).astype(np.float32)
    if sensor == "PUMP_VIB_MM_S":
        return (0.8 + rng.normal(0, 0.05, n)).astype(np.float32)

    # Auxiliary fallback — deterministic but distinguishable per sensor
    base = float((hash(sensor) % 100) + 10)
    return (base + 5 * ramp * np.sin(2 * np.pi * t / 50.0)
            + rng.normal(0, 0.3, n)).astype(np.float32)


# ---------------------------------------------------------------------------
# Per-tool generation and parquet writing
# ---------------------------------------------------------------------------

def _arrow_table(timestamps: np.ndarray, columns: dict[str, np.ndarray]) -> "pa.Table":
    """Build an Arrow table with a tz-aware UTC timestamp column."""
    # timestamps is datetime64[us]; convert to int64 microseconds since epoch
    ts_int = (timestamps - np.datetime64("1970-01-01", "us")).astype("int64")
    fields = [("timestamp", pa.timestamp("us", tz="UTC"))]
    fields += [(name, pa.float32()) for name in SENSORS]
    schema = pa.schema(fields)

    arrays = [pa.array(ts_int, type=pa.timestamp("us", tz="UTC"))]
    arrays += [pa.array(columns[name], type=pa.float32()) for name in SENSORS]
    return pa.Table.from_arrays(arrays, schema=schema)


def generate_tool_data(
    tool_id: str,
    tool_idx: int,
    start: datetime,
    end: datetime,
    output_dir: Path,
    seed: int,
    single_file: bool,
) -> dict:
    """Generate parquet output for one tool over [start, end)."""
    rng_runs = random.Random(seed + tool_idx)
    rng_vals = np.random.default_rng(seed * 1000 + tool_idx)
    tool_offset = (tool_idx - 10) * 0.5

    runs = generate_wafer_runs(start, end, rng_runs)

    # Bucket samples by hour (or all into one bucket if --single-file)
    # bucket key: datetime (hour-floor, UTC) -> list of (ts_array, {sensor: array})
    buckets: dict[datetime, list[tuple[np.ndarray, dict[str, np.ndarray]]]] = {}
    total_rows = 0

    for run_start, run_end in runs:
        n = int((run_end - run_start).total_seconds() * SAMPLE_HZ)
        if n == 0:
            continue

        run_start_us = np.datetime64(run_start.replace(tzinfo=None), "us")
        ts = run_start_us + (np.arange(n, dtype=np.int64) * SAMPLE_PERIOD_US
                             ).astype("timedelta64[us]")
        sensor_data = {s: synthesize_wafer_trace(s, n, tool_offset, rng_vals)
                       for s in SENSORS}

        if single_file:
            # All into one bucket keyed at start-of-window
            key = start.replace(minute=0, second=0, microsecond=0)
            chunks = buckets.setdefault(key, [])
            chunks.append((ts, sensor_data))
            total_rows += n
        else:
            # Split across hour boundaries
            ts_seconds = (ts - np.datetime64("1970-01-01", "us")).astype("int64") // 1_000_000
            hour_seconds = ts_seconds // 3600
            unique_hours = np.unique(hour_seconds)
            for h in unique_hours:
                mask = hour_seconds == h
                hour_dt = (datetime(1970, 1, 1, tzinfo=timezone.utc)
                           + timedelta(seconds=int(h) * 3600))
                chunks = buckets.setdefault(hour_dt, [])
                chunks.append((ts[mask], {s: sensor_data[s][mask] for s in SENSORS}))
                total_rows += int(mask.sum())

    # Write output
    files_written = 0
    bytes_written = 0
    for key, chunks in buckets.items():
        ts_all = np.concatenate([c[0] for c in chunks])
        order = np.argsort(ts_all, kind="stable")
        ts_all = ts_all[order]
        cols = {s: np.concatenate([c[1][s] for c in chunks])[order] for s in SENSORS}

        table = _arrow_table(ts_all, cols)

        if single_file:
            path = output_dir / f"{tool_id}.parquet"
        else:
            path = (output_dir / tool_id
                    / f"{key.year:04d}" / f"{key.month:02d}"
                    / f"{key.day:02d}" / f"{key.hour:02d}.parquet")
        path.parent.mkdir(parents=True, exist_ok=True)

        pq.write_table(table, path, compression="zstd", compression_level=3)
        files_written += 1
        bytes_written += path.stat().st_size

    return {
        "tool_id": tool_id,
        "prc_group": TOOL_PRC_GROUP[tool_id],
        "runs": len(runs),
        "rows": total_rows,
        "files": files_written,
        "bytes": bytes_written,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--output", type=Path, default=Path("./dummy_data"),
                   help="Output directory (default: ./dummy_data)")
    p.add_argument("--start", type=str, default=None,
                   help="Window start (ISO 8601, treated as UTC). "
                        "Default: hour-aligned start ending at the current hour.")
    p.add_argument("--hours", type=int, default=24,
                   help="Number of hours to generate (default: 24)")
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed for reproducible output (default: 42)")
    p.add_argument("--tools", type=str, default=None,
                   help="Comma-separated subset of tools, e.g. ETCH_T01,ETCH_T02. "
                        "Default: all 20 tools.")
    p.add_argument("--single-file", action="store_true",
                   help="Write one parquet file per tool instead of hour-bucketed "
                        "directory structure. Useful for quick smoke tests.")
    p.add_argument("--write-manifest", action="store_true",
                   help="Also write metadata.json with tool/prc-group/sensor catalog.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.start:
        start = datetime.fromisoformat(args.start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        else:
            start = start.astimezone(timezone.utc)
    else:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=args.hours)
    end = start + timedelta(hours=args.hours)

    if args.tools:
        requested = [t.strip() for t in args.tools.split(",") if t.strip()]
        unknown = [t for t in requested if t not in ALL_TOOLS]
        if unknown:
            sys.exit(f"Unknown tool(s): {', '.join(unknown)}. "
                     f"Valid: {', '.join(ALL_TOOLS)}")
        tools = requested
    else:
        tools = ALL_TOOLS

    args.output.mkdir(parents=True, exist_ok=True)

    print(f"Local EQUS dummy data generator")
    print(f"  Window:  {start.isoformat()}  ->  {end.isoformat()}  ({args.hours}h)")
    print(f"  Tools:   {len(tools)} of {len(ALL_TOOLS)}")
    print(f"  Sensors: {len(SENSORS)} per tool, {SAMPLE_HZ} Hz when running")
    print(f"  Output:  {args.output.resolve()}")
    print(f"  Layout:  {'single file per tool' if args.single_file else 'hour-bucketed (tool/YYYY/MM/DD/HH.parquet)'}")
    print()

    summaries = []
    total_rows = 0
    total_files = 0
    total_bytes = 0
    for tool_id in tools:
        idx = ALL_TOOLS.index(tool_id)
        result = generate_tool_data(
            tool_id, idx, start, end, args.output, args.seed, args.single_file,
        )
        util = result["rows"] / (args.hours * 3600 * SAMPLE_HZ) * 100
        size_mb = result["bytes"] / (1024 * 1024)
        print(f"  {tool_id} [{result['prc_group']:>13s}]: "
              f"{result['runs']:>3d} wafers, "
              f"{result['rows']:>10,} rows ({util:>4.1f}% util), "
              f"{result['files']:>3d} files, "
              f"{size_mb:>6.1f} MB")
        summaries.append(result)
        total_rows += result["rows"]
        total_files += result["files"]
        total_bytes += result["bytes"]

    print()
    print(f"  Total: {total_rows:,} rows, {total_files} files, "
          f"{total_bytes / (1024*1024):.1f} MB")

    if args.write_manifest:
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "sample_hz": SAMPLE_HZ,
            "prc_groups": PRC_GROUPS,
            "sensors": SENSORS,
            "tools": summaries,
        }
        manifest_path = args.output / "metadata.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
        print(f"  Wrote manifest: {manifest_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())