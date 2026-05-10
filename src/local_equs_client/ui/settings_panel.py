"""Settings UI: data dir (M0), server URL (M2), full settings (M5).

C0.7 ships the data-dir-only skeleton. C2.1 adds server URL; C5.10 fills out the
remaining settings (telemetry opt-out, update check frequency).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.config import settings


class SettingsPanel(QDialog):
    """Modal dialog letting the user inspect and edit the data directory."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")

        current = settings.get_settings()
        self._initial_data_dir = current.data_dir

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Data directory:"))

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(str(current.data_dir))
        path_row.addWidget(self._path_edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        path_row.addWidget(browse)
        layout.addLayout(path_row)

        hint = QLabel("Changes to the data directory take effect after restarting the app.")
        hint.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(hint)

        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save)
        button_row.addWidget(save_btn)
        layout.addLayout(button_row)

    def _on_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Select data directory", self._path_edit.text()
        )
        if chosen:
            self._path_edit.setText(chosen)

    def _on_save(self) -> None:
        new_dir = Path(self._path_edit.text()).expanduser()
        settings.save(replace(settings.get_settings(), data_dir=new_dir))
        self.accept()
