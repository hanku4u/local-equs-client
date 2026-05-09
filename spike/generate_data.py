"""Synthetic etch-tool data generator for the C0.1 perf spike.

Produces one parquet per tool under ``spike/data/{tool_id}.parquet`` with a
realistic wafer-processing schedule: dense 10 Hz rows during a wafer run,
gaps between wafers / lots / maintenance windows.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

# --- Catalog ----------------------------------------------------------------

TOOL_IDS = ["etch_a1", "etch_a2", "etch_b1", "etch_b2", "etch_c1"]


def sensor_names() -> list[str]:
    """100 realistic etch-tool sensor names, same canonical set across tools."""
    names: list[str] = []

    # 20 OES wavelengths
    for nm in [350, 380, 405, 420, 435, 450, 465, 480, 495, 510,
               525, 540, 555, 570, 585, 600, 620, 660, 700, 740]:
        names.append(f"oes_{nm}nm")

    # 20 MFC channels: 10 gases x (setpoint, actual)
    for gas in ["cf4", "o2", "ar", "he", "sf6", "n2", "h2", "ch4", "hbr", "cl2"]:
        for kind in ["setpoint", "actual"]:
            names.append(f"mfc_{gas}_{kind}")

    # 10 RF
    names.extend([
        "rf_forward_power", "rf_reflected_power", "rf_match_position",
        "rf_voltage_dc", "rf_bias_voltage", "rf_phase", "rf_frequency",
        "rf_load_capacitance", "rf_tune_capacitance", "rf_match_load",
    ])

    # 10 substrate temperature zones
    for z in range(1, 11):
        names.append(f"substrate_temp_zone{z}")

    # 10 chamber
    names.extend([
        "chamber_pressure", "wall_temp", "lid_temp", "liner_temp",
        "chamber_humidity", "throttle_valve_position", "exhaust_pressure",
        "purge_flow", "lid_position", "shower_head_temp",
    ])

    # 10 ESC
    names.extend([
        "esc_voltage", "esc_current", "esc_temp", "esc_he_pressure",
        "esc_clamp_force", "esc_leak_rate", "esc_resistance",
        "esc_lift_pin_position", "esc_zone1_temp", "esc_zone2_temp",
    ])

    # 10 He cooling
    names.extend([
        "he_pressure_inner", "he_pressure_outer", "he_flow_inner",
        "he_flow_outer", "he_supply_pressure", "he_return_pressure",
        "he_leak_rate", "he_temp_in", "he_temp_out", "he_valve_position",
    ])

    # 10 misc
    names.extend([
        "slit_valve_state", "lift_pin_position_a", "lift_pin_position_b",
        "turbo_pump_speed", "turbo_pump_temp", "rough_pump_state",
        "robot_position", "load_lock_pressure", "load_lock_state", "epd_signal",
    ])

    if len(names) != 100:
        raise AssertionError(f"sensor catalog has {len(names)} entries, expected 100")
    return names


# --- Schedule ---------------------------------------------------------------

WAFER_SECONDS = 90.0
INTER_WAFER_GAP_S = 15.0
WAFERS_PER_LOT = 25
HZ = 10


def generate_schedule(start_dt: datetime, hours: float, rng: np.random.Generator) -> list[tuple[datetime, datetime]]:
    """Return a list of (wafer_start, wafer_end) intervals over an N-hour window.

    Models lots of 25 wafers, 90 s per wafer, 15 s inter-wafer gap, 5–15 min
    inter-lot gap, occasional 30–60 min maintenance breaks.
    """
    schedule: list[tuple[datetime, datetime]] = []
    t = start_dt
    end_window = start_dt + timedelta(hours=hours)

    while t < end_window:
        # 5% chance of a maintenance gap (30–60 min) before the next lot
        if rng.random() < 0.05:
            t += timedelta(minutes=float(rng.uniform(30, 60)))
            if t >= end_window:
                break

        # Run a lot of WAFERS_PER_LOT wafers
        for _ in range(WAFERS_PER_LOT):
            wafer_start = t
            wafer_end = wafer_start + timedelta(seconds=WAFER_SECONDS)
            if wafer_end > end_window:
                break
            schedule.append((wafer_start, wafer_end))
            t = wafer_end + timedelta(seconds=INTER_WAFER_GAP_S)

        # Inter-lot gap 5–15 min
        t += timedelta(minutes=float(rng.uniform(5, 15)))

    return schedule


# --- Signal generation ------------------------------------------------------

def make_signal_params(rng: np.random.Generator, n_sensors: int) -> dict[str, np.ndarray]:
    """Per-sensor parameters for the synthetic signal model."""
    return {
        "baseline":   rng.uniform(20.0, 200.0, size=n_sensors),
        "amplitude":  rng.uniform(1.0,   30.0, size=n_sensors),
        "frequency":  rng.uniform(0.005, 0.5,  size=n_sensors),
        "phase":      rng.uniform(0.0,   2.0 * np.pi, size=n_sensors),
        "noise":      rng.uniform(0.1,   5.0,  size=n_sensors),
    }


def generate_wafer_block(
    t_offset_seconds: np.ndarray,
    params: dict[str, np.ndarray],
    rng: np.random.Generator,
) -> np.ndarray:
    """Generate `(len(t_offset_seconds), n_sensors)` values for one wafer.

    Models three recipe phases via a step-pattern multiplier: idle (0–30 s),
    process active (30–60 s), ramp-down (60–90 s).
    """
    n_samples = t_offset_seconds.shape[0]
    n_sensors = params["baseline"].shape[0]

    # Recipe step pattern
    step = np.where(t_offset_seconds < 30.0, 0.4,
            np.where(t_offset_seconds < 60.0, 1.0, 0.7))[:, None]  # (n_samples, 1)

    # Sin component (broadcast)
    sin_part = (
        params["amplitude"][None, :]
        * np.sin(2.0 * np.pi * params["frequency"][None, :] * t_offset_seconds[:, None]
                 + params["phase"][None, :])
    )

    noise = rng.normal(0.0, 1.0, size=(n_samples, n_sensors)) * params["noise"][None, :]

    # baseline + scaled (sin + noise) by step
    return params["baseline"][None, :] + step * (sin_part + noise)


# --- Tool parquet writer ----------------------------------------------------

def generate_tool_parquet(
    tool_id: str,
    out_path: Path,
    sensors: list[str],
    rng_seed: int,
    operating_hours: float = 8.0,
    base_start: datetime | None = None,
) -> dict[str, int | float | str]:
    """Generate one tool's parquet file. Returns stats."""
    rng = np.random.default_rng(rng_seed)

    if base_start is None:
        base_start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    # Stagger tools so they aren't synchronized
    start_dt = base_start + timedelta(minutes=float(rng.uniform(0, 90)))

    schedule = generate_schedule(start_dt, hours=operating_hours, rng=rng)
    params = make_signal_params(rng, n_sensors=len(sensors))

    # Pre-allocate arrays sized to the total active sample count
    samples_per_wafer = int(WAFER_SECONDS * HZ)
    total_samples = len(schedule) * samples_per_wafer

    timestamps = np.empty(total_samples, dtype="datetime64[ns]")
    values = np.empty((total_samples, len(sensors)), dtype=np.float64)

    sample_offsets = np.arange(samples_per_wafer) / HZ  # 0, 0.1, 0.2, ... 89.9 seconds
    cursor = 0
    for wafer in schedule:
        w_start = wafer[0]
        ts = (np.datetime64(w_start.replace(tzinfo=None), "ns")
              + (sample_offsets * 1_000_000_000).astype("timedelta64[ns]"))
        timestamps[cursor:cursor + samples_per_wafer] = ts
        values[cursor:cursor + samples_per_wafer, :] = generate_wafer_block(
            sample_offsets, params, rng
        )
        cursor += samples_per_wafer

    # Build pyarrow table
    columns: dict[str, pa.Array] = {"ts": pa.array(timestamps, type=pa.timestamp("ns"))}
    for i, name in enumerate(sensors):
        columns[name] = pa.array(values[:, i], type=pa.float64())

    table = pa.Table.from_pydict(columns)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path, compression="zstd", compression_level=3)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    return {
        "tool_id": tool_id,
        "wafers": len(schedule),
        "rows": total_samples,
        "size_mb": round(size_mb, 1),
    }


