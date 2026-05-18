# Error Handling Audit (C5.16)

This document catalogues every "things that go wrong" case the client
currently knows about: what the user sees, where the handler lives, and
which automated test exercises it. Anything missing a test is flagged
explicitly so the gap doesn't go dark.

The catalogue is grouped by subsystem. Each row is:
**Failure mode** — what triggers it · **Behavior** · **Code** · **Test**.

---

## 1. Selection & query pipeline

### Empty selection
The user has no tools or sensors selected.
- **Behavior**: Chart grid renders no plots. Data Table tab shows
  `"Empty selection — pick a tool and sensor to view raw data."` in its
  status bar.
- **Code**: `data_table.py:184` short-circuit in `DataTableView.set_plan`
  for `plan.per_tool_queries == []`.
- **Test**: `test_data_table.py::test_set_plan_empty_keeps_empty_status`.

### Selected tools have no mapped sensors
A canonical sensor is selected but no `MetadataCache.mapping` exists for
the chosen tools.
- **Behavior**: Data Table shows `"No mapped sensors for the selected
  tools."` Chart grid renders the plot frames but no curves.
- **Code**: `data_table.py:189` short-circuit when every `ToolQuery` has
  empty `raw_columns`. `QueryPlan.missing_mappings` carries the per-tool
  list for richer UI (not surfaced today).
- **Test**: `test_data_table.py::test_set_plan_no_mapped_sensors_shows_friendly_status`.
- **Gap**: missing-mapping warnings aren't surfaced in the chart UI
  beyond the per-tool empty plot.

### Partial time coverage
The selected time range overlaps with only part of the available local
data for a tool.
- **Behavior**: `QueryPlan.partial_data_warnings` carries one
  human-readable string per affected tool (e.g.
  `"a: no local data after 2026-01-15"`). Data Table appends
  `"(partial data: …)"` to its status text.
- **Code**: `query_planner._coverage_warnings`; consumed at
  `data_table.py:247` and `data_table.py:280`.
- **Test**: `test_query_planner.py` covers warning generation;
  data-table consumption is implicit in the status-text tests.

### Empty time range
Selected time range is zero-width or in the future with no overlap.
- **Behavior**: `QueryPlan.per_tool_queries` is non-empty but each
  `ToolQuery.file_paths` may be empty. `RawQueryEngine` short-circuits
  (returns 0 / empty Arrow table) so the Data Table renders nothing
  without crashing. Chart grid likewise renders empty plots.
- **Code**: `raw_query_engine.py:42` early-return when
  `_any_file_paths(...)` is False.
- **Test**:
  `test_raw_query_engine.py::test_count_plan_with_all_empty_file_paths_returns_zero`.

