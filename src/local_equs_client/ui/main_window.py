"""Top-level QMainWindow with picker / chart grid / time range layout (C1.1).

Layout per Project_Plan §"UI Layer":

- TimeRangeSelector across the top.
- Horizontal QSplitter below: SensorPicker on the left, ChartGrid on the right.

Window geometry, splitter sizes, and dock state persist via ``QSettings``.
"""

from __future__ import annotations

import logging
import sqlite3

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.download_manager import DownloadManager
from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import MetadataCache
from local_equs_client.data_layer.query_controller import QueryController
from local_equs_client.data_layer.query_planner import QueryPlanner
from local_equs_client.data_layer.raw_query_engine import RawQueryEngine
from local_equs_client.data_layer.update_manager import UpdateManager
from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.selection.view_controller import ViewController
from local_equs_client.ui.chart_grid import ChartGrid
from local_equs_client.ui.data_table import DataTableView
from local_equs_client.ui.local_library_panel import LocalLibraryPanel
from local_equs_client.ui.mapping_editor import MappingEditor
from local_equs_client.ui.sensor_picker import SensorPicker
from local_equs_client.ui.settings_panel import SettingsPanel
from local_equs_client.ui.time_range_selector import TimeRangeSelector
from local_equs_client.ui.updates_panel import UpdatesPanel
from local_equs_client.ui.view_mode_bar import ViewModeBar

logger = logging.getLogger(__name__)

_DEFAULT_WIDTH = 1400
_DEFAULT_HEIGHT = 900
_DEFAULT_SPLIT = (320, 1080)
_TABLE_VIEWPORT_WIDTH_PX = 1080  # planner argument; table doesn't use target_resolution


