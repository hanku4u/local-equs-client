# Build & packaging (M6)

Scripts in this directory are populated during M6 (`C6.1`–`C6.3`).

## C6.1 — Nuitka build

Produces a **standalone Windows folder** at `dist/LocalEQUS/` containing
`LocalEQUS.exe` alongside every native DLL it needs.

### One-time setup

From the repo root in a Python 3.12+ environment:

```cmd
pip install -e ".[build]"
```

This pulls Nuitka + zstandard + ordered-set into the active environment.

### Build

```cmd
build\nuitka.cmd
```

or, to delete the previous `dist/` first for a clean build:

```cmd
build\nuitka.cmd --clean
```

Either form delegates to `python build/build_config.py`. The build:

- Bundles PySide6 (via Nuitka's `pyside6` plugin) and the native DLLs
  for DuckDB and PyArrow.
- Strips the test suite (`tests/`, `pytest`).
- Stamps file/product version, company, and product name from
  `pyproject.toml` so Windows Explorer + signing tools recognize the
  executable correctly.
- Disables the console window so the GUI doesn't spawn a black
  terminal on launch.

Expected output: `dist/LocalEQUS/LocalEQUS.exe` plus its `.dist` folder
of dependencies. The whole folder is what the Inno Setup installer
(C6.2) consumes.

### Reproducibility

The exact Nuitka invocation lives in `build_config.py:nuitka_args` —
that single function is the build's source of truth. Bumping the
project version in `pyproject.toml` automatically updates the .exe
metadata on the next build.

## C6.2 — Inno Setup installer

Wraps the Nuitka folder from C6.1 into `dist/LocalEQUS-Setup-{version}.exe`.

### One-time setup

Install **Inno Setup 6** from https://jrsoftware.org/isinfo.php. The
driver looks for `iscc.exe` in the default install path; if you
installed it somewhere else, set `$ISCC` to the full path.

### Build the installer

```cmd
build\nuitka.cmd          REM produces dist\LocalEQUS\
build\installer.cmd       REM produces dist\LocalEQUS-Setup-X.Y.Z.exe
```

The installer is **per-user** (no UAC prompt, installs to
`%LOCALAPPDATA%\Programs\LocalEQUS`), registers in Add/Remove Programs,
adds a Start Menu shortcut, and offers a "Launch LocalEQUS" checkbox on
the final wizard page. Silent installs (`/SILENT`, `/VERYSILENT`) skip
the launch checkbox automatically.

User state under `%LOCALAPPDATA%\LocalEQUS\` (config.toml, state.db,
telemetry queue) is **not** removed on uninstall — that's intentional.

## C6.3 — Authenticode signing

Signs the bundled `LocalEQUS.exe` and the installer `.exe` with the OV
code-signing certificate, timestamps via DigiCert, and verifies the
result with `signtool verify /pa`.

### One-time setup

Install a **recent Windows SDK** so `signtool.exe` is available. The
driver searches `$SIGNTOOL`, then PATH, then the default SDK install
paths under `C:\Program Files (x86)\Windows Kits\10\bin\`.

### Configure the certificate

Set **one** of these env-var groups before invoking `sign.cmd`. The
.pfx group wins if both are set.

**.pfx file:**

```cmd
set SIGNING_CERT=C:\secure\codesign.pfx
set SIGNING_PASSWORD=...
```

**Windows certificate store** (EV certs on hardware tokens):

```cmd
set SIGNING_THUMBPRINT=<hex SHA-1 thumbprint, no separators>
```

Optional overrides:

```cmd
set SIGNING_TIMESTAMP_URL=http://timestamp.digicert.com  REM default
set SIGNTOOL=C:\path\to\signtool.exe                     REM auto-discovered
```

The cert path and password are read from the environment — they are
never committed to the repo.

### Sign

```cmd
build\sign.cmd                       REM signs both default targets
build\sign.cmd path\to\file.exe      REM signs just that file
```

The default targets are `dist\LocalEQUS\LocalEQUS.exe` and the most
recent `dist\LocalEQUS-Setup-*.exe`. Each file is signed with
`/fd SHA256` and timestamped via RFC 3161; then verified with
`signtool verify /pa /v`. A non-zero exit code halts the pipeline so
malformed signatures don't reach distribution.

### Pipeline placement

The signing step runs **after** both the Nuitka build and the Inno
Setup compile, since the installer must include the already-signed
.exe for SmartScreen to pick up the signature on first launch:

```cmd
build\nuitka.cmd
build\sign.cmd dist\LocalEQUS\LocalEQUS.exe
build\installer.cmd
build\sign.cmd dist\LocalEQUS-Setup-X.Y.Z.exe
```

Or, more typically, sign both at the end since the installer is built
from the bundled .exe and re-signing in the right order is what M6.4
will automate:

```cmd
build\nuitka.cmd
build\installer.cmd
build\sign.cmd          REM signs both
```

## Layout

- `nuitka.cmd` — Windows entry for the Nuitka build.
- `build_config.py` — Nuitka command-line assembly + post-build folder rename.
- `installer.cmd` — Windows entry for the installer compile.
- `build_installer.py` — `iscc.exe` discovery + invocation.
- `installer.iss` — declarative Inno Setup configuration.
- `sign.cmd` — Windows entry for Authenticode signing.
- `sign.py` — `signtool.exe` discovery + sign/verify invocation.

Build artifacts (`_nuitka/`, `dist/`, `__pycache__/`) are gitignored;
only the scripts themselves are committed.
