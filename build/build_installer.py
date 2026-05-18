"""Drive the Inno Setup compiler to produce LocalEQUS-Setup-{version}.exe (C6.2).

Inno Setup is configured declaratively in ``installer.iss``. This driver:

1. Reads the project version from ``pyproject.toml`` (single source of truth).
2. Verifies ``dist/LocalEQUS/LocalEQUS.exe`` exists — the installer would
   silently produce a broken setup otherwise.
3. Locates ``iscc.exe`` (Inno Setup's command-line compiler).
4. Invokes ``iscc /DAppVersion=<version> build/installer.iss``.

Run from the repo root via ``build\\installer.cmd``.

Inno Setup 6 download: https://jrsoftware.org/isinfo.php
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from build_config import app_version  # type: ignore[import-not-found]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ISS_FILE = _REPO_ROOT / "build" / "installer.iss"
_NUITKA_OUTPUT = _REPO_ROOT / "dist" / "LocalEQUS" / "LocalEQUS.exe"

_DEFAULT_ISCC_PATHS = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)


def find_iscc() -> Path:
    """Resolve ``iscc.exe`` from $ISCC, PATH, or default install locations.

    Raises ``FileNotFoundError`` if no usable iscc.exe is found.
    """
    override = os.environ.get("ISCC")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate

    on_path = shutil.which("iscc") or shutil.which("ISCC")
    if on_path:
        return Path(on_path)

    for candidate in _DEFAULT_ISCC_PATHS:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "Could not find iscc.exe. Install Inno Setup 6 "
        "(https://jrsoftware.org/isinfo.php), or set $ISCC to its full path."
    )


def verify_nuitka_output() -> None:
    """Bail early if the Nuitka standalone hasn't been built yet."""
    if not _NUITKA_OUTPUT.is_file():
        raise FileNotFoundError(
            f"Expected {_NUITKA_OUTPUT} to exist before building the installer. "
            "Run build\\nuitka.cmd first."
        )


def iscc_args(iscc_exe: Path, version: str) -> list[str]:
    return [
        str(iscc_exe),
        f"/DAppVersion={version}",
        str(_ISS_FILE),
    ]


def run_build() -> int:
    """Compile the installer; return iscc's exit code."""
    verify_nuitka_output()
    iscc = find_iscc()
    version = app_version()
    cmd = iscc_args(iscc, version)
    print("Running Inno Setup for version", version, flush=True)
    print(" ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=_REPO_ROOT)


def main() -> int:
    try:
        return run_build()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
