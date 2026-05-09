# Local EQUS — MVP Implementation Plan

Detailed task breakdown for the v1/MVP build, derived from `Project_Plan` and `Backend_plan`. Tasks are sized for AI coding agents: each one is a single focused unit of work with clear inputs, outputs, and acceptance criteria.

---

## How to use this document

**Scope.** "MVP" here = the v1 scope already defined in the source documents: milestones M0 through M6. Items in the "Out of v1" / "Deferred" sections of either source plan are explicitly out of scope for this plan.

**Task IDs.** Each task has a stable ID:
- `B<milestone>.<n>` — backend (FastAPI server) tasks
- `C<milestone>.<n>` — client (PySide6 desktop app) tasks
- `S<milestone>.<n>` — shared / coordination / non-code tasks (e.g. schema freezes, certificate orders)

**Task format.**
- **Goal** — one-line outcome.
- **Files** — primary files created or modified. Not exhaustive; agents should create supporting modules as needed.
- **Depends on** — task IDs that must complete first (within or across the B/C/S streams).
- **Done when** — testable acceptance criteria. Agent stops when these are satisfied.

**Sequencing rules** (from the source plans):
1. Within each milestone, do tasks roughly in numerical order; explicit `Depends on` overrides this.
2. Don't start M3 until M2 ingest works against real(ish) data — M2 surfaces bugs that M3 can't.
3. Don't broadly release M2 — saved views break when M3 introduces canonical names.
4. Hand-shape the skeleton (M0) yourself; delegate M1+ feature work to agents.
5. If the M0 client spike disappoints, stop and reconsider the stack before doing M1.

**Conventions every task assumes.**
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x async, Alembic, pydantic-settings, asyncpg, httpx, pyarrow, APScheduler, pytest with testcontainers for integration.
- Client: Python 3.12, PySide6, PyQtGraph, DuckDB, pyarrow, requests/httpx, pytest-qt.
- Lint/format: ruff + black; type-check: mypy strict on `services/`, `data/`, `ingest/`, `retention/` (server) and `data_layer/`, `selection/`, `query/` (client). UI modules don't need strict typing.
- Tests live under `tests/unit/` (no DB, no Qt) and `tests/integration/` (real Postgres for server, real QApplication for client).
- All file paths in this doc use the project structure from `Backend_plan` §7 and the equivalent client structure introduced in C0.4.

**Cross-cutting work.** Every task should leave the codebase green: lint, type-check, and existing tests pass. Adding tests for new code is part of "done."

---

## Quick milestone summary

| Milestone | Theme | Backend deliverable | Client deliverable |
|---|---|---|---|
| **M0** | Spike + skeleton | Repo skeleton, scheduler stub, health route, CLI | Performance spike, repo skeleton, threading scaffold, interface contracts |
| **M1** | Local-only client | — | Working app reading local parquet, charting in standard mode |
| **M2** | Server integration | Ingest pipeline, manifest/files/sensors endpoints, seed fixture | Update + Download Manager, Updates + Library panels |
| **M3** | Process groups | prc_groups, mappings, audit, 409 concurrency | Tree picker with canonical names, read-only mapping editor |
| **M4** | View modes | — | Overview/Focus modes, virtualization, query cache, progressive rendering |
| **M5** | Polish | Telemetry, retention, archive, full health | Saved sets, data table, full mapping editor, settings, telemetry, logging |
| **M6** | Distribution | `/v1/app-version`, installer hosting | Nuitka + Inno Setup + signing + auto-updater |

---

# M0 — Spike & Skeleton

Goal: the developer hand-shapes the architecture so M1+ agent work has rails. Two parallel streams (backend + client) plus a procurement task.

## Shared / non-code

### S0.1 — Order code-signing certificate
**Goal.** Begin lead time for OV cert (Sectigo or DigiCert, ~$200/yr) so it's in hand by M6.
**Done when.** Order placed; vendor's verification process underway. Track ETA in project notes.

### S0.2 — Confirm wrapper API contract with upstream owner
**Goal.** Resolve open question 1 in `Backend_plan` §13 before M2.
**Done when.** Documented decisions captured in repo (e.g. `docs/upstream_api.md`) for: data endpoint return format (parquet bytes vs. arrow vs. JSON rows), endpoint paths and parameters for snapshot/changes/data, auth model on the wrapper, error semantics for "no data this hour."

### S0.3 — Confirm platform parameters
**Goal.** Resolve open questions 2, 4, 5 in `Backend_plan` §13.
**Done when.** Documented: who configures probes and against what path, `terminationGracePeriodSeconds` value, env var injection mechanism, exact PVC mount path.

## Backend stream