# --- Public API -------------------------------------------------------------

def ensure_dataset(out_dir: Path, force: bool = False) -> list[dict]:
    """Generate all 5 tool parquets if missing. Idempotent unless `force=True`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sensors = sensor_names()

    stats: list[dict] = []
    base_start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

    for i, tool in enumerate(TOOL_IDS):
        path = out_dir / f"{tool}.parquet"
        if path.exists() and not force:
            stats.append({
                "tool_id": tool,
                "rows": pq.read_metadata(path).num_rows,
                "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
                "wafers": "(cached)",
            })
            continue

        t0 = time.perf_counter()
        s = generate_tool_parquet(
            tool_id=tool,
            out_path=path,
            sensors=sensors,
            rng_seed=42 + i,
            operating_hours=8.0,
            base_start=base_start,
        )
        s["gen_seconds"] = round(time.perf_counter() - t0, 1)
        stats.append(s)
        print(f"  generated {tool}: {s['rows']:,} rows, {s['size_mb']} MB ({s['gen_seconds']}s)")

    return stats


if __name__ == "__main__":  # pragma: no cover
    out = Path(__file__).parent / "data"
    print(f"Generating dataset in {out} ...")
    info = ensure_dataset(out)
    total_rows = sum(s["rows"] for s in info)
    total_mb = sum(s["size_mb"] for s in info)
    print(f"\nTotal: {total_rows:,} rows, {total_mb:.1f} MB across {len(info)} tools.")
