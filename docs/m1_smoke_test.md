# M1 smoke test

End-to-end manual verification that the Local Client Foundation works against
local parquet, with no server in the picture. Target: from a fresh checkout to
linked charts of 8 sensors across 3 tools in **under 5 minutes**.

This is the **M1 exit criterion** — every box below has to tick before
declaring M1 done.

## Setup

1. Clone (or `git pull`) and check out `main` at or after the M1 closing PR.
2. Create a venv and install the package with dev extras:
   ```bash
   python3.12 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   ```
3. Generate ~50 MB of synthetic parquet for three tools using the spike
   generator:
   ```bash
   .venv/bin/python -c "from spike.generate_data import ensure_dataset; from pathlib import Path; ensure_dataset(Path('test-data'))"
   ```
   That writes `etch_a1.parquet`, `etch_a2.parquet`, `etch_b1.parquet`,
   `etch_b2.parquet`, `etch_c1.parquet` under `test-data/`.
4. Point the client at it. Either set `LOCAL_EQUS_APP_DIR` to a scratch
   directory and drop the parquet files under `data/`, or open
   File → Settings… after launch and pick the directory directly:
   ```bash
   export LOCAL_EQUS_APP_DIR="$PWD/.equs-scratch"
   mkdir -p "$LOCAL_EQUS_APP_DIR/data"
   cp test-data/etch_a1.parquet test-data/etch_a2.parquet test-data/etch_b1.parquet "$LOCAL_EQUS_APP_DIR/data/"
   ```

## Run the app

5. Launch the client:
   ```bash
   .venv/bin/python -m local_equs_client
   ```

## Checklist

- [ ] **Window opens** with title "Local EQUS", time range selector across the
      top, sensor picker on the left, empty chart pane on the right.
- [ ] **Picker is populated** with `(tool, sensor)` rows for `etch_a1`,
      `etch_a2`, `etch_b1`. Filter "chamber" narrows the visible list.
- [ ] **Time range strip** shows the local data extent and a draggable region.
- [ ] **Pick 8 sensors across the 3 tools** — e.g. `chamber_pressure`,
      `rf_forward_power`, and `wall_temp` on each of the three tools (only 8
      total: drop one row). Each `(tool, sensor)` pair appears as its own
      chart on the right within ~200 ms.
- [ ] **Avg line + min/max band** render on each chart. The band is a faint
      blue fill behind the solid line.
- [ ] **Linked x-axes**: drag-pan on any chart scrolls every other chart
      together.
- [ ] **Crosshair sync**: hover the mouse over any chart — a vertical
      crosshair tracks across **every** chart at the same x.
- [ ] **Re-query on zoom**: scroll-wheel zoom in on the time axis; the date
      pickers update, charts re-render at higher resolution within ~200–500 ms.
      Old data stays visible until the new query returns (no flat zero baseline,
      no flicker).
- [ ] **Per-tool error isolation**: corrupt one parquet file
      (`echo bogus > "$LOCAL_EQUS_APP_DIR/data/etch_b1.parquet"`),
      then View → Rescan. Charts for `etch_a1` and `etch_a2` still render
      normally; `etch_b1`'s charts show a red `Tool error: …` overlay.
- [ ] **No data in range**: drag the start of the time range to a point
      before any local data (e.g. year 2000). Charts show "No data in range"
      centered, no flat lines plotted.
- [ ] **Persistence**: close the app, reopen — window size, splitter
      position, and last data directory are restored.

## Bail criteria

If any of the following happens, M1 is **not** done; file a bug and revisit:

- Picking sensors takes longer than ~1.5 s to first paint.
- Pan/zoom flickers or shows zero-baseline ghost lines.
- A corrupt file aborts every chart, not just its tool's.
- The app crashes; check `paths.logs_dir()` for the rotating log.

## What's deliberately not in M1

- Server / manifest. M2 wires those in.
- Canonical sensor names. M3 lands those.
- Overview / focus view modes; >50-chart guardrails. M4 territory.
- Saved sets, CSV/PNG export, telemetry, crash handler. M5.
- Packaged installer + auto-updater. M6.