### Per-tool query failure
DuckDB raises on one tool (corrupt parquet, schema mismatch, missing
sensor column).
- **Behavior**: That tool's result becomes a `QueryError(tool_id,
  message)`. Other tools' results still flow through. The chart plot
  for the failing tool shows nothing; no app-level error banner.
- **Code**: `query_engine.py:108-114` `try/except` around per-tool
  execution.
- **Test**: `test_query_engine.py` failure-mode tests.
- **Gap**: failed tools aren't surfaced to the user; we silently render
  empty plots. Worth a banner in a future polish pass.

### Whole-pipeline query failure
`QueryEngine.execute` itself raises (unlikely — most failures are
per-tool).
- **Behavior**: `QueryController.queryFailed` emits with the exception.
  `MainWindow` shows the exception in the status bar for 5 s:
  `"Query failed: <exc>"`. A `query_failed` telemetry event fires with
  `{error_type, partial_results}`.
- **Code**: `query_controller.py:170` `_on_failed` callback; status bar
  display at `main_window.py:178`.
- **Test**:
  `test_query_controller.py::test_engine_failure_emits_query_failed`,
  `test_query_controller.py::test_query_failed_event_emitted_on_engine_exception`.

### Query cancellation
A new selection arrives while a query is in flight, or the user closes
the app mid-query.
- **Behavior**: Pending query is interrupted via DuckDB's
  `conn.interrupt()`. Worker returns `QueryCancelled`. **No** error
  surface (status bar, telemetry) — cancellation is normal.
- **Code**: `query_controller.py:124-127` cancels the prior job before
  dispatch; `query_engine.py` polls `cancelled()` between tools.
- **Test**:
  `test_query_controller.py::test_query_cancelled_does_not_emit_failed`,
  `test_query_controller.py::test_query_cancelled_emits_no_telemetry`,
  `test_query_controller.py::test_rapid_dispatch_cancels_prior_job`.

---

## 2. Network & server

### Server unreachable (DNS, connection refused, timeout)
Network is down or `server_url` points at a host that doesn't answer.
- **Behavior**: `requests.RequestException` is mapped to
  `ServerUnreachable`. Each call site decides what to do:
  - `MetadataCache.refresh_*` — log a warning and fall back to the
    cached payload.
  - `UpdateManager.fetch_manifest` — exception propagates; UpdatesPanel
    surfaces it.
  - `DownloadManager` — propagates; the `.partial` file is preserved so
    the next attempt resumes.
  - `Telemetry.flush` — caught; queue retained for retry; backoff
    advances.
- **Code**: `http.py:99` wraps `requests.RequestException`. Fallbacks at
  `metadata_cache.py:204+`, telemetry backoff at
  `telemetry_client.py:74-80`.
- **Test**: `test_http.py::test_connection_error_raises_server_unreachable`,
  `test_metadata_cache.py::test_refresh_falls_back_to_cache_when_server_unreachable`,
  `test_telemetry_client.py::test_flush_network_error_keeps_events_for_retry`.

### Server returns 5xx
The server responds with 500–599.
- **Behavior**: Mapped to `ServerError(status_code, url, body)`. Same
  per-call-site policy as `ServerUnreachable`. Telemetry treats it as
  retryable and advances backoff.
- **Code**: `http.py:117` raises `ServerError` for any 4xx-5xx other
  than 404.
- **Test**: `test_http.py::test_5xx_raises_server_error`,
  `test_telemetry_client.py::test_flush_5xx_keeps_events_for_retry`.

### Server returns 4xx (non-404)
Bad request, unauthorized, forbidden, etc.
- **Behavior**: Mapped to `ServerError`. **Telemetry drops the batch**
  on 4xx (a malformed event would otherwise block the queue forever).
  Other call sites propagate the exception.
- **Code**: `http.py:117`; telemetry 4xx branch at
  `telemetry_client.py:84-99`.
- **Test**: `test_http.py::test_4xx_other_raises_server_error`,
  `test_telemetry_client.py::test_flush_4xx_drops_batch`.

### Server returns 404
Manifest or resource missing.
- **Behavior**: Mapped to `NotFound(url)`. Propagates to the caller.
- **Code**: `http.py:115`.
- **Test**: `test_http.py::test_404_raises_not_found`,
  `test_update_manager.py::test_not_found_propagates`.

### 409 stale mapping conflict
Spec calls this out, but the client has no `PUT /mappings` call site
yet (Mapping Editor is read-only).
- **Behavior**: N/A today.
- **Code**: N/A.
- **Test**: N/A.
- **Gap**: Lands with [#56 (Mapping Editor full edit)]. The save flow
  needs to recognize 409, refresh from server, and prompt the user to
  re-apply their edits on top of the new baseline.

### `server_url` not configured
First launch or user explicitly cleared the URL.
- **Behavior**: `FirstRunWizard` prompts on launch; user can skip.
  `MainWindow` constructs without `HttpClient`, `UpdateManager`,
  `DownloadManager`, or `Telemetry`. Server-dependent features
  (updates, telemetry, canonical sensors) silently degrade.
- **Code**: `main.py:54-64`. Telemetry singleton not registered →
  module-level `event()`/`flush()` are no-ops.
- **Test**: Integration-only (boot path); no automated coverage.
- **Gap**: No UI badge indicating "offline mode." Users may not know
  why update-check is silent.

---

## 3. Local data

### Corrupt or unreadable parquet
A `.parquet` in the data dir has a truncated footer / unreadable schema.
- **Behavior**: `LocalLibrary.scan` logs a warning, **skips the file**,
  and continues. The file is excluded from the index. At query time
  it's simply absent from `file_paths`.
- **Code**: `local_library.py:74-76` catches `(OSError, ValueError)`
  during indexing.
- **Test**: `test_local_library.py::test_scan_skips_corrupt_parquet`.

### Missing data directory
The configured `data_dir` doesn't exist on disk.
- **Behavior**: `main.py` creates it (`mkdir(parents=True,
  exist_ok=True)`). `LocalLibrary.scan` tolerates an empty dir and
  returns 0.
- **Code**: `main.py:35`.
- **Test**: `test_local_library.py::test_scan_handles_missing_data_dir`.

### Sensor missing from a tool's parquet schema
Mapping says tool A has sensor X, but X isn't a column in A's parquet.
- **Behavior**: Per-tool query fails →
  `QueryError(message="…schema mismatch…")`. Other tools unaffected.
- **Code**: `query_engine.py:108-114`.
- **Test**: Existing per-tool failure tests cover this path generally.

---

## 4. Downloads

### Checksum mismatch
Downloaded bytes' SHA-256 doesn't match the manifest entry.
- **Behavior**: `.partial` is unlinked; `ChecksumMismatch(file_id,
  expected, actual)` is raised. UpdatesPanel surfaces the failure.
  `download_failed` telemetry fires.
- **Code**: `download_manager.py:120-123`.
- **Test**:
  `test_download_manager.py::test_checksum_mismatch_raises_and_drops_partial`,
  `test_download_manager.py::test_download_failed_event_on_checksum_mismatch`.

### Network drop mid-download
TCP reset, server timeout, connection error while streaming chunks.
- **Behavior**: Exception propagates. `.partial` is **preserved** on
  disk; the next attempt sends `Range: bytes=N-` and resumes from byte
  N. `download_failed` telemetry fires with `partial_bytes`.
- **Code**: `download_manager.py:99-110` chunked loop; no special
  except clause — the implicit `raise` in the surrounding `try/except`
  leaves the `.partial` alone.
- **Test**: Resume path covered by
  `test_download_manager.py::test_resume_from_partial_uses_range_header`.
- **Gap**: No test simulates mid-stream connection drop. The `responses`
  library makes this awkward — would need a deliberate truncated body
  fixture.

### Disk full while writing `.partial`
`OSError(ENOSPC)` from `f.write(chunk)`.
- **Behavior**: Exception propagates out of `download_file` as
  `OSError`. `.partial` is left in whatever state the OS reached.
  `download_failed` telemetry fires. UpdatesPanel surfaces it via
  `QMessageBox.warning`.
- **Code**: `download_manager.py:108`; UI surface via
  UpdatesPanel callbacks.
- **Test**: **Missing**. Hard to simulate in unit tests.
- **Gap**: No explicit ENOSPC handling — we don't pre-flight available
  disk space against `manifest_file.size_bytes`.

### Download cancellation
User dismisses the Updates dialog mid-transfer.
- **Behavior**: Worker observes the cancel flag between chunks and
  raises `DownloadCancelled`. `.partial` preserved. **No** telemetry
  event (consistent with `QueryCancelled` precedent).
- **Code**: `download_manager.py:103-110`.
- **Test**:
  `test_download_manager.py::test_cancellation_preserves_partial`,
  `test_download_manager.py::test_download_cancelled_emits_no_failed_event`.

---

## 5. Telemetry pipeline

### Endpoint offline at startup
`server_url` is set but the telemetry endpoint never comes back.
- **Behavior**: Each flush attempt catches `ServerUnreachable`;
  consecutive-failure counter advances; backoff stretches from 60 s →
  2 min → 5 min → 15 min cap. Queue is capped at 10 000 rows
  (FIFO eviction logs a `WARNING` per eviction).
- **Code**: `telemetry_client.py:74-80, 102-105`;
  `state/dao/telemetry_queue.py:37-53` cap eviction.
- **Test**: `test_telemetry_client.py::test_flush_during_backoff_window_is_skipped`,
  `test_telemetry_queue.py::test_enqueue_evicts_oldest_when_cap_reached`.

### Telemetry opt-out
User unchecks "Send anonymous telemetry" in Settings.
- **Behavior**: `event()` becomes a no-op (drops new events). `flush()`
  short-circuits without touching the queue, so already-queued events
  stay put in case the user opts back in.
- **Code**: `telemetry_client.py:56-58, 66-67`.
- **Test**: `test_telemetry_client.py::test_event_is_noop_when_opted_out`,
  `test_telemetry_client.py::test_flush_is_noop_when_opted_out`.

---

## 6. Uncaught exceptions

### Crash in the Qt event loop
A signal slot or any code reachable from the main thread raises.
- **Behavior**: `crash_handler._crash_hook` catches via `sys.excepthook`.
  Logs the full traceback at ERROR (lands in the rotating log file);
  emits `error` telemetry event with type, truncated traceback (≤ 4000
  chars), `thread="main"`; chains to the previous hook so the default
  exit behavior still runs.
- **Code**: `crash_handler.py:65-87`.
- **Test**: `test_crash_handler.py::test_main_thread_crash_emits_error_event`.

### Crash in a `threading.Thread` worker
Stdlib threads raising uncaught exceptions.
- **Behavior**: `crash_handler._thread_crash_hook` via
  `threading.excepthook`. Same log + telemetry surface, with
  `thread=<thread name>`.
- **Code**: `crash_handler.py:90-105`.
- **Test**: `test_crash_handler.py::test_thread_crash_emits_error_event`.

### `KeyboardInterrupt` / `SystemExit`
Ctrl-C, normal app exit.
- **Behavior**: Special-cased — passed straight through to the previous
  hook without log noise or telemetry.
- **Code**: `crash_handler.py:71-75, 92-93`.
- **Test**: `test_crash_handler.py::test_keyboard_interrupt_does_not_emit_telemetry`,
  `test_crash_handler.py::test_thread_systemexit_does_not_emit_telemetry`.

### Worker exceptions inside `BackgroundJob.run`
Any subclass of `BackgroundJob` raising during `run()`.
- **Behavior**: Caught by `_BackgroundJobRunnable.run`, re-emitted as
  the `failed(exc)` signal. Each `BackgroundJob` consumer wires its
  own slot (e.g. `QueryController._on_failed`). Doesn't reach
  `crash_handler`.
- **Code**: `data_layer/threading.py:46-50`.
- **Test**: `test_threading.py` covers the wrapping;
  query/download tests cover end-to-end propagation.

---

## 7. Exports

### Save path is unwritable / disk full
User picks an invalid location, no write permission, or disk is full.
- **Behavior**: `OSError` is caught in `MainWindow._export_csv` and
  `_export_png`. Displays `QMessageBox.warning("Export failed", str(exc))`.
  No retry — user is expected to pick a different path.
- **Code**: `main_window.py:255-258, 279-281`.
- **Test**: Manual.
- **Gap**: No automated test. Mocking `QFileDialog.getSaveFileName`
  plus `Path.open` is feasible but not done.

### Empty chart / table data
User triggers Export with nothing selected.
- **Behavior**: Writer produces a header-only CSV (chart: just `ts`;
  table: `tool_id,ts`). PNG export captures whatever blank state the
  chart is in.
- **Code**: `export.py:65, 95`.
- **Test**: `test_export.py::test_write_chart_csv_empty_results_writes_only_ts_header`,
  `test_export.py::test_write_table_csv_empty_plan_writes_header_only`.

---

## 8. Settings & config

### Garbage `config.toml`
Hand-edited file with invalid TOML syntax.
- **Behavior**: `tomllib.load` raises. `main.py` doesn't currently
  catch this — the app fails to start.
- **Code**: `config/settings.py:43-55`.
- **Test**: None.
- **Gap**: Should fall back to defaults with a one-line warning and a
  status banner. Currently the app just dies on startup.

### Field present but invalid value
`update_check_frequency_hours = "every hour"` instead of an int.
- **Behavior**: `int(update_freq_raw)` raises in `Settings.from_file`.
  Same outcome as garbage TOML — app fails to start.
- **Code**: `config/settings.py:54`.
- **Test**: None.
- **Gap**: Same as above.

---

## 9. Mapping Editor (read-only era)

The current Mapping Editor is read-only. Server-write failure modes
(409 stale, 422 validation, 401 unauthorized) will land with the full
edit support in #56. Tracked there; not present today.

---

## Summary of gaps to revisit

These aren't bugs but they're worth a follow-up issue or two when M5
ships:

1. **Per-tool QueryError isn't user-visible** — failed tools just
   render empty plots. A banner or per-plot error stamp would close
   this.
2. **No "offline mode" badge** when `server_url` is unset. Users see
   silent degradation of update-check and canonical sensors.
3. **No ENOSPC pre-flight** on downloads. We learn about disk-full
   mid-stream.
4. **No mid-stream-drop test for downloads**. Resume logic is tested
   but the failing path isn't.
5. **Hand-edited `config.toml` crashes the app**. Should fall back to
   defaults with a warning banner.
6. **Export OSError path has no automated test** — only manual.
7. **409 stale mapping** — out of scope until #56.

None of these block M5 exit. Each could be its own follow-up issue
when the time is right.
