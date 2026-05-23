"""Reproducible Nuitka build for ``LocalEQUS.exe`` (C6.1).

Run from the repo root either via ``build\\nuitka.cmd`` (Windows) or
directly as ``python build/build_config.py``. Produces a standalone
folder under ``dist/LocalEQUS/`` containing the .exe plus every native
DLL / resource it needs to run on a fresh Windows machine.

Build prerequisites are pinned under the ``[project.optional-dependencies]
build`` extras group in ``pyproject.toml`` — install with
``pip install -e ".[build]"``.

The Nuitka commercial features are NOT required. The standalone +
PySide6 plugin combo is everything we need for a working .exe.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_ENTRY = _REPO_ROOT / "src" / "local_equs_client" / "main.py"
_OUTPUT_DIR = _REPO_ROOT / "dist"
# Nuitka names its standalone folder ``<entry-basename>.dist``. Our entry is
# ``main.py``, so without intervention we'd get ``dist/main.dist/``. The
# installer (C6.2) sources from ``dist/LocalEQUS/`` instead.
_NUITKA_DEFAULT_FOLDER = _OUTPUT_DIR / "main.dist"
_FINAL_FOLDER = _OUTPUT_DIR / "LocalEQUS"


def app_version() -> str:
    """Read the project version from ``pyproject.toml`` so builds match it."""
    with _PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"])


def nuitka_args(version: str) -> list[str]:
    """Assemble the Nuitka command-line for a standalone Windows build."""
    return [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--remove-output",
        "--enable-plugin=pyside6",
        "--windows-console-mode=disable",
        f"--output-dir={_OUTPUT_DIR}",
        "--output-filename=LocalEQUS.exe",
        # pyqtgraph pulls these PySide6 submodules in via
        # importlib.import_module() (pyqtgraph/Qt/OpenGLHelpers.py), which
        # Nuitka's static analysis can't follow — so they must be named
        # explicitly or the packaged app dies at import with
        # ModuleNotFoundError before any window appears.
        "--include-module=PySide6.QtOpenGL",
        "--include-module=PySide6.QtOpenGLWidgets",
        # Native DLLs that ride alongside the Python package data.
        "--include-package-data=duckdb",
        "--include-package-data=pyarrow",
        # Make sure every submodule of our package is bundled, even ones
        # that aren't imported at startup (e.g. settings_panel).
        "--include-package=local_equs_client",
        # Bundle our own non-code data files. Today that's the SQL migration
        # files under state/migrations/, loaded at runtime via
        # Path(__file__).parent.iterdir(). Nuitka bundles code but not data
        # by default, so without this db.migrate() raises FileNotFoundError
        # on first launch. Covers any future package data (icons, styles…) too.
        "--include-package-data=local_equs_client",
        # Test code never reaches runtime; reject any accidental import.
        "--nofollow-import-to=tests",
        "--nofollow-import-to=pytest",
        # Windows file metadata so the .exe shows up correctly in Explorer
        # and downstream code-signing tooling.
        f"--file-version={version}",
        f"--product-version={version}",
        "--company-name=LocalEQUS",
        "--product-name=Local EQUS Client",
        "--file-description=Local EQUS desktop client",
        str(_ENTRY),
    ]


def run_build(*, clean: bool = False) -> int:
    """Invoke Nuitka and return its exit code."""
    if clean and _OUTPUT_DIR.exists():
        print(f"Removing previous output dir {_OUTPUT_DIR}", flush=True)
        shutil.rmtree(_OUTPUT_DIR)

    version = app_version()
    cmd = nuitka_args(version)
    print("Running Nuitka for version", version, flush=True)
    print(" ".join(cmd), flush=True)
    rc = subprocess.call(cmd, cwd=_REPO_ROOT)
    if rc == 0:
        _rename_output_folder()
    return rc


def _rename_output_folder() -> None:
    """Rename Nuitka's ``main.dist/`` to ``LocalEQUS/`` for a tidy install root."""
    if not _NUITKA_DEFAULT_FOLDER.exists():
        return
    if _FINAL_FOLDER.exists():
        shutil.rmtree(_FINAL_FOLDER)
    _NUITKA_DEFAULT_FOLDER.rename(_FINAL_FOLDER)
    print(f"Renamed {_NUITKA_DEFAULT_FOLDER.name} -> {_FINAL_FOLDER.name}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete dist/ before building so the run is reproducible.",
    )
    args = parser.parse_args()
    return run_build(clean=args.clean)


if __name__ == "__main__":
    sys.exit(main())