### B0.1 — Repository scaffolding
**Goal.** Create the src-layout repo skeleton per `Backend_plan` §7.
**Files.** `pyproject.toml`, `alembic.ini`, `.env.example`, `README.md`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/.gitkeep`, all directory stubs under `src/local_equs/` and `tests/`.
**Depends on.** —
**Done when.** `pip install -e .[dev]` succeeds; `python -c "import local_equs"` works; `pytest` discovers an empty test suite and exits 0; ruff and mypy pass on stubs.

### B0.2 — Settings module
**Goal.** Implement `config.py` with all env vars from `Backend_plan` §7 "Config" subsection.
**Files.** `src/local_equs/config.py`, `.env.example`.
**Depends on.** B0.1
**Done when.** Every var in the config block is present with the documented default; `EQUS_` prefix works; loading from `.env` works; values type-check; importing `from local_equs.config import settings` succeeds in a fresh interpreter.

### B0.3 — Logging configuration
**Goal.** Structured logging setup that the rest of the app and APScheduler can use.
**Files.** `src/local_equs/logging.py`.
**Depends on.** B0.2
**Done when.** A `configure_logging()` function emits JSON-line logs to stdout with timestamp, level, logger name, and message; APScheduler's loggers route through the same config; tests assert log records are produced.

### B0.4 — Database engine + base
**Goal.** Async SQLAlchemy engine, session factory, and `Base` with the naming convention from `Backend_plan` §7.
**Files.** `src/local_equs/data/db.py`, `src/local_equs/data/base.py`.
**Depends on.** B0.2
**Done when.** `get_session()` async dependency returns a working session against a configured Postgres; `Base.metadata` carries the naming convention; unit tests verify engine construction without hitting a real DB (mock URL).

### B0.5 — Initial Alembic migration
**Goal.** First migration creating only the bookkeeping tables: `ingest_runs`, `retention_runs`, `ingest_state` (with FK placeholder if `tools` doesn't yet exist — leave commented and add in M2).
**Files.** `src/local_equs/data/models/ingest.py`, `src/local_equs/data/models/retention.py`, `migrations/versions/0001_initial.py`.
**Depends on.** B0.4
**Done when.** `alembic upgrade head` against a clean Postgres creates the three tables with correct column names, types, and indexes per `Backend_plan` §5; `alembic downgrade base` reverses cleanly.

### B0.6 — Scheduler skeleton
**Goal.** APScheduler wired into FastAPI lifespan, registering no-op ingest and retention jobs that log a tick.
**Files.** `src/local_equs/scheduler.py`, `src/local_equs/main.py`.
**Depends on.** B0.3
**Done when.** Starting the app spins up two interval jobs (ingest 10min, retention 1hr) with `coalesce=True`, `max_instances=1`, `misfire_grace_time=60`; jobs log "ingest tick" / "retention tick" lines; `EQUS_ENABLE_BACKGROUND_JOBS=false` disables both; `scheduler.shutdown(wait=True)` runs on shutdown.

### B0.7 — Health endpoint
**Goal.** `GET /v1/health` returning the documented payload, sourcing `last_ingest_at` and `last_retention_at` from the run tables.
**Files.** `src/local_equs/api/router.py`, `src/local_equs/api/routes/health.py`, `src/local_equs/services/health_service.py`, `src/local_equs/schemas/health.py`.
**Depends on.** B0.5, B0.6
**Done when.** A real Postgres call returns `{status, last_ingest_at, last_retention_at, disk_pct, upstream_reachable}`; `disk_pct` uses `shutil.disk_usage(settings.data_dir)`; `upstream_reachable` is a stub returning `null` until M2; integration test asserts shape and 200.

### B0.8 — CLI structure
**Goal.** `python -m local_equs.cli ...` entrypoint with subcommands `migrate`, `seed-dev`, `clear-dev`.
**Files.** `src/local_equs/cli.py`.
**Depends on.** B0.5
**Done when.** `migrate` shells out to `alembic upgrade head`; `seed-dev` and `clear-dev` exit 0 with a "stub — implemented in M2" message; both check `EQUS_ALLOW_DEV_SEED=true` and refuse if absent; both check the database name contains `dev` or `test` and refuse otherwise.

### B0.9 — Middleware: client headers
**Goal.** Capture `X-Client-Id` and `X-App-Version` per request and attach to request state.
**Files.** `src/local_equs/api/middleware.py`, `src/local_equs/deps.py`.
**Depends on.** B0.7
**Done when.** A `get_request_context` dependency returns `{client_id, app_version}` (both nullable); middleware doesn't reject requests missing the headers; integration test confirms presence/absence both work.

### B0.10 — Test infrastructure
**Goal.** Pytest config, fixtures for an ephemeral Postgres (testcontainers), and a `TestClient` fixture with the lifespan started.
**Files.** `tests/conftest.py`, `tests/integration/conftest.py`, `tests/unit/conftest.py`.
**Depends on.** B0.5, B0.7
**Done when.** Unit tests run without docker; integration tests bring up a Postgres container, run all migrations, hand a `TestClient` and `AsyncSession` to test functions; one smoke test calls `/v1/health` and asserts 200.

### B0.11 — README documenting the migration flow
**Goal.** Capture the manual migrate-then-redeploy flow and the dev seed workflow.
**Files.** `README.md`.
**Depends on.** B0.8
**Done when.** README has a "Local development" section with `seed-dev`/`clear-dev` examples and a "Production migrations" section documenting the migrate-then-redeploy procedure verbatim.

## Client stream

### C0.1 — Performance spike
**Goal.** Validate stack assumption: real parquet → DuckDB query with downsampling → PyQtGraph render is snappy enough for the 100-chart scenario.
**Files.** `spike/spike_render.py`, `spike/README.md`. **This code is throwaway — keep it in a `spike/` directory and don't graft it into the main app.**
**Depends on.** —
**Done when.** With representative parquet (~100MB, ~30 sensors, ~1Hz, several days), the script produces a window with 8 linked charts at ~2000 points each in under 1.5s end-to-end on the developer's machine, and zoom re-query under 500ms. Numbers logged to `spike/results.md`. Memory under 500 MB. **If targets aren't met, stop M0 and reconsider stack before C0.2.**

### C0.2 — Client repository scaffolding
**Goal.** Set up the desktop app repo with a layered structure that matches the architecture in `Project_Plan` §"Architecture".
**Files.** `pyproject.toml`, `src/local_equs_client/__init__.py`, `src/local_equs_client/main.py`, and stubs for these subpackages: `data_layer/` (update_manager, download_manager, local_library, query_planner, query_engine, query_cache, metadata_cache, permissions, telemetry_client, update_client), `state/` (sqlite schema + dao), `ui/` (panels, picker, chart_grid, time_range, mapping_editor, settings), `selection/` (selection_model, view_controller), `config/`.
**Depends on.** C0.1 (passing)
**Done when.** `pip install -e .[dev]` succeeds; `python -m local_equs_client` launches an empty PySide6 main window with the title "Local EQUS"; ruff and mypy pass.

### C0.3 — Configuration & paths
**Goal.** Centralize the path conventions from `Project_Plan` §"Configuration & Storage".
**Files.** `src/local_equs_client/config/paths.py`, `src/local_equs_client/config/settings.py`.
**Depends on.** C0.2
**Done when.** `paths.app_dir()` returns `%LOCALAPPDATA%\LocalEQUS\` on Windows (and a sensible fallback on macOS/Linux for dev); `paths.data_dir()`, `paths.state_db()`, `paths.logs_dir()`, `paths.config_file()` all derive from app_dir; `Settings` reads `config.toml` if present and falls back to defaults; settings object is process-wide singleton.

### C0.4 — SQLite state schema
**Goal.** Local state DB carrying file index, pins, saved views, cached mappings.
**Files.** `src/local_equs_client/state/schema.sql`, `src/local_equs_client/state/db.py`, `src/local_equs_client/state/migrations/001_initial.sql`.
**Depends on.** C0.3
**Done when.** Tables created on first run: `local_files` (manifest mirror + pinned/archived), `saved_views`, `saved_sets`, `cached_sensors`, `cached_mappings`, `schema_version`. A simple migrator runs versioned `.sql` files in order. Unit tests cover schema creation and idempotent re-runs.

### C0.5 — Threading scaffold
**Goal.** The threading model that all background work in M1+ will use: `QThreadPool`-based runnables with cancellation, results delivered via Qt signals.
**Files.** `src/local_equs_client/data_layer/threading.py`.
**Depends on.** C0.2
**Done when.** A `BackgroundJob` base class exposes `run()`, emits `finished(result)` and `failed(error)`, supports cooperative cancellation via a `cancelled` flag and `request_cancel()`. A `JobRunner` wraps `QThreadPool` with `submit(job)` returning a handle. Unit tests (pytest-qt) confirm a simple counting job completes, and a long-running job stops promptly when cancelled.

### C0.6 — Component interface contracts
**Goal.** Lock in the seams where AI agents most often misinterpret intent: Selection Model, Query Planner, Local Library API. Type-hinted stubs with full docstrings, no implementation.
**Files.** `src/local_equs_client/selection/selection_model.py`, `src/local_equs_client/data_layer/query_planner.py`, `src/local_equs_client/data_layer/local_library.py`.
**Depends on.** C0.2
**Done when.** Each module exports a class or protocol with every public method type-annotated and docstring'd. Specifically:
- `SelectionModel`: properties for `tools`, `sensors_canonical`, `sensors_raw`, `time_range`, with `selectionChanged` signal; methods `set_tools`, `set_sensors`, `set_time_range`, `clear`.
- `QueryPlanner`: `plan(selection: Selection, mode: ViewMode, viewport_width_px: int) -> QueryPlan`; `QueryPlan` has `per_tool_queries: list[ToolQuery]`, `target_resolution: timedelta`, `partial_data_warnings: list[str]`.
- `LocalLibrary`: `files_for(tool_id: str, time_range) -> list[LocalFile]`, `pin(file_id)`, `unpin(file_id)`, `total_size_bytes()`, `archived_files()`.
mypy strict passes on all three modules. No business logic yet.

### C0.7 — Settings panel skeleton (data dir only)
**Goal.** Minimum settings UI showing current data directory and letting the user change it. Other settings come in M5.
**Files.** `src/local_equs_client/ui/settings_panel.py`.
**Depends on.** C0.3
**Done when.** Panel opens from main window menu; shows current `paths.data_dir()`; "Browse…" button opens folder picker; saving updates `config.toml` via `Settings`; a restart hint label shows up since hot-reloading the data dir is out of scope for v1.

### C0.8 — Logging
**Goal.** File-based logging to `paths.logs_dir()` with rotation; same configuration loadable from main entrypoint.
**Files.** `src/local_equs_client/config/logging.py`.
**Depends on.** C0.3
**Done when.** `configure_logging()` writes daily-rotating logs to `logs/`, retains 14 days, also mirrors to stderr in dev mode (`EQUS_DEV=1`). Imports anywhere in the codebase that use `logging.getLogger(__name__)` work without further setup.

---

# M1 — Local Client Foundation

Backend is unchanged in M1. Goal: a working app reading local parquet from a configured folder and charting in standard mode. No server, no manifest, no canonical names. **Resist polish.**

### C1.1 — Main window layout
**Goal.** Panel layout per `Project_Plan` §"UI Layer": picker on the left, chart grid in the center, time-range selector across the top.
**Files.** `src/local_equs_client/ui/main_window.py`.
**Depends on.** C0.7
**Done when.** Splitters between picker/charts work and persist size to settings; menu bar present (File, View, Help); window position and size persist across sessions.

### C1.2 — Local Library implementation
**Goal.** Scan `paths.data_dir()` for parquet files and populate the local files table in SQLite.
**Files.** `src/local_equs_client/data_layer/local_library.py` (replace stub from C0.6 with real implementation), `src/local_equs_client/state/dao/local_files.py`.
**Depends on.** C0.6, C0.4
**Done when.** On startup, library scans `data_dir/` recursively, extracts `(tool_id, hour_bucket, min_ts, max_ts, row_count)` per parquet via pyarrow metadata (no full reads), upserts into `local_files`. `files_for(tool_id, time_range)` returns matching rows. Unit tests use fixture parquet files.

### C1.3 — Sensor catalog from parquet schemas
**Goal.** Build an in-memory sensor catalog from the column names in local parquet files. M1 has no canonical names — raw names only.
**Files.** `src/local_equs_client/data_layer/metadata_cache.py`.
**Depends on.** C1.2
**Done when.** `MetadataCache.sensors_for(tool_id)` returns `list[SensorInfo]` with raw name and units (from parquet column metadata if present, else `None`). Cache invalidates on Local Library changes.

### C1.4 — Sensor Picker (flat list)
**Goal.** Flat list picker — no tree, no categories yet. Filters across tool name, sensor raw name, units.
**Files.** `src/local_equs_client/ui/sensor_picker.py`.
**Depends on.** C1.3, C0.6 (`SelectionModel`)
**Done when.** Picker shows all `(tool, raw_sensor)` pairs, with a filter box (debounced 150ms). Selecting items updates `SelectionModel.sensors_raw`. "Selected (N)" header with clear-all. No tree mode, no saved sets — just a list.

### C1.5 — Time Range Selector
**Goal.** Date pickers + draggable region from `Project_Plan` §"UI Layer".
**Files.** `src/local_equs_client/ui/time_range_selector.py`.
**Depends on.** C0.6
**Done when.** Two `QDateTimeEdit` for start/end; a thumbnail strip beneath showing local data extent with a draggable selection region; changes write to `SelectionModel.time_range` (debounced 200ms).

### C1.6 — Selection Model implementation
**Goal.** Replace the C0.6 stub with a working `QObject`-based shared model.
**Files.** `src/local_equs_client/selection/selection_model.py`.
**Depends on.** C0.6
**Done when.** `SelectionModel` is a `QObject` with `selectionChanged` signal emitted on any field change; thread-safe reads; setting the same value twice doesn't emit. Unit tests with pytest-qt.

### C1.7 — Query Planner
**Goal.** Real `plan()` implementation: choose files from Local Library, raw columns per tool, target resolution.
**Files.** `src/local_equs_client/data_layer/query_planner.py`.
**Depends on.** C1.2, C0.6
**Done when.** Given a selection and viewport width, `plan()` returns `QueryPlan` with one `ToolQuery` per tool (paths, raw column names, time range), and a `target_resolution` chosen so each chart yields ~2000 points (rounded to clean buckets: 1s, 10s, 1min, 5min, 1h, 1d). Returns `partial_data_warnings` when the requested range exceeds local extent. Unit tests cover bucket selection at different range sizes.

### C1.8 — Query Engine (DuckDB)
**Goal.** Execute a `QueryPlan`: parallel per-tool queries with SQL downsampling, cancellable, returning Arrow tables.
**Files.** `src/local_equs_client/data_layer/query_engine.py`.
**Depends on.** C1.7, C0.5
**Done when.** `execute(plan, cancel_token) -> dict[tool_id, ArrowTable]`. Uses `read_parquet([...])`, `time_bucket()`, `min/max/avg` aggregations matching `Project_Plan` §"Query-to-Chart Pipeline". Per-tool queries dispatched to `QThreadPool`. Cancellation interrupts in-flight DuckDB connections. Returns Arrow tables (zero-copy to numpy on the consumer side). Unit tests with fixture parquet.

### C1.9 — Query controller wiring
**Goal.** Glue: Selection changes → debounced plan → execute → result delivered to chart grid via signal.
**Files.** `src/local_equs_client/data_layer/query_controller.py`.
**Depends on.** C1.6, C1.7, C1.8
**Done when.** Subscribes to `SelectionModel.selectionChanged`, debounces 150-200ms, cancels any in-flight query, builds plan, runs query, emits `queryCompleted(plan, results)` or `queryFailed(error)`. Cancellation on rapid selection changes verified by tests.

### C1.10 — Chart grid (standard mode, virtualized)
**Goal.** PyQtGraph `GraphicsLayoutWidget` rendering one PlotItem per selected sensor, with linked x-axes and crosshair sync. Virtualization can be simple in M1 (paint all, reuse on selection change); full viewport-based virtualization comes in M4.
**Files.** `src/local_equs_client/ui/chart_grid.py`.
**Depends on.** C1.9
**Done when.** On `queryCompleted`, grid replaces existing plots with one per `(tool, sensor)` pair; avg as solid line; min/max as faint `FillBetweenItem` band; `setXLink()` links all x-axes; mouse hover shows synchronized vertical crosshair across all charts; data updates via `setData()` (no full re-creation).

### C1.11 — Re-query on zoom
**Goal.** Pan/zoom on any linked chart triggers a re-plan at new resolution; old data stays visible until the new query returns; atomic swap.
**Files.** `src/local_equs_client/ui/chart_grid.py`, `src/local_equs_client/data_layer/query_controller.py`.
**Depends on.** C1.10
**Done when.** Dragging or scroll-zoom on a chart updates `SelectionModel.time_range`, controller debounces, new query runs at adjusted resolution. Old plot data not cleared; replaced atomically via `setData()` on completion. Verified manually with a few seconds of stuttering pan — no flicker, no flat-line artifacts.

### C1.12 — Per-tool error isolation
**Goal.** A failure querying one tool (e.g. corrupt file) shows that tool failed but renders the rest.
**Files.** `src/local_equs_client/data_layer/query_engine.py`, `src/local_equs_client/ui/chart_grid.py`.
**Depends on.** C1.10
**Done when.** Query Engine catches exceptions per tool and returns `{tool_id: ArrowTable | QueryError}`; chart grid renders an error placeholder card for failed tools; the rest plot normally. Test forces one tool's path to be invalid.

### C1.13 — "No data in range" handling
**Goal.** Sensor selected but absent from files in range (older data) shows an explicit "no data" message instead of a flat zero line.
**Files.** `src/local_equs_client/data_layer/query_engine.py`, `src/local_equs_client/ui/chart_grid.py`.
**Depends on.** C1.10
**Done when.** When a sensor's column doesn't exist in any file covering the range, the chart shows "No data in range" centered, no line plotted. Test with fixture file missing a column.

### C1.14 — M1 self-demo smoke test
**Goal.** End-to-end manual verification recorded as a checklist.
**Files.** `docs/m1_smoke_test.md`.
**Depends on.** C1.10–C1.13
**Done when.** A documented procedure that, given a fresh checkout and ~50MB of parquet placed in the data dir, takes the developer from `python -m local_equs_client` to a chart of 8 sensors across 3 tools in under 5 minutes, with linked zoom/pan working and one corrupt-file tool isolated. **This is the M1 exit criterion.**

---

# M2 — Server Integration

App pulls from server instead of a hand-managed folder. Backend ingest pipeline goes live; client gets Update + Download Managers and panels.

## Backend stream

### B2.1 — Tool model and migration
**Goal.** Add the `tools` table and FK on `ingest_state.tool_id`.
**Files.** `src/local_equs/data/models/tools.py`, new migration `0002_tools.py`.
**Depends on.** B0.5
**Done when.** Migration creates `tools` per `Backend_plan` §5; FK on `ingest_state.tool_id` activated; `alembic upgrade head` and downgrade clean.

### B2.2 — ParquetFile model and migration
**Goal.** File index table.
**Files.** `src/local_equs/data/models/parquet_files.py`, migration `0003_parquet_files.py`.
**Depends on.** B2.1
**Done when.** Table per `Backend_plan` §5 with all indexes. `(tool_id, hour_bucket)` unique constraint enforced; `relative_path` unique.

### B2.3 — Manifest schemas (pydantic)
**Goal.** Pin the wire format of `/v1/manifest.json`. **This is the schema freeze referenced in `Project_Plan` M2.**
**Files.** `src/local_equs/schemas/manifest.py`.
**Depends on.** B2.2
**Done when.** `ManifestFile` and `Manifest` pydantic models defined; documented in module docstring as "FROZEN as of M2 — adding fields is OK, renaming/removing requires manifest version bump." Fields per `Backend_plan` §5 columns of `parquet_files` plus `tool_id`, `hour_bucket` (ISO-8601 UTC), `relative_path`.

### B2.4 — Manifest service
**Goal.** Build manifest payload + ETag digest cheaply.
**Files.** `src/local_equs/services/manifest_service.py`.
**Depends on.** B2.3
**Done when.** `load_manifest()` returns the full `Manifest`; `manifest_digest()` returns a stable hash from `(max(ingested_at), count(*))` over `parquet_files`; both work against an empty DB.

### B2.5 — Manifest endpoint
**Goal.** `GET /v1/manifest.json` with ETag and `Last-Modified`.
**Files.** `src/local_equs/api/routes/manifest.py`.
**Depends on.** B2.4
**Done when.** First GET returns 200 + ETag; second GET with `If-None-Match: <etag>` returns 304; integration test asserts both flows.

### B2.6 — Files endpoint with Range support
**Goal.** `GET /v1/files/{path:path}` serving parquet bytes via Starlette `FileResponse` with `Range:` and `If-Range:` semantics.
**Files.** `src/local_equs/api/routes/files.py`, `src/local_equs/services/files_service.py`.
**Depends on.** B2.2
**Done when.** Path traversal rejected (resolved path must be under `data_dir`); 404 for missing files; integration test verifies a `Range: bytes=0-1023` request returns 206 with `Content-Range` correct, and a full GET returns 200.

### B2.7 — Sensors endpoint (raw only)
**Goal.** `GET /v1/sensors/{tool_id}.json` returning raw sensor names from parquet schemas. Canonical names land in M3.
**Files.** `src/local_equs/api/routes/sensors.py`, `src/local_equs/services/sensors_service.py`, `src/local_equs/schemas/sensors.py`.
**Depends on.** B2.2
**Done when.** Returns `{tool_id, raw_sensors: [{name, units}], canonical_sensors: [], categories: []}` (canonical/categories empty in M2). Sources raw sensors from the most recent parquet for that tool via pyarrow schema. ETag based on most-recent file's `ingested_at`. Integration test.

### B2.8 — Wrapper API client
**Goal.** Async HTTP client for the upstream wrapper API per S0.2 contract.
**Files.** `src/local_equs/ingest/ducklake_client.py`.
**Depends on.** S0.2, B0.2
**Done when.** Class `DucklakeClient` with methods `get_snapshot()`, `get_changes(tool_id, since)`, `get_data_stream(tool_id, start, end)` (async generator yielding bytes). Uses `httpx.AsyncClient` with `EQUS_UPSTREAM_TIMEOUT_SEC`. Errors mapped to typed exceptions: `UpstreamUnavailable`, `UpstreamNotFound`, `UpstreamTimeout`. Unit tests mock httpx.

### B2.9 — Parquet I/O helpers
**Goal.** pyarrow inspection (size, row count, min/max ts, schema) and SHA-256, both wrapped for `asyncio.to_thread`.
**Files.** `src/local_equs/ingest/parquet_io.py`.
**Depends on.** B0.1
**Done when.** `inspect_parquet(path) -> FileMeta` returns documented fields; `sha256_file(path) -> str` chunks 1MB at a time. Both are sync; callers wrap. Tests with fixture parquet of varying sizes.

### B2.10 — Filesystem helpers
**Goal.** Staging path management and atomic rename.
**Files.** `src/local_equs/ingest/filesystem.py`.
**Depends on.** B0.2
**Done when.** `staging_path() -> Path` returns a unique path under `data_dir/.staging/`; `atomic_replace(staging, final)` calls `os.replace`; `ensure_parent(path)` mkdirs; tests verify atomicity assumption (same FS).

### B2.11 — Hour regeneration
**Goal.** Implement `regenerate_hour(tool_id, hour, snapshot_id)` per `Backend_plan` §4.
**Files.** `src/local_equs/ingest/hour_regen.py`.
**Depends on.** B2.8, B2.9, B2.10, B2.2
**Done when.** End-to-end: stream from upstream → write to staging → inspect → SHA → short-circuit if unchanged → atomic replace → upsert `parquet_files`. Empty rows skipped. Errors during streaming clean up staging file. Integration test against a mock wrapper API.

### B2.12 — Tool refresh
**Goal.** `refresh_tool(tool)` decides which hours to regenerate and runs them.
**Files.** `src/local_equs/ingest/tool_refresh.py`.
**Depends on.** B2.11
**Done when.** Combines: current hour, unsealed hours within `late_arrival_window_hours`, snapshot diff via `DucklakeClient.get_changes`. After regenerating, marks hours older than the window as `sealed=true`. Updates `IngestState.last_seen_snapshot_id` and `last_successful_pull_at`. Per-hour timeout via `asyncio.wait_for`. Integration test.

### B2.13 — Ingest tick orchestration
**Goal.** `run_ingest_tick()` calling `refresh_tool` for every active tool, recording an `IngestRun`.
**Files.** `src/local_equs/ingest/tick.py`.
**Depends on.** B2.12
**Done when.** Per `Backend_plan` §4 code sketch: per-tool wrap with `asyncio.wait_for(timeout=tool_refresh_timeout_sec)`, log+continue on failure, write `IngestRun` row with success/fail counts. Wired into the scheduler replacing the no-op job from B0.6.

### B2.14 — APScheduler event listener
**Goal.** Per `Backend_plan` §4 "Observability". Logs job-completed/error/missed events.
**Files.** `src/local_equs/scheduler.py`.
**Depends on.** B2.13
**Done when.** `add_listener` on `EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED` writes the appropriate log levels. Ground for M5 telemetry events.

### B2.15 — Seed fixture: data structures
**Goal.** Define 20 tools across 4 prc groups with realistic raw sensor names per `Backend_plan` §9.
**Files.** `src/local_equs/seeds/dev.py`.
**Depends on.** B2.1
**Done when.** Module exports `PRC_GROUPS`, `CANONICAL_SENSORS` (M3 will use), `RAW_MAPPINGS` data structures. 20 tool entries, 4 prc groups, ~30 sensors per group with deliberate raw-name variation (e.g. `CHM_PRES` vs `chmbr_p` for the same canonical), and a few intentionally unmapped raw sensors. Module is data-only; no DB writes here.

### B2.16 — Seed fixture: DB writer
**Goal.** Implement `seed-dev` CLI: write tools (and prc groups, mappings — though M3 models lands later, leaving these as placeholders for now is fine; revisit in M3).
**Files.** `src/local_equs/seeds/__init__.py`, `src/local_equs/cli.py`.
**Depends on.** B2.15, B0.8
**Done when.** `python -m local_equs.cli seed-dev` (with guards) writes 20 tools to the `tools` table. PRC group / mapping seeding is deferred but stubbed with a clear TODO referenced from M3 tasks. `clear-dev` deletes everything in `tools`, `parquet_files`, and any optional staging directory.

### B2.17 — Synthetic parquet generator
**Goal.** `seed-dev --with-parquet --weeks N` produces hour-bucketed parquet under `data_dir/`.
**Files.** `src/local_equs/seeds/parquet_gen.py`.
**Depends on.** B2.15, B2.10
**Done when.** Generates `{tool_id}/YYYY/MM/DD/HH.parquet` with 1Hz timestamps and one column per raw sensor on that tool. Plausible sin+noise patterns. Default 4 weeks. After generation, calls `inspect_parquet` and writes corresponding `parquet_files` rows so the manifest endpoint sees them without an ingest tick.

### B2.18 — Path layout contract document
**Goal.** Pin the `{tool_id}/YYYY/MM/DD/HH.parquet` path layout, alongside the manifest schema. **Schema freeze.**
**Files.** `docs/file_layout.md`.
**Depends on.** B2.11, B2.17
**Done when.** Document states the layout, the UTC-only rule, and labels both as part of the M2 wire contract that the client hardcodes.

### B2.19 — Integration test: ingest tick
**Goal.** End-to-end test that ingest tick against a mock upstream produces correct parquet files and DB rows.
**Files.** `tests/integration/test_ingest_tick.py`.
**Depends on.** B2.13
**Done when.** Test starts a `respx` mock returning canned parquet bytes, runs one ingest tick for 2 tools, asserts files appear at the expected paths, `parquet_files` rows match, and a second tick with unchanged data does not bump `ingested_at`.

### B2.20 — Integration test: manifest + files end-to-end
**Goal.** After a seed, manifest lists files and `/v1/files/{path}` serves them with Range.
**Files.** `tests/integration/test_files_endpoint.py`, `tests/integration/test_manifest_endpoint.py`.
**Depends on.** B2.5, B2.6, B2.17
**Done when.** Test seeds DB + a couple of small parquet files, hits `/v1/manifest.json`, picks a file, hits `/v1/files/{path}` and asserts content + Range behavior.

## Client stream

### C2.1 — Server URL setting
**Goal.** Add server hostname to `Settings`; UI in settings panel.
**Files.** `src/local_equs_client/config/settings.py`, `src/local_equs_client/ui/settings_panel.py`.
**Depends on.** C0.7
**Done when.** `Settings.server_url` reads from `config.toml`; settings panel exposes it; first-run wizard prompts when missing.

### C2.2 — Stable client identity
**Goal.** Generate a UUID once at first run and persist it in SQLite state.
**Files.** `src/local_equs_client/state/dao/identity.py`.
**Depends on.** C0.4
**Done when.** `client_id()` returns the same UUID on every call; persisted in `state.db` `app_state` table; first call populates it; reset is not exposed to UI.

### C2.3 — HTTP client wrapper
**Goal.** Single shared HTTP client that injects `X-Client-Id` and `X-App-Version` on every request.
**Files.** `src/local_equs_client/data_layer/http.py`.
**Depends on.** C2.2, C2.1
**Done when.** Returns `requests.Session`-equivalent object with default headers; respects `server_url` from settings; raises typed errors `ServerUnreachable`, `ServerError`, `NotFound`. Tests with `responses`.

### C2.4 — Manifest fetch + cache
**Goal.** Fetch `/v1/manifest.json` with ETag-aware caching; persist ETag in SQLite.
**Files.** `src/local_equs_client/data_layer/update_manager.py` (initial pass), `src/local_equs_client/state/dao/manifest_cache.py`.
**Depends on.** C2.3, C0.4
**Done when.** First call: GET, cache body + ETag. Second call: GET with `If-None-Match`, on 304 returns cached body. Tests cover both paths.

### C2.5 — Update Manager (diff logic)
**Goal.** Compare server manifest to local files; produce add/remove diff.
**Files.** `src/local_equs_client/data_layer/update_manager.py`.
**Depends on.** C2.4, C1.2
**Done when.** `compute_updates() -> UpdateDiff` returns lists of `to_download` and `archived_locally` (in local but absent from manifest). Files whose `sha256` differs from local also appear in `to_download`. Unit tests with synthetic manifest + state.

### C2.6 — Download Manager
**Goal.** Background download of a file list with checksum verification, atomic moves, and Range-resume.
**Files.** `src/local_equs_client/data_layer/download_manager.py`.
**Depends on.** C2.3, C0.5
**Done when.** Per-file: download to `<final_path>.partial`, send Range if a partial exists, verify SHA-256 on completion, `os.replace` to final, update SQLite. On failure mid-download, `.partial` survives for next attempt. Cancellable via threading scaffold. Tests with `responses` simulating Range behavior.

### C2.7 — Updates Panel
**Goal.** UI showing what's available from the server, what's local, and a "Download" action with selective per-file or per-tool checkboxes.
**Files.** `src/local_equs_client/ui/updates_panel.py`.
**Depends on.** C2.5, C2.6
**Done when.** Panel groups available files by tool; shows size and date range; user picks files, hits "Download", sees progress. Cancelled downloads stop promptly. Files marked "archived" (in local but server-deleted) shown distinctly.

### C2.8 — Local Library Panel
**Goal.** UI showing what's on disk: pin/unpin, delete, total size.
**Files.** `src/local_equs_client/ui/local_library_panel.py`.
**Depends on.** C1.2
**Done when.** Sortable table by tool/date/size/pinned. Right-click delete (confirms first). Pin checkbox prevents auto-eviction (reserved for future use). "Used: X / Y GB" footer.

### C2.9 — Sensor catalog from server
**Goal.** Replace C1.3 to source sensor lists from `/v1/sensors/{tool_id}.json` (still raw-only in M2).
**Files.** `src/local_equs_client/data_layer/metadata_cache.py`.
**Depends on.** C2.3
**Done when.** `MetadataCache.refresh_sensors(tool_id)` calls the endpoint, caches the result in `cached_sensors` SQLite table with ETag. Picker uses cache. Falls back to parquet schema when offline (with a stale-cache warning).

### C2.10 — End-to-end M2 demo
**Goal.** Document the fresh-install → check updates → download → chart flow.
**Files.** `docs/m2_smoke_test.md`.
**Depends on.** C2.7, C2.8, C2.9, C1.10
**Done when.** Procedure works against a backend running `seed-dev --with-parquet`. Internal-only test; **do not release broadly** — saved views built here will break in M3 when canonical names land.

---

# M3 — Process Groups & Canonical Names

The heaviest milestone; touches both server and client. End state: pick canonical sensors, app resolves per tool, cross-tool comparison works.

## Backend stream

### B3.1 — PrcGroup model + migration
**Goal.** Add `prc_groups` table with `last_modified_at`/`last_modified_by` for optimistic concurrency.
**Files.** `src/local_equs/data/models/prc_groups.py`, migration `0004_prc_groups.py`.
**Depends on.** B2.1
**Done when.** Per `Backend_plan` §5; FK from `tools.prc_group_id` activated.

### B3.2 — CanonicalSensor and Category models
**Goal.** Add `canonical_sensors` and `categories` tables.
**Files.** `src/local_equs/data/models/sensors.py` (extend), migration `0005_canonical_sensors_and_categories.py`.
**Depends on.** B3.1
**Done when.** Per `Backend_plan` §5. `(prc_group_id, name)` unique constraint enforced. `category_id` nullable FK.

### B3.3 — SensorMapping model
**Goal.** `sensor_mappings` table with the `(tool_id, raw_name)` unique constraint that enforces "raw mapped to two canonicals on same tool" as a DB error.
**Files.** `src/local_equs/data/models/sensors.py` (extend), migration `0006_sensor_mappings.py`.
**Depends on.** B3.2
**Done when.** Insert violating the unique constraint raises `IntegrityError` in tests.

### B3.4 — AuditLog model
**Goal.** `audit_log` table.
**Files.** `src/local_equs/data/models/audit.py`, migration `0007_audit_log.py`.
**Depends on.** B3.1
**Done when.** Per `Backend_plan` §5. JSONB `before` and `after` columns work end-to-end.

### B3.5 — Audit service
**Goal.** Centralized writer for audit events, called from PUT handlers.
**Files.** `src/local_equs/services/audit_service.py`.
**Depends on.** B3.4
**Done when.** `record(actor, entity_type, entity_id, action, before, after)` writes a row in the same transaction as the caller's session. Unit tests verify `before`/`after` are JSON-serializable dicts.

### B3.6 — ProcessGroups service (read)
**Goal.** Service for listing and retrieving prc_groups with members.
**Files.** `src/local_equs/services/process_groups_service.py`.
**Depends on.** B3.1
**Done when.** `list_groups()` and `get_group(id)` return groups with `members: [tool_id]`, `last_modified_at`, `last_modified_by`. Tests.

### B3.7 — ProcessGroups service (update)
**Goal.** Update a prc_group with optimistic concurrency check.
**Files.** `src/local_equs/services/process_groups_service.py` (extend).
**Depends on.** B3.6, B3.5
**Done when.** `update(id, payload, expected_last_modified_at, actor)` raises `StaleVersionError` if DB's value differs; otherwise updates fields, bumps `last_modified_at`/`last_modified_by`, writes audit row, all in one transaction. Tests cover both branches.

### B3.8 — Mappings service (read)
**Goal.** Build the matrix payload for the editor.
**Files.** `src/local_equs/services/mappings_service.py`.
**Depends on.** B3.3, B3.6
**Done when.** `get_matrix(prc_group_id) -> MappingMatrix` returns canonical sensors as rows, member tools as columns, mapping cells, plus per-tool unmapped raw sensor counts. Tests with seeded data.

### B3.9 — Mappings service (bulk replace)
**Goal.** Replace all mappings for a prc_group in one transaction with concurrency check.
**Files.** `src/local_equs/services/mappings_service.py` (extend).
**Depends on.** B3.8, B3.7
**Done when.** `replace(prc_group_id, mappings, expected_last_modified_at, actor)` does: validate (no dup `(tool_id, raw_name)`); 409 on stale; delete-then-insert all rows; write audit row capturing `before` and `after` matrix hashes. Tests for 409, validation failure, success.

### B3.10 — Categories endpoints
**Goal.** `GET /v1/categories` (list) and admin write paths (deferred admin gating to client; server allows writes for now under the same `X-Client-Id` audit trail).
**Files.** `src/local_equs/api/routes/categories.py`, `src/local_equs/services/categories_service.py`, `src/local_equs/schemas/categories.py`.
**Depends on.** B3.2
**Done when.** GET returns `[{id, name, sort_order}]`; PUT `/v1/categories` accepts the full list (replace). ETag on GET. Tests.

### B3.11 — ProcessGroups GET endpoints
**Goal.** `GET /v1/process-groups` and `GET /v1/process-groups/{id}`.
**Files.** `src/local_equs/api/routes/process_groups.py`, `src/local_equs/schemas/process_groups.py`.
**Depends on.** B3.6
**Done when.** Returns lists/objects matching schemas; 404 for unknown id.

### B3.12 — ProcessGroups PUT endpoint
**Goal.** `PUT /v1/process-groups/{id}` with 409 on stale.
**Files.** `src/local_equs/api/routes/process_groups.py` (extend).
**Depends on.** B3.7
**Done when.** Returns 200 + updated body on success; returns 409 with `{reason: "stale", current_modified_at}` on stale. Integration test.

### B3.13 — Mappings GET/PUT endpoints
**Goal.** `GET` and `PUT /v1/process-groups/{id}/mappings`.
**Files.** `src/local_equs/api/routes/process_groups.py` (extend), `src/local_equs/schemas/mappings.py`.
**Depends on.** B3.8, B3.9
**Done when.** GET returns matrix; PUT bulk-replaces; 409 propagated; validation errors return 400 with cell location info. Integration test asserts a concurrent-edit conflict.

### B3.14 — Unmapped sensors endpoint
**Goal.** `GET /v1/tools/{tool_id}/unmapped` returning raw sensors with no mapping.
**Files.** `src/local_equs/api/routes/tools.py`, `src/local_equs/services/sensors_service.py` (extend).
**Depends on.** B3.3
**Done when.** Cross-references the latest parquet schema's columns with `sensor_mappings` for that tool's prc_group; returns the difference.

### B3.15 — Sensors endpoint extended
**Goal.** Extend `/v1/sensors/{tool_id}.json` to return canonical names alongside raw, with their mappings.
**Files.** `src/local_equs/api/routes/sensors.py` (extend), `src/local_equs/services/sensors_service.py` (extend).
**Depends on.** B3.3
**Done when.** Response now includes `canonical_sensors: [{name, display_name, units, category_id, raw_for_this_tool}]` and `categories`. ETag invalidates on mappings change. Tests.

### B3.16 — Seed fixture: extend to mappings + canonicals + categories
**Goal.** `seed-dev` writes prc_groups, canonical sensors, categories, and mappings (with intentional raw-name variation and some unmapped sensors per `Backend_plan` §9).
**Files.** `src/local_equs/seeds/__init__.py` (extend).
**Depends on.** B3.3, B3.10, B2.16
**Done when.** After seed: 4 prc groups exist; ~30 canonical sensors per group; mapping matrix shows variation across tools; at least one tool has 1-2 unmapped raw sensors. Verify via the running server endpoints.

### B3.17 — Integration test: 409 concurrency
**Goal.** Simulate two clients editing the same prc_group; second one gets 409.
**Files.** `tests/integration/test_process_groups_concurrency.py`.
**Depends on.** B3.12, B3.13
**Done when.** Test does GET (snapshots `last_modified_at`), PUT from client A (success), PUT from client B with old timestamp (asserts 409 + body shape). Same test for mappings endpoint.

### B3.18 — Integration test: mapping validation
**Goal.** Confirm DB constraint catches duplicate raw-name mappings.
**Files.** `tests/integration/test_mappings_validation.py`.
**Depends on.** B3.13
**Done when.** PUT with two cells mapping the same raw name on the same tool to different canonicals returns 400 with a useful message; nothing partially written.

## Client stream

### C3.1 — Metadata Cache extended for canonicals + mappings
**Goal.** Cache canonical sensors, categories, and mappings per tool/prc_group; refresh on demand or when manifest indicates upstream changes.
**Files.** `src/local_equs_client/data_layer/metadata_cache.py`, `src/local_equs_client/state/dao/metadata.py`.
**Depends on.** B3.15, C2.9
**Done when.** `MetadataCache` exposes `canonical_sensors(prc_group_id)`, `category_tree()`, `mapping(tool_id, canonical_name) -> raw_name | None`, all backed by SQLite cache with ETag on the relevant endpoint. Tests with `responses`.

### C3.2 — Query Planner: canonical → raw resolution
**Goal.** Update Query Planner so a `Selection` of canonical sensors expands to per-tool raw-name queries.
**Files.** `src/local_equs_client/data_layer/query_planner.py`.
**Depends on.** C3.1, C1.7
**Done when.** Given canonical sensors and a set of tools, planner uses `MetadataCache.mapping()` to produce per-tool raw column names. If a tool lacks a mapping for a selected canonical, the plan records `(tool_id, canonical, missing)`; downstream renders "no data" for that pair. Unit tests cover all branches.

### C3.3 — Sensor Picker: tree mode
**Goal.** Replace the M1 flat list with a tree: Tool → Category → Sensor. Tri-state checkboxes; tool nodes show counts.
**Files.** `src/local_equs_client/ui/sensor_picker.py`.
**Depends on.** C3.1, C1.4
**Done when.** Tree builds from `MetadataCache`. Categories show under each tool, with canonical sensors as leaves. Tri-state propagation works (parent shows partial). Counts on tool nodes (e.g. "Etcher A1 (12/30)"). Expansion state persisted in SQLite. Tests with pytest-qt.

### C3.4 — Sensor Picker: search section
**Goal.** Add the search section above the tree per `Project_Plan` §"Sensor Picker Design".
**Files.** `src/local_equs_client/ui/sensor_picker.py` (extend).
**Depends on.** C3.3
**Done when.** Single text box, debounced 150ms, in-memory match across canonical name, tool name, units, description. Results as flat list grouped by tool with breadcrumb (Tool / Category / Sensor). Selecting from search updates the same SelectionModel as tree. Tests.

### C3.5 — Sensor Picker: hover detail pane
**Goal.** When hovering a sensor in the tree or search results, show: description, sample rate, files containing it locally, server vs. local date range.
**Files.** `src/local_equs_client/ui/sensor_picker.py` (extend).
**Depends on.** C3.3, C1.2
**Done when.** Detail pane on the right of the picker populates within 50ms; pulls from MetadataCache + LocalLibrary. No DuckDB hits.

### C3.6 — Saved Sets section (read-only stub)
**Goal.** Top section in the picker showing saved sets. Read-only in M3 (full CRUD in M5) — empty state with placeholder.
**Files.** `src/local_equs_client/ui/sensor_picker.py` (extend).
**Depends on.** C3.3
**Done when.** Section renders with "No saved sets yet — coming in M5" empty state. Placeholder for M5 wiring is in place.

### C3.7 — "Selected (N)" header
**Goal.** Header above the picker sections showing total selected count, with "Clear all" and a (disabled in M3) "Save as set…" action.
**Files.** `src/local_equs_client/ui/sensor_picker.py` (extend).
**Depends on.** C3.3
**Done when.** Count updates from SelectionModel. Clear-all empties the model. Save action shows tooltip "Coming in M5".

### C3.8 — Mapping Editor: matrix view (read-only)
**Goal.** Matrix view of canonical (rows) × tools (columns), cells showing mapped raw names. No editing in M3 — that's M5.
**Files.** `src/local_equs_client/ui/mapping_editor.py`.
**Depends on.** C3.1, B3.13
**Done when.** Editor opens from menu; prc_group selector at top; matrix renders with sticky first column and header row; empty cells styled red; categories tab present but disabled. Loads via `GET /v1/process-groups/{id}/mappings`. Permissions stub gate: editor accessible to all in M3, but a feature flag (`Settings.permissions_simulate_admin`) hides the (still-disabled) editing affordances when false.

### C3.9 — Mapping Editor: detail panel
**Goal.** Right-side collapsible detail panel showing canonical name + description, units, all raw mappings, audit history (read from server in M5).
**Files.** `src/local_equs_client/ui/mapping_editor.py` (extend).
**Depends on.** C3.8
**Done when.** Selecting a row populates the panel; collapse/expand persists in settings; audit history shows "Coming in M5" placeholder.

### C3.10 — Cross-tool selection demo
**Goal.** Verify the headline use case: "select chamber_pressure on all etchers" works end-to-end.
**Files.** `docs/m3_smoke_test.md`.
**Depends on.** C3.2, C3.3, C1.10
**Done when.** Documented procedure: open app → tree shows Etcher prc_group's tools → select `chamber_pressure` on the canonical level → all etchers' charts populate with correct raw resolution per tool. **This is the M3 exit criterion** and the first version that's "genuinely useful."

---

# M4 — View Modes & 100-Chart Scenario

Mostly UI work on solid M1–M3 foundations. No backend changes.

### C4.1 — View Controller introduction
**Goal.** Formalize the View Controller from `Project_Plan` §"Architecture" as the routing layer between Selection Model and the chart grid.
**Files.** `src/local_equs_client/selection/view_controller.py`.
**Depends on.** C1.6, C1.9
**Done when.** Holds `mode: ViewMode` ("overview" | "standard" | "focus"), `group_by: GroupBy` ("sensor" | "tool" | "both"). Routes selection + mode to query plan. Existing standard mode now goes through the controller (regression-tested against M1 smoke test).

### C4.2 — Adaptive resolution per mode
**Goal.** Query Planner picks resolution based on mode: ~100 points/chart overview, ~2000 standard, ~5000 focus.
**Files.** `src/local_equs_client/data_layer/query_planner.py`.
**Depends on.** C4.1, C1.7
**Done when.** `plan(selection, mode, viewport_width)` returns a resolution scaled to `mode`. Tests over each mode at varying ranges.

### C4.3 — Query Cache (LRU)
**Goal.** LRU cache keyed on `(tool, sensors, range, resolution) → ArrowTable`, sized in MB.
**Files.** `src/local_equs_client/data_layer/query_cache.py`.
**Depends on.** C1.8
**Done when.** ~200 MB cap; eviction by LRU on memory pressure (pyarrow nbytes); thread-safe. Mode switches benefiting from cache (e.g. overview → standard at the same range) skip re-querying when cache hits at the new resolution; otherwise cache misses. Tests cover hit/miss/evict.

### C4.4 — Progressive rendering scaffolding
**Goal.** Chart grid lays out empty placeholder chart frames immediately on selection change, populates as each tool's query completes.
**Files.** `src/local_equs_client/ui/chart_grid.py` (extend).
**Depends on.** C4.1
**Done when.** Frames appear within 50ms of selection change; per-tool plot fills in as `queryCompleted` events arrive. Visible "X of Y sensors loaded" indicator at the top. No spinner-then-everything-at-once flash.

### C4.5 — Viewport-priority query order
**Goal.** Query Engine submits per-tool queries in viewport order (visible first), then off-screen.
**Files.** `src/local_equs_client/data_layer/query_engine.py`, `src/local_equs_client/ui/chart_grid.py`.
**Depends on.** C4.4
**Done when.** Chart grid emits a "viewport changed" signal with currently-visible chart IDs; query engine re-orders pending queries accordingly. Manual test: scrolling reveals charts that begin loading after they enter view.

### C4.6 — Virtualized chart grid
**Goal.** Render only charts in viewport + small buffer; recycle PlotItems on scroll.
**Files.** `src/local_equs_client/ui/chart_grid.py` (significant rewrite).
**Depends on.** C4.4, C4.5
**Done when.** With 100+ charts requested, only ~15 PlotItems exist at any time. Scrolling smoothly recycles. Crosshair sync still works across visible charts. Linked x-range stored in shared model so off-screen charts get current range when they re-enter view.

### C4.7 — Overview mode (sparkline grid)
**Goal.** Tiny sparklines (~200×60 px), no axes, name + current value. Click promotes to focus.
**Files.** `src/local_equs_client/ui/chart_grid.py` (mode plumbing), `src/local_equs_client/ui/sparkline.py`.
**Depends on.** C4.1, C4.2
**Done when.** Toggling to overview mode lays out a dense grid; ~100 sparklines fit on a typical screen; click handler switches to focus mode with that sensor selected. No re-query needed if cache has overview-resolution data.

### C4.8 — Focus mode (1-4 charts)
**Goal.** Small selected set rendered large with full axes, statistics, high resolution.
**Files.** `src/local_equs_client/ui/chart_grid.py` (mode plumbing).
**Depends on.** C4.1, C4.2
**Done when.** Toggling to focus shows up to 4 selected charts at full size; statistics strip per chart (min/max/mean over visible range); higher-resolution re-query at focus resolution.

### C4.9 — Group-by axis controls
**Goal.** Toggle in the View Controller panel: sensor (default), tool, sensor × tool.
**Files.** `src/local_equs_client/ui/view_mode_bar.py`.
**Depends on.** C4.1
**Done when.** Three radio options. Selecting changes how chart grid groups: one chart per canonical with tools overlaid; one per tool; one per pair. "Tool" warns when sensors have wildly different scales.

### C4.10 — Soft guardrail at >50 charts
**Goal.** Warning banner when selection produces >50 series with a one-click "Switch to Overview" button.
**Files.** `src/local_equs_client/ui/chart_grid.py` (extend).
**Depends on.** C4.7
**Done when.** Banner non-blocking; appears at >50, escalates to "Are you sure?" at >200. Button switches mode without changing selection.

### C4.11 — Performance test: 100 sensors × 8 tools
**Goal.** Documented benchmark proving the grid handles the worst case smoothly.
**Files.** `docs/m4_performance.md`.
**Depends on.** C4.6, C4.7
**Done when.** With seed data, selecting 100 canonical sensors across 8 tools (800 series) in overview mode, the grid lays out and starts populating within 1.5s; standard mode with viewport virtualization scrolls without dropped frames. Numbers logged. **M4 exit criterion.**

---

# M5 — Polish & Admin

Backend rounds out telemetry, retention, and full health. Client gets saved sets, data table, full mapping editor, settings, telemetry, exports.

## Backend stream

### B5.1 — TelemetryEvent model + migration
**Goal.** Telemetry table.
**Files.** `src/local_equs/data/models/telemetry.py`, migration.
**Depends on.** B0.5
**Done when.** Per `Backend_plan` §5. JSONB `event_data` indexed via GIN if quick queries on subkeys are needed (decide based on M5 telemetry questions).

### B5.2 — Telemetry endpoint
**Goal.** `POST /v1/telemetry` accepting the documented envelope, returning 202.
**Files.** `src/local_equs/api/routes/telemetry.py`, `src/local_equs/services/telemetry_service.py`, `src/local_equs/schemas/telemetry.py`.
**Depends on.** B5.1
**Done when.** Validates envelope (`client_id`, `app_version`, `timestamp`, `event_type`, `event_data`); writes one row per event; supports batched payloads (`events: [...]`); returns 202 even on partial validation success (logs invalid items). Integration test.

### B5.3 — Telemetry archive job
**Goal.** Nightly: archive events older than 7 days to `data/_telemetry/YYYY-MM-DD.parquet`, then DELETE from Postgres.
**Files.** `src/local_equs/services/telemetry_archive.py`, scheduler registration in `src/local_equs/scheduler.py`.
**Depends on.** B5.2
**Done when.** Job runs via APScheduler at a fixed UTC hour. Archive path queryable via DuckDB. After archive, `DELETE FROM telemetry_events WHERE timestamp < cutoff`. Idempotent: re-running for same date overwrites parquet but never drops un-archived rows. Test.

### B5.4 — Real retention tick
**Goal.** Replace the M0 no-op with the full implementation per `Backend_plan` §3.
**Files.** `src/local_equs/retention/cleanup.py`, `src/local_equs/retention/disk_usage.py`.
**Depends on.** B2.2
**Done when.** Layer 1 (time): `cutoff = now - retention_weeks` (default 8); delete each row, then unlink file. Layer 2 (size): while `disk_usage > disk_threshold_pct`, delete oldest. Order strictly DB-first then disk per the source plan. Records `RetentionRun` with both counts. Logs `WARNING` on size eviction. Tests cover: time-only deletes, size-only deletes (synthetic huge files), DB-fail-after-disk-fail recovery (orphan reaper picks up next run).

### B5.5 — Orphan reaper
**Goal.** Periodic check that filesystem files exist for every `parquet_files` row, and vice versa.
**Files.** `src/local_equs/retention/orphan_reaper.py`.
**Depends on.** B5.4
**Done when.** Hourly job (separate from retention) finds: files on disk with no DB row → delete file with `WARNING`; DB rows with no file → mark and log (deletion deferred to retention). Tests with simulated orphans.

### B5.6 — Health endpoint extended
**Goal.** Real values for `disk_pct`, `last_ingest_at`, `last_retention_at`, `upstream_reachable`.
**Files.** `src/local_equs/api/routes/health.py` (extend), `src/local_equs/services/health_service.py` (extend).
**Depends on.** B5.4, B2.13
**Done when.** Each field populated from the right source. `upstream_reachable` is a cheap `HEAD` against the wrapper API health endpoint with a short timeout (~2s) cached for ~30s. Test asserts shape and reasonable latency.

### B5.7 — Job-listener telemetry
**Goal.** APScheduler job events (executed/error/missed) write `TelemetryEvent` rows.
**Files.** `src/local_equs/scheduler.py` (extend).
**Depends on.** B5.2
**Done when.** Each ingest and retention tick produces telemetry events with relevant context (success counts, durations, error info). Verify by querying telemetry after running ticks.

### B5.8 — Mapping audit history endpoint
**Goal.** `GET /v1/process-groups/{id}/audit?limit=N` returning recent audit entries for the editor's history tab.
**Files.** `src/local_equs/api/routes/process_groups.py` (extend), `src/local_equs/services/audit_service.py` (extend).
**Depends on.** B3.5
**Done when.** Returns most recent N entries (default 50), filtered by `entity_type IN ('prc_group', 'mapping')`. Test.

## Client stream

### C5.1 — Saved Sets: full CRUD
**Goal.** Replace the M3 read-only stub with full create/rename/delete and load-into-selection.
**Files.** `src/local_equs_client/ui/sensor_picker.py` (extend), `src/local_equs_client/state/dao/saved_sets.py`.
**Depends on.** C3.6
**Done when.** "Save current selection…" creates a new set in SQLite (`saved_sets` table). Click loads into SelectionModel. Shift-click adds to current selection. Right-click → rename / delete. Tests.

### C5.2 — Data Table view
**Goal.** Virtualized table of raw values (no downsampling) with separate query path from charts.
**Files.** `src/local_equs_client/ui/data_table.py`, `src/local_equs_client/data_layer/query_engine.py` (extend).
**Depends on.** C1.8
**Done when.** Table tab in main window. Query is a separate `read_parquet()` with no `time_bucket`, with `LIMIT/OFFSET` driven by virtualization. Sortable columns. Visible row count never exceeds ~200. Tests.

### C5.3 — Mapping Editor: full edit support
**Goal.** Promote the read-only matrix from C3.8 to fully editable.
**Files.** `src/local_equs_client/ui/mapping_editor.py` (extend).
**Depends on.** C3.8, B3.13
**Done when.** Click any cell → dropdown of that tool's raw sensors, filterable, showing units. No free-text. Save button bulk-replaces via `PUT /v1/process-groups/{id}/mappings` with `last_modified_at` from the GET. 409 → modal "X edited at Y; reload? (your edits will be lost)". Tests with mocked server.

### C5.4 — Mapping Editor: validation indicators
**Goal.** Visual cell states per `Project_Plan` §"Mapping Editor".
**Files.** `src/local_equs_client/ui/mapping_editor.py` (extend).
**Depends on.** C5.3
**Done when.** Empty cells red; low-confidence (units mismatch) yellow; canonical with zero tools mapped flagged in detail panel; canonical missing description flagged similarly. Hard error (duplicate raw on same tool) blocks save with cell highlight.

### C5.5 — Mapping Editor: audit history tab
**Goal.** Right detail panel includes audit history sourced from B5.8.
**Files.** `src/local_equs_client/ui/mapping_editor.py` (extend).
**Depends on.** B5.8, C3.9
**Done when.** Tab shows last 50 changes for the prc_group; each entry: timestamp, actor, action, brief summary. Reload-on-open.

### C5.6 — Categories tab (admin)
**Goal.** Categories management UI in the Mapping Editor, gated by the permissions stub.
**Files.** `src/local_equs_client/ui/categories_tab.py`, `src/local_equs_client/ui/mapping_editor.py` (extend).
**Depends on.** B3.10, C3.8
**Done when.** Tab lists categories with reorder + rename + add + delete. Save calls `PUT /v1/categories`. Hidden when `permissions.is_admin()` returns false.

### C5.7 — Permissions wiring
**Goal.** Real `Permissions` module; reads `Settings.permissions_simulate_admin` in v1; documents the integration seam for the future real source.
**Files.** `src/local_equs_client/data_layer/permissions.py`.
**Depends on.** C3.8
**Done when.** Module exposes `is_admin() -> bool`; current implementation reads the simulate flag; module docstring documents how to swap in a real auth source. UI gates use this exclusively.

### C5.8 — CSV export
**Goal.** Export current chart data and current data table to CSV.
**Files.** `src/local_equs_client/ui/export.py`.
**Depends on.** C5.2
**Done when.** File menu → Export → CSV. Chart export: per-sensor columns, time as ISO-8601 UTC; Data table export: as displayed. Tests.

### C5.9 — PNG export
**Goal.** Export chart grid (visible viewport) as PNG.
**Files.** `src/local_equs_client/ui/export.py` (extend).
**Depends on.** C1.10
**Done when.** File menu → Export → PNG. Uses PyQtGraph's `ImageExporter`. Resolution sufficient for printing.

### C5.10 — Settings panel: complete
**Goal.** All settings from `Project_Plan` §"Configuration & Storage": data dir, server hostname, telemetry opt-out (default on), update check frequency.
**Files.** `src/local_equs_client/ui/settings_panel.py` (extend).
**Depends on.** C0.7, C2.1
**Done when.** All fields persist to `config.toml` and `Settings`. Telemetry opt-out gates the telemetry client. Update check frequency drives the auto-updater (M6).

### C5.11 — Telemetry client
**Goal.** Queue events locally, flush periodically to `/v1/telemetry`, survive offline.
**Files.** `src/local_equs_client/data_layer/telemetry_client.py`, `src/local_equs_client/state/dao/telemetry_queue.py`.
**Depends on.** C2.3, C5.10
**Done when.** Events queued in SQLite. Flush every 60s in batches of up to 50. On 5xx or network error, leave in queue and retry. Respect opt-out. `Telemetry.event(type, **data)` is the single entry point used everywhere. Tests with `responses`.

### C5.12 — Telemetry events: app lifecycle
**Goal.** Wire `app_start` and `app_exit` events.
**Files.** `src/local_equs_client/main.py` (extend).
**Depends on.** C5.11
**Done when.** Events emitted with `app_version`, OS info (no PII), Python version, install duration since last `app_exit`.

### C5.13 — Telemetry events: queries
**Goal.** Wire `query_run` and `query_failed` events.
**Files.** `src/local_equs_client/data_layer/query_controller.py` (extend).
**Depends on.** C5.11
**Done when.** Each completed query emits `query_run` with `{tool_count, sensor_count, range_seconds, resolution_seconds, latency_ms}`. Failures emit `query_failed` with `{error_type, partial_results: bool}`.

### C5.14 — Telemetry events: downloads & mappings
**Goal.** Wire download and mapping_edit events.
**Files.** `src/local_equs_client/data_layer/download_manager.py` (extend), `src/local_equs_client/ui/mapping_editor.py` (extend).
**Depends on.** C5.11
**Done when.** `download_started/completed/failed` fired per file; `update_check` fired when manifest is polled. `mapping_edit` fired on each successful `PUT /mappings`.

### C5.15 — Crash handler
**Goal.** Top-level exception handler around the Qt event loop and worker threads sends an `error` telemetry event with traceback.
**Files.** `src/local_equs_client/main.py` (extend), `src/local_equs_client/data_layer/threading.py` (extend).
**Depends on.** C5.11
**Done when.** `sys.excepthook` and `threading.excepthook` both wired. Qt: install on `QApplication.exceptionhandler` if available, plus a guard in the main run loop. The handler also writes to the rotating log file. Tests.

### C5.16 — Error handling pass
**Goal.** Walk every "things that go wrong" case in `Project_Plan` and confirm UX. Document outcomes.
**Files.** `docs/error_handling.md`.
**Depends on.** C5.15, C1.12, C1.13
**Done when.** Document lists each failure mode (corrupt file, sensor missing in range, empty range, network error, server 5xx, 409 stale mapping, disk full on download, etc.) and the user-visible behavior; each behavior has an automated or scripted manual test reference.

---

# M6 — Distribution

Make installation and updates work for 100 users without intervention.

## Backend stream

### B6.1 — App version endpoint
**Goal.** `GET /v1/app-version` returning `{version, download_url, sha256, release_notes}` for the auto-updater.
**Files.** `src/local_equs/api/routes/app_version.py`, `src/local_equs/services/app_version_service.py`, `src/local_equs/schemas/app_version.py`.
**Depends on.** B0.7
**Done when.** Reads from a config file or DB table that the developer updates on each release; ETag honored; integration test.

### B6.2 — Installer hosting
**Goal.** Static hosting for signed installer binaries under a stable URL.
**Files.** `src/local_equs/api/routes/installers.py` (or sidecar config).
**Depends on.** B6.1
**Done when.** Installers served as static files at `/v1/installers/{version}/{filename}`. Resolves whether the FastAPI app or a separate static server hosts these (defer to nginx sidecar if file traffic warrants). Decision recorded in `docs/distribution.md`.

## Client stream

### C6.1 — Nuitka build configuration
**Goal.** Reproducible Nuitka build producing a standalone Windows executable.
**Files.** `build/nuitka.cmd`, `build/build_config.py`, `pyproject.toml` (build deps).
**Depends on.** —
**Done when.** Single command produces `dist/LocalEQUS.exe` (or `LocalEQUS/` standalone folder) on Windows. Excludes test code. Bundles PySide6, PyQtGraph, DuckDB. Documented in `build/README.md`.

### C6.2 — Inno Setup installer script
**Goal.** Inno Setup `.iss` script wrapping the Nuitka output.
**Files.** `build/installer.iss`.
**Depends on.** C6.1
**Done when.** Compiling produces `LocalEQUS-Setup-{version}.exe`. Installer creates Start Menu entries; registers in Add/Remove Programs; supports `/SILENT` and `/VERYSILENT`; default install path `%LOCALAPPDATA%\Programs\LocalEQUS`; preserves user state (`%LOCALAPPDATA%\LocalEQUS\`) on uninstall.

### C6.3 — Code signing integration
**Goal.** Build pipeline signs both the executable and installer using the OV certificate from S0.1.
**Files.** `build/sign.cmd`, `build/README.md` (extend).
**Depends on.** C6.2, S0.1
**Done when.** Sign step runs after Nuitka and after Inno Setup compile; uses `signtool` with timestamping; `signtool verify` confirms a valid signature. Cert/key not in repo (env-var-based path). Documented procedure in `build/README.md`.

### C6.4 — Auto-updater: poll + download + verify
**Goal.** Thin updater that checks `/v1/app-version` on launch (per `Settings.update_check_frequency`), downloads the signed installer, verifies SHA-256.
**Files.** `src/local_equs_client/data_layer/update_client.py`.
**Depends on.** B6.1, C2.3
**Done when.** Polls on schedule. If newer version is available, prompts user. Downloads to `%LOCALAPPDATA%\LocalEQUS\updates\`. Verifies SHA-256. Logs progress and outcome.

### C6.5 — Auto-updater: hand-off
**Goal.** Once verified, hand off to Inno Setup with `/SILENT`; current process exits cleanly.
**Files.** `src/local_equs_client/data_layer/update_client.py` (extend).
**Depends on.** C6.4, C6.2
**Done when.** Spawns the installer with `/SILENT /CLOSEAPPLICATIONS` (or equivalent), then `QApplication.quit()`. After installer completes, the new version starts. Manually tested across 0.1.0 → 0.1.1.

### C6.6 — Crash reporting wiring (already present)
**Goal.** Confirm the M5 telemetry `error` events flow correctly from a packaged build.
**Files.** `docs/m6_smoke_test.md`.
**Depends on.** C5.15, C6.1
**Done when.** Run the packaged installer, induce a crash (debug build path), verify a corresponding `error` event appears in server telemetry within ~2 minutes.

### C6.7 — M6 exit checklist
**Goal.** Document the rollout plan: 5-10 internal testers first, then full ramp.
**Files.** `docs/rollout_plan.md`.
**Depends on.** C6.5
**Done when.** Procedure documents: how to update `app-version` data on the server; how to publish an installer; rollback procedure (revert `app-version`, accept that already-updated clients keep the new version); first 5-10 user list; ramp criteria. **M6 exit criterion.**

---

# Cross-cutting working notes for AI agents

These guidelines apply to every task in this plan. Hand them to agents along with the task.

**Don't expand scope.** If a task says "read-only matrix view," resist adding inline editing because "it'd be easy." Editing has its own task with its own concurrency considerations.

**Honor the layer rules.** `services/` (backend) and `data_layer/` (client) never import from API or UI modules. Same for `ingest/` and `retention/`: peers; never import each other; both depend only on `data/` and `services/`.

**Tests are part of "done."** Every task adds tests for the code it touches. Unit tests stay fast (no DB, no Qt event loop). Integration tests live in `tests/integration/` and are allowed to be slower.

**Blocking calls inside async code.** On the server, any sync I/O (pyarrow, hashing, file I/O) must be wrapped in `asyncio.to_thread`. On the client, any blocking work must run via the threading scaffold (C0.5), not on the GUI thread.

**Schema freezes are real.** The manifest schema (M2) and the path layout (M2) are pinned. Adding fields is OK; renaming or removing requires a coordinated client/server change.

**No DuckDB on the server.** Server uses pyarrow only for parquet inspection. DuckDB is exclusively a client-side dependency.

**Concurrency model.** Optimistic with `last_modified_at` per `Project_Plan` and `Backend_plan`. Don't sneak in row-level locks or merge logic.

**Headers are observable, not authoritative.** `X-Client-Id` and `X-App-Version` are for telemetry attribution, not authorization. There's no auth in v1.

**Don't reach for new dependencies.** If a task can be solved with the stack already chosen, do that. New deps need a one-line justification in the PR.

---

# Open questions surfaced for the developer

Carried forward from `Backend_plan` §13 and `Project_Plan` — these block specific tasks and need answers before those tasks start.

| ID | Question | Blocks |
|---|---|---|
| OQ.1 | Wrapper API contract: data endpoint format, snapshot/changes/data routes, auth, error semantics | B2.8, B2.11 |
| OQ.2 | Probe configuration: who owns it, what path, what timeouts | Deployment, B5.6 |
| OQ.3 | `terminationGracePeriodSeconds` on the pod (need ≥ 120s) | Deployment |
| OQ.4 | Env var injection mechanism (ConfigMap + Secret? UI? `.env`?) | Deployment |
| OQ.5 | First-run ingest cost: how heavy is 8 weeks × 20 tools all at once? | Startup probe budget |
| OQ.6 | Real tool list and prc group definitions | Replaces seed fixture content (not schema) |
| OQ.7 | PVC mount path inside the pod | `EQUS_DATA_DIR` default |
| OQ.8 | M0 client spike result: is the stack fast enough? | Whether M1+ proceeds on this stack at all |
| OQ.9 | `permissions_simulate_admin` vs. real source: when does the real source land? | M5 admin features remain stub-gated until then |