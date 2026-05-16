# M4 performance benchmark (C4.11)

This is the **M4 exit criterion**: prove the chart grid handles the worst
case — 100 sensors across 8 tools (800 series) — smoothly.

Two halves to the test:

1. **Data layer** (planner + engine + cache): scripted, reproducible on any
   platform with Python + DuckDB.
2. **UI layer** (placeholder layout, progressive fill, viewport
   virtualization, scrolling): requires a real display server. Verified
   manually on Windows because that's the target platform.

## Targets

- Selecting 100 canonical sensors across 8 tools in **overview mode** lays
  out and starts populating within **1.5s**.
- **Standard mode** (with the C4.6 cap and C4.5 viewport-priority queries)
  scrolls without dropped frames.

## Headless benchmark (data layer)

`scripts/m4_perf_bench.py` generates 8 synthetic parquet files (one per
tool, 100 sensor columns + `ts`), indexes them through `LocalLibrary`,
builds a `QueryPlan` via the real `QueryPlanner`, and times
`QueryEngine.execute()`.

### Reproducing

```bash
PYTHONPATH=src .venv/bin/python scripts/m4_perf_bench.py
```

Optional flags: `--tools`, `--sensors`, `--rows`, `--runs` (defaults match
the M4 worst case).

### Measured (Linux sandbox, Python 3.11, DuckDB, in-memory connections)

**10 minutes of 10 Hz data per tool (6 000 rows / file)** — overview mode
(~100-point target resolution):

```json
{
  "indexed_files": 8,
  "total_series": 800,
  "library_index_seconds": 0.015,
  "plan_seconds":        {"min_s": 0.000, "median_s": 0.000, "max_s": 0.000},
  "execute_cold_seconds":{"min_s": 0.387, "median_s": 0.393, "max_s": 0.585},
  "execute_warm_seconds":{"min_s": 0.001, "median_s": 0.001, "max_s": 0.001}
}
```

**1 hour of 10 Hz data per tool (36 000 rows / file)** — same plan, denser
aggregation:

```json
{
  "indexed_files": 8,
  "total_series": 800,
  "library_index_seconds": 0.012,
  "plan_seconds":        {"min_s": 0.000, "median_s": 0.000, "max_s": 0.000},
  "execute_cold_seconds":{"min_s": 0.566, "median_s": 0.745, "max_s": 0.760}
}
```

### Reading the numbers

- **Plan time is effectively zero.** The C4.4 progressive renderer emits
  placeholder frames as soon as `queryPlanned` fires — well within the 1.5s
  budget the UI gets after the user clicks.
- **Cold execute** lands at ~0.4–0.75s for 800 series. The Query Engine
  parallelises one DuckDB connection per tool (8 in flight here), so the
  per-tool sub-query that the user actually waits on is the slowest of the
  eight, not the sum.
- **Warm execute** is sub-millisecond thanks to the C4.3 LRU
  `QueryCache` — repeat queries from pan/zoom hit the cache and skip
  DuckDB entirely.

## Manual UI verification (Windows)

Run on a real Windows installation since the Linux sandbox cannot drive
PySide6 / OpenGL.

1. Generate seed data covering several hours:
   ```bash
   .venv/Scripts/python.exe src/dummy_data_generator.py --output .equs-perf/data --hours 4
   ```
2. Point Local EQUS at that directory (`LOCAL_EQUS_APP_DIR=.equs-perf` and
   the Settings → data dir).
3. Launch the client, **View → Rescan local data**.
4. In the sensor picker, select **100 canonical sensors** across all 8
   tools (use the prc-group sub-tree to bulk-select).
5. Verify each behaviour:

| Check                                                                                                | Pass criterion                                                            |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Placeholder frames appear immediately on selection commit.                                           | Visible within ~1.5s.                                                     |
| Progress label reads `Loading X / 8 tools…` and decrements as each tool completes.                   | Updates progressively, never freezes the window.                          |
| C4.10 banner shows at >50 series and reads "Showing 50 of 800 charts. Overview mode renders the full grid." | Banner visible, "Switch to Overview" button present, no modal block.     |
| Click **Switch to Overview**.                                                                        | Sparkline grid renders all 800 series within 1.5s; banner hides.          |
| Scroll the sparkline grid.                                                                           | No dropped frames; sparklines that come into view start loading promptly. |
| Click any sparkline.                                                                                 | Flips to focus mode, narrows selection to that tool/sensor pair.          |
| Switch back to standard mode.                                                                        | Banner re-appears; visible 50 plots render at the focus resolution.       |
| Pan / zoom the linked x-axis on a standard-mode plot.                                                | All 50 plots track together; debounce re-fires the query at the new range.|

## M4 exit checklist

- [x] Headless benchmark numbers logged above (re-run any time via
      `scripts/m4_perf_bench.py`).
- [ ] Manual Windows checklist above completed against the M4-closing
      commit. Pasted screenshot of the overview grid into the PR.
- [x] C4.10 soft guardrail visible at >50 and escalation copy at >200
      (covered by `tests/unit/ui/test_chart_grid.py`).
