"""Unit tests for ``local_equs_client.ui.main_window`` (C1.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from local_equs_client.data_layer.local_library import LocalLibrary  # noqa: E402
from local_equs_client.data_layer.metadata_cache import MetadataCache  # noqa: E402
from local_equs_client.data_layer.query_controller import QueryController  # noqa: E402
from local_equs_client.data_layer.query_engine import QueryEngine  # noqa: E402
from local_equs_client.data_layer.query_planner import QueryPlanner  # noqa: E402
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
    model = SelectionModel()
    controller = QueryController(model, QueryPlanner(library), QueryEngine())
    yield model, library, cache, controller
    conn.close()


def test_constructs_with_dependencies(qapp, env) -> None:
    model, library, cache, controller = env
    window = MainWindow(model, library, cache, controller)
    assert window.windowTitle() == "Local EQUS"


def test_menu_bar_has_file_view_help(qapp, env) -> None:
    model, library, cache, controller = env
    window = MainWindow(model, library, cache, controller)
    titles = [action.text() for action in window.menuBar().actions()]
    assert any("File" in t for t in titles)
    assert any("View" in t for t in titles)
    assert any("Help" in t for t in titles)


def test_close_event_persists_geometry(qapp, env, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    model, library, cache, controller = env
    window = MainWindow(model, library, cache, controller)
    window.resize(900, 700)

    from PySide6.QtGui import QCloseEvent

    window.closeEvent(QCloseEvent())

    geometry = window._qsettings.value("mainwindow/geometry")  # noqa: SLF001
    assert geometry is not None


def test_right_side_of_splitter_is_a_tab_widget_with_chart_and_table(qapp, env) -> None:
    from PySide6.QtWidgets import QTabWidget
    model, library, cache, controller = env
    window = MainWindow(model, library, cache, controller)
    right = window._splitter.widget(1)  # noqa: SLF001
    assert isinstance(right, QTabWidget)
    labels = [right.tabText(i) for i in range(right.count())]
    assert labels == ["Chart", "Table"]


def test_switching_to_table_tab_activates_data_table(qapp, env) -> None:
    from PySide6.QtWidgets import QTabWidget
    model, library, cache, controller = env
    window = MainWindow(model, library, cache, controller)
    tabs: QTabWidget = window._splitter.widget(1)  # noqa: SLF001
    tabs.setCurrentIndex(1)  # Table
    table_view = tabs.widget(1)
    assert table_view._active is True  # noqa: SLF001
    tabs.setCurrentIndex(0)  # back to Chart
    assert table_view._active is False  # noqa: SLF001
