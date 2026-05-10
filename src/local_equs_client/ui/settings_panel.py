"""Settings UI: data dir (M0), server URL (M2), full settings (M5).

C0.7 shipped the data-dir-only skeleton. C2.1 (this revision) adds server URL.
C5.10 fills out the remaining settings (telemetry opt-out, update check
frequency).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.config import settings


class SettingsPanel(QDialog):
    """Modal dialog letting the user inspect and edit the data directory + server URL."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")

        current = settings.get_settings()

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Data directory row
        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(str(current.data_dir))
        path_row.addWidget(self._path_edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        path_row.addWidget(browse)
        form.addRow("Data directory:", path_row)

        # Server URL row
        self._server_edit = QLineEdit(current.server_url or "")
        self._server_edit.setPlaceholderText("https://equs.example.com")
        form.addRow("Server URL:", self._server_edit)

        layout.addLayout(form)

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
        new_server = self._server_edit.text().strip() or None
        settings.save(
            replace(settings.get_settings(), data_dir=new_dir, server_url=new_server)
        )
        self.accept()


class FirstRunWizard(QDialog):
    """One-shot prompt for the server URL when ``settings.server_url`` is missing.

    Called from :func:`local_equs_client.main.main` on launch. Cancelling leaves
    server-dependent features disabled; the user can fill it in later via Settings.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Local EQUS")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Enter the EQUS server URL to enable manifest-driven downloads, "
            "the canonical sensor catalog, and telemetry.\n\n"
            "You can change this later in File → Settings."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._server_edit = QLineEdit()
        self._server_edit.setPlaceholderText("https://equs.example.com")
        layout.addWidget(self._server_edit)

        button_row = QHBoxLayout()
        button_row.addStretch()
        skip_btn = QPushButton("Skip for now")
        skip_btn.clicked.connect(self.reject)
        button_row.addWidget(skip_btn)
        ok_btn = QPushButton("Save")
        ok_btn.clicked.connect(self._on_ok)
        button_row.addWidget(ok_btn)
        layout.addLayout(button_row)

    def _on_ok(self) -> None:
        url = self._server_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing server URL", "Enter a URL or click Skip.")
            return
        settings.save(replace(settings.get_settings(), server_url=url))
        self.accept()
