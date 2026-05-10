"""Top-level QMainWindow with picker / chart grid / time range layout (C1.1).

Layout per Project_Plan §"UI Layer":

- TimeRangeSelector across the top.
- Horizontal QSplitter below: SensorPicker on the left, ChartGrid on the right.

Window geometry, splitter sizes, and dock state persist via ``QSettings``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.download_manager import DownloadManager
from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import MetadataCache
from local_equs_client.data_layer.query_controller import QueryController
from local_equs_client.data_layer.update_manager import UpdateManager
from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.ui.chart_grid import ChartGrid
from local_equs_client.ui.local_library_panel import LocalLibraryPanel
from local_equs_client.ui.sensor_picker import SensorPicker
from local_equs_client.ui.settings_panel import SettingsPanel
from local_equs_client.ui.time_range_selector import TimeRangeSelector
from local_equs_client.ui.updates_panel import UpdatesPanel

logger = logging.getLogger(__name__)

_DEFAULT_WIDTH = 1400
_DEFAULT_HEIGHT = 900
_DEFAULT_SPLIT = (320, 1080)


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
    ) -> None:
        super().__init__()
        self._model = selection_model
        self._library = library
        self._cache = metadata_cache
        self._controller = query_controller
        self._update_manager = update_manager
        self._download_manager = download_manager
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

        self._splitter = QSplitter()
        self._picker = SensorPicker(self._model, self._library, self._cache)
        self._splitter.addWidget(self._picker)

        self._chart_grid = ChartGrid()
        self._splitter.addWidget(self._chart_grid)

        self._splitter.setSizes(list(_DEFAULT_SPLIT))
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        layout.addWidget(self._splitter, stretch=1)

        self.setCentralWidget(central)

    # --- Query pipeline wiring -------------------------------------------

    def _wire_query_pipeline(self) -> None:
        self._controller.queryCompleted.connect(self._chart_grid.update_from_results)
        self._controller.queryFailed.connect(self._on_query_failed)
        # Pan/zoom on the charts updates the model's time_range; the controller
        # then debounces and re-queries at the new resolution.
        self._chart_grid.rangeChangedByUser.connect(self._model.set_time_range)

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

        help_menu = bar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # --- Actions ----------------------------------------------------------

    def _open_settings(self) -> None:
        SettingsPanel(self).exec()

    def _open_local_library(self) -> None:
        LocalLibraryPanel(self._library, self).exec()

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
