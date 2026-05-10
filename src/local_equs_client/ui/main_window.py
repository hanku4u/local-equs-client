"""Top-level QMainWindow with picker / chart grid / time range layout (C1.1)."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow

from local_equs_client.ui.settings_panel import SettingsPanel


class MainWindow(QMainWindow):
    """Empty skeleton main window — to be fleshed out in C1.1."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Local EQUS")
        self.resize(1200, 800)
        self._build_menu()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        settings_action = QAction("&Settings…", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _open_settings(self) -> None:
        SettingsPanel(self).exec()
