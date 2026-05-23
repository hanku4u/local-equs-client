"""Unit tests for the Nuitka build driver (C6.1).

We don't actually invoke Nuitka — the build takes minutes and needs a C
compiler. Instead, smoke-test the pure helpers: version resolution and
command-line assembly. These would catch regressions if the
``pyproject.toml`` schema or the Nuitka flag set ever shift.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUILD_CONFIG_PATH = _REPO_ROOT / "build" / "build_config.py"


def _load_build_config():
    """Load build/build_config.py as a module (it lives outside src/)."""
    spec = importlib.util.spec_from_file_location("build_config", _BUILD_CONFIG_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_config", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_config():
    return _load_build_config()


def test_app_version_reads_pyproject(build_config) -> None:
    version = build_config.app_version()
    assert isinstance(version, str)
    # Must be a real semver-ish string, not an empty placeholder.
    assert version.count(".") >= 1
    assert version[0].isdigit()


def test_nuitka_args_contains_required_flags(build_config) -> None:
    args = build_config.nuitka_args("1.2.3")
    joined = " ".join(args)
    assert "-m nuitka" in joined or "nuitka" in args
    assert "--standalone" in args
    assert "--enable-plugin=pyside6" in args
    assert "--windows-console-mode=disable" in args
    assert "--output-filename=LocalEQUS.exe" in args


def test_nuitka_args_bundles_native_dep_data(build_config) -> None:
    args = build_config.nuitka_args("1.2.3")
    assert "--include-package-data=duckdb" in args
    assert "--include-package-data=pyarrow" in args


def test_nuitka_args_excludes_test_code(build_config) -> None:
    args = build_config.nuitka_args("1.2.3")
    assert "--nofollow-import-to=tests" in args
    assert "--nofollow-import-to=pytest" in args


def test_nuitka_args_includes_pyqtgraph_dynamic_pyside6_modules(build_config) -> None:
    # pyqtgraph imports these via importlib.import_module(), which Nuitka's
    # static analysis can't follow. Without explicit --include-module the
    # packaged app dies at import with ModuleNotFoundError before any window
    # appears. Regression guard for the C6.1 silent-crash fix.
    args = build_config.nuitka_args("1.2.3")
    assert "--include-module=PySide6.QtOpenGL" in args
    assert "--include-module=PySide6.QtOpenGLWidgets" in args


def test_nuitka_args_bundles_own_package_data(build_config) -> None:
    # The SQL migration files under state/migrations/ are loaded at runtime;
    # Nuitka bundles code but not data by default, so without this the
    # packaged app raises FileNotFoundError in db.migrate() on first launch.
    args = build_config.nuitka_args("1.2.3")
    assert "--include-package-data=local_equs_client" in args


def test_nuitka_args_stamps_version_metadata(build_config) -> None:
    args = build_config.nuitka_args("1.2.3")
    assert "--file-version=1.2.3" in args
    assert "--product-version=1.2.3" in args
    assert any(a.startswith("--product-name=") for a in args)


def test_nuitka_args_points_at_main_module_entry(build_config) -> None:
    args = build_config.nuitka_args("1.2.3")
    entry = args[-1]
    assert entry.endswith("main.py")
    assert Path(entry).is_file(), f"Expected {entry} to exist on disk"