class MainWindow(QMainWindow):
    """Picker / chart grid / time range layout (C1.1)."""

    def __init__(
        self,
        selection_model: SelectionModel,
        library: LocalLibrary,
        metadata_cache: MetadataCache,
        query_controller: QueryController,
        update_manager: UpdateManager | None = None,
        download_manager: DownloadManager | None = None,
        view_controller: ViewController | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        super().__init__()
        self._model = selection_model
        self._library = library
        self._cache = metadata_cache
        self._controller = query_controller
        self._update_manager = update_manager
        self._download_manager = download_manager
        self._view_controller = view_controller
        self._conn = conn
        self._qsettings = QSettings("LocalEQUS", "Client")

        self.setWindowTitle("Local EQUS")
        self.resize(_DEFAULT_WIDTH, _DEFAULT_HEIGHT)

        self._build_menu()
        self._build_layout()
        self._wire_query_pipeline()
        self._restore_state()

    # --- Layout -----------------------------------------------------------

    def _build_layout(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        self._time_range = TimeRangeSelector(self._model, self._library)
        layout.addWidget(self._time_range)

        # C4.9: view-mode + group-by toolbar above the splitter, when a
        # ViewController is wired through.
        self._view_mode_bar: ViewModeBar | None = None
        if self._view_controller is not None:
            self._view_mode_bar = ViewModeBar(self._view_controller)
            layout.addWidget(self._view_mode_bar)

        self._splitter = QSplitter()
        self._picker = SensorPicker(self._model, self._library, self._cache, conn=self._conn)
        self._splitter.addWidget(self._picker)

        self._chart_grid = ChartGrid()
        self._data_table = DataTableView(engine=RawQueryEngine())
        self._right_tabs = QTabWidget()
        self._right_tabs.addTab(self._chart_grid, "Chart")
        self._right_tabs.addTab(self._data_table, "Table")
        self._right_tabs.currentChanged.connect(self._on_right_tab_changed)
        self._splitter.addWidget(self._right_tabs)

        self._splitter.setSizes(list(_DEFAULT_SPLIT))
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        layout.addWidget(self._splitter, stretch=1)

        self.setCentralWidget(central)

    # --- Query pipeline wiring -------------------------------------------

    def _wire_query_pipeline(self) -> None:
        # C4.4: layout placeholders the moment the plan is ready, fill per tool
        # as results arrive, then finalize on the full queryCompleted.
        self._controller.queryPlanned.connect(self._chart_grid.on_plan_ready)
        self._controller.toolCompleted.connect(self._chart_grid.on_tool_complete)
        self._controller.queryCompleted.connect(self._chart_grid.update_from_results)
        self._controller.queryFailed.connect(self._on_query_failed)
        # Pan/zoom on the charts updates the model's time_range; the controller
        # then debounces and re-queries at the new resolution.
        self._chart_grid.rangeChangedByUser.connect(self._model.set_time_range)
        # C4.5: tool order visible in the grid feeds the engine's submit order.
        self._chart_grid.visibleToolsChanged.connect(self._controller.set_tool_priority)
        # C4.9: view-mode flips drive the chart grid's stacked layout.
        if self._view_controller is not None:
            self._view_controller.modeChanged.connect(self._on_mode_changed)
            self._chart_grid.set_mode(self._view_controller.mode)
        # C4.7: sparkline click in overview promotes that pair to focus mode.
        self._chart_grid.promoteRequested.connect(self._on_promote_requested)
        # C4.10: guardrail banner button switches view mode without touching selection.
        self._chart_grid.switchToOverviewRequested.connect(self._on_switch_to_overview)
        # C5.2: feed the raw-data table on selection changes, prime its initial state.
        self._model.selectionChanged.connect(self._on_selection_changed_for_table)
        self._data_table.set_active(self._right_tabs.currentIndex() == 1)
        self._on_selection_changed_for_table()

    def _on_right_tab_changed(self, index: int) -> None:
        self._data_table.set_active(index == 1)

    def _on_selection_changed_for_table(self) -> None:
        planner = QueryPlanner(self._library, self._cache)
        plan = planner.plan(self._model.snapshot(), "standard", _TABLE_VIEWPORT_WIDTH_PX)
        self._data_table.set_plan(plan)

    def _on_mode_changed(self, mode: str) -> None:
        from typing import cast as _cast

        from local_equs_client.selection.types import ViewMode

        self._chart_grid.set_mode(_cast(ViewMode, mode))

    def _on_promote_requested(self, tool_id: str, sensor: str) -> None:
        # Narrow the selection to just the clicked pair, then flip to focus.
        self._model.set_tools((tool_id,))
        self._model.set_sensors_canonical((sensor,))
        if self._view_controller is not None:
            self._view_controller.set_mode("focus")

    def _on_switch_to_overview(self) -> None:
        if self._view_controller is not None:
            self._view_controller.set_mode("overview")

    def _on_query_failed(self, exc: object) -> None:
        logger.warning("Query failed: %s", exc)
        self.statusBar().showMessage(f"Query failed: {exc}", 5000)

    # --- Menu -------------------------------------------------------------

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        settings_action = QAction("&Settings…", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = bar.addMenu("&View")
        rescan_action = QAction("&Rescan local data", self)
        rescan_action.triggered.connect(self._rescan)
        view_menu.addAction(rescan_action)
        library_action = QAction("&Local Library…", self)
        library_action.triggered.connect(self._open_local_library)
        view_menu.addAction(library_action)
        updates_action = QAction("&Updates…", self)
        updates_action.triggered.connect(self._open_updates)
        updates_action.setEnabled(
            self._update_manager is not None and self._download_manager is not None
        )
        view_menu.addAction(updates_action)
        mapping_action = QAction("&Mapping Editor…", self)
        mapping_action.triggered.connect(self._open_mapping_editor)
        view_menu.addAction(mapping_action)

        help_menu = bar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # --- Actions ----------------------------------------------------------

    def _open_settings(self) -> None:
        SettingsPanel(self).exec()

    def _open_local_library(self) -> None:
        LocalLibraryPanel(self._library, self).exec()

    def _open_mapping_editor(self) -> None:
        MappingEditor(self._cache, self).exec()

    def _open_updates(self) -> None:
        if self._update_manager is None or self._download_manager is None:
            QMessageBox.information(
                self,
                "Updates unavailable",
                "Configure a server URL in Settings and restart to enable updates.",
            )
            return
        UpdatesPanel(self._update_manager, self._download_manager, self).exec()
        # After downloads, indices may have changed.
        self._cache.invalidate()
        self._picker.refresh()
        self._time_range.refresh_extent()
        self._controller.trigger()

    def _rescan(self) -> None:
        count = self._library.scan()
        self._cache.invalidate()

        # M3: refresh canonical metadata first so the picker tree has something to render.
        self._cache.refresh_categories()
        for prc_group in self._cache.prc_groups():
            self._cache.refresh_canonical_sensors(prc_group)
            self._cache.refresh_mappings(prc_group)

        # Per-tool raw sensor catalog (C2.9). Falls back to cache / parquet schema offline.
        for tool_id in sorted({f.tool_id for f in self._library.all_files() if not f.archived}):
            self._cache.refresh_sensors(tool_id)

        self._picker.refresh()
        self._time_range.refresh_extent()
        self._controller.trigger()
        self.statusBar().showMessage(f"Rescan complete — {count} parquet files indexed.", 5000)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Local EQUS",
            "Local EQUS desktop client.\nSensor data exploration for fab tools.",
        )

    # --- Persistence ------------------------------------------------------

    def _restore_state(self) -> None:
        geometry = self._qsettings.value("mainwindow/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        window_state = self._qsettings.value("mainwindow/state")
        if window_state is not None:
            self.restoreState(window_state)

        splitter_state = self._qsettings.value("mainwindow/splitter")
        if splitter_state is not None:
            self._splitter.restoreState(splitter_state)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._qsettings.setValue("mainwindow/geometry", self.saveGeometry())
        self._qsettings.setValue("mainwindow/state", self.saveState())
        self._qsettings.setValue("mainwindow/splitter", self._splitter.saveState())
        super().closeEvent(event)
