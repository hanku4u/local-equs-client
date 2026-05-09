# Build & packaging (M6)

Scripts in this directory are populated during M6 (`C6.1`–`C6.3`):

- `nuitka.cmd` — Nuitka build producing `dist/LocalEQUS.exe` (C6.1)
- `build_config.py` — shared Nuitka build configuration (C6.1)
- `installer.iss` — Inno Setup script wrapping the Nuitka output (C6.2)
- `sign.cmd` — Authenticode signing of executable + installer (C6.3)

Build artifacts (`_nuitka/`, `dist/`, `__pycache__/`) inside this directory are
gitignored; only the scripts themselves are committed.
