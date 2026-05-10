# M3 smoke test

End-to-end manual verification that canonical sensor names + cross-tool
selection work. **This is the headline use case** the milestone exists to
deliver: pick `chamber_pressure` once and every etcher's chart populates with
the right per-tool raw column.

This is the **M3 exit criterion**.

## Prerequisites

- A backend running with seeded process groups, canonical sensors, mappings,
  and categories. The dev seeder should populate at least:
  - One prc_group named `etcher` (or similar).
  - 3-5 etcher tools (`etch_a1`, `etch_a2`, …) with parquet files served via
    the manifest.
  - The canonical sensor `chamber_pressure` mapping to a different raw column
    per tool (e.g. `etch_a1: ChamberPressure_torr`, `etch_a2: PCham_torr`).
- Client checked out at the M3-closing commit and installed in a venv.

## Setup

1. Wipe the client app dir so the run is genuinely fresh:
   ```bash
   export LOCAL_EQUS_APP_DIR="$PWD/.equs-m3"
   rm -rf "$LOCAL_EQUS_APP_DIR"
   ```
2. Launch:
   ```bash
   .venv/bin/python -m local_equs_client
   ```
3. First-run wizard appears; enter the dev server URL → Save.
4. **View → Updates…** Check all etcher files. **Download selected**. Wait
   for every row to read `done`.
5. **View → Rescan local data**. Status bar shows `Rescan complete — N
   parquet files indexed`.

## Checklist

### Bootstrap

- [ ] Rescan triggered `/v1/categories.json`, `/v1/process-groups/etcher/canonical-sensors.json`,
      and `/v1/process-groups/etcher/mappings.json` (verify in server access
      logs — every request should carry `X-Client-Id` and, after the first
      run, `If-None-Match`).
- [ ] `sqlite3 "$LOCAL_EQUS_APP_DIR/state.db" "SELECT prc_group_id, etag FROM cached_mappings"`
      shows the `etcher` row with a real ETag.

### Sensor picker tree

- [ ] The picker shows each etcher as a tree root labeled `(0/N)`.
- [ ] Expanding a tool shows category folders (Process, RF, …) populated from
      `/v1/categories.json`.
- [ ] Hovering `chamber_pressure` on any etcher pops the Details pane with
      description, units, local-file count, and local date range — within
      ~50 ms.
- [ ] Filter `chamber` — the Results list appears with breadcrumbs
      `etch_a1 / Process / chamber_pressure` etc. Clicking a result toggles
      the matching tree leaf.

### Cross-tool selection — the headline path

- [ ] Click the canonical `chamber_pressure` leaf on **every** etcher (or
      check the parent category on each, then uncheck the unwanted siblings).
- [ ] The Selected (N) header shows the total leaf count across tools (e.g.
      `Selected (5)`).
- [ ] Within ~200 ms, the chart grid shows one chart per `(tool, raw_column)`.
      Each plot title is the form `etch_aX — <raw_name_per_tool>`.
- [ ] Plot titles confirm the canonical→raw expansion worked — different
      tools show different raw column names for the same canonical
      (`etch_a1: ChamberPressure_torr`, `etch_a2: PCham_torr`, …).
- [ ] Linked pan/zoom and crosshair sync still work across the new charts
      (regression check from M1).

### Mapping editor

- [ ] **View → Mapping Editor…** opens the editor with the prc_group
      dropdown defaulting to `etcher`.
- [ ] Matrix shows canonical sensors as rows, etcher tools as columns. Cells
      where a mapping exists show the raw column name; cells with no mapping
      render with a faint red background and `—`.
- [ ] Click a canonical row — Details panel populates with name, description,
      units, and per-tool `tool → raw_name` listing. "Audit history: Coming
      in M5" is the explicit placeholder.
- [ ] **Categories** tab is visible but disabled — only Mappings is active.
- [ ] Drag the matrix/detail splitter — re-open the editor and confirm the
      split is restored.

### Missing-mapping handling

- [ ] Pick a canonical that's not mapped on every selected tool. The chart
      grid still produces charts for the mapped tools; the unmapped tool
      simply has no chart for that canonical and the warnings list (see logs
      / future status bar) records the missing pair.

## Bail criteria

If any of the following happens, M3 is **not** done:

- Picking `chamber_pressure` charts the *same raw column* on every tool
  (i.e. the canonical→raw expansion didn't run).
- The picker tree is empty after a fresh server-connected install + rescan.
- Hovering a sensor hangs the UI thread (Details should never touch DuckDB).
- The mapping editor crashes opening or switching prc_group.
- A 304 manifest fetch followed by 200 canonical-sensors fetch leaves the
  cache inconsistent — picker and editor should agree on what's in the
  cache.

## What's deliberately not in M3

- Editing mappings, full Save as set CRUD, full categories admin, telemetry,
  crash handler. M5.
- View modes (overview/focus), virtualized chart grid, >50-chart guardrails.
  M4.
- Packaged installer / auto-updater. M6.
