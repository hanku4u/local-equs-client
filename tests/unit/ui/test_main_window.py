"""Unit tests for ``local_equs_client.ui.main_window`` (C1.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from local_equs_client.data_layer.local_library import LocalLibrary  # noqa: E402
from local_equs_client.data_layer.metadata_cache import MetadataCache  # noqa: E402
from local_equs_client.selection.selection_model import SelectionModel  # noqa: E402
from local_equs_client.state import db  # noqa: E402
from local_equs_client.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def env(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(tmp_path / "state.db")
    db.migrate(conn)
    library = LocalLibrary(data_dir, conn)
    cache = MetadataCache(library)
    yield SelectionModel(), library, cache
    conn.close()


def test_constructs_with_dependencies(qapp, env) -> None:
    model, library, cache = env
    window = MainWindow(model, library, cache)
    assert window.windowTitle() == "Local EQUS"


def test_menu_bar_has_file_view_help(qapp, env) -> None:
    model, library, cache = env
    window = MainWindow(model, library, cache)
    titles = [m.title() for m in window.menuBar().findChildren(type(window.menuBar().addMenu("")))]
    titles = [t for t in titles if t]  # filter the throwaway "" menu
    # &File renders as 'File' on some platforms; just check the leading character set.
    assert any("File" in t for t in titles)
    assert any("View" in t for t in titles)
    assert any("Help" in t for t in titles)


def test_close_event_persists_geometry(qapp, env, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    model, library, cache = env
    window = MainWindow(model, library, cache)
    window.resize(900, 700)

    # closeEvent is called by Qt on close(); we can call it via the underlying API.
    from PySide6.QtGui import QCloseEvent

    window.closeEvent(QCloseEvent())

    geometry = window._qsettings.value("mainwindow/geometry")  # noqa: SLF001
    assert geometry is not None
