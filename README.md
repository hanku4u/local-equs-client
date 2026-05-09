# Local EQUS — Client

Desktop client for the Local EQUS sensor data exploration platform.

See [`mvp-implementation-plan.md`](./mvp-implementation-plan.md) for the milestone breakdown
(M0 spike & skeleton through M6 distribution) and the full task list.

## Status

Scaffolded only — no implementation yet. The next steps are:

1. **C0.1** — performance spike in `spike/` to validate the stack.
2. **C0.2+** — fill in modules under `src/local_equs_client/` per the plan.

## Stack

- Python 3.12
- PySide6 (UI)
- PyQtGraph (charts)
- DuckDB (query engine)
- pyarrow (parquet I/O)
- httpx / requests (HTTP)

## Repository layout

```
src/local_equs_client/
  config/         paths, settings, logging
  data_layer/     query pipeline, downloads, telemetry, update client
  selection/      shared selection model + view controller
  state/          SQLite schema, migrations, DAOs
  ui/             PySide6 panels and widgets
tests/
  unit/           fast tests, no DB, no QApplication
  integration/    real QApplication via pytest-qt
spike/            throwaway M0 performance spike
build/            Nuitka + Inno Setup packaging (M6)
docs/             milestone smoke tests, design notes
```

## Development setup

Once an implementation exists:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m local_equs_client
```
