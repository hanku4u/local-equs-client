"""Settings UI: data dir (M0), server URL (M2), full settings (M5).

C0.7 shipped the data-dir-only skeleton. C2.1 (this revision) adds server URL.
C5.10 fills out the remaining settings (telemetry opt-out, update check
frequency).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

_UPDATE_FREQ_OPTIONS: list[tuple[str, int]] = [
    ("Never", 0),
    ("Daily", 24),
    ("Weekly", 168),
]


def _update_freq_index(hours: int) -> int:
    """Return the dropdown index whose hours value matches ``hours``, default Daily."""
    for i, (_, value) in enumerate(_UPDATE_FREQ_OPTIONS):
        if value == hours:
            return i
    return 1  # Daily fallback when stored value isn't one of the canonical options


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

        # Telemetry row — positive framing, checked = telemetry enabled.
        self._telemetry_check = QCheckBox("Send anonymous telemetry")
        self._telemetry_check.setChecked(not current.telemetry_opt_out)
        form.addRow("Telemetry:", self._telemetry_check)

        # Update check frequency row.
        self._update_freq_combo = QComboBox()
        for label, hours in _UPDATE_FREQ_OPTIONS:
            self._update_freq_combo.addItem(label, hours)
        self._update_freq_combo.setCurrentIndex(
            _update_freq_index(current.update_check_frequency_hours)
        )
        form.addRow("Check for updates:", self._update_freq_combo)

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
        opt_out = not self._telemetry_check.isChecked()
        freq_data = self._update_freq_combo.currentData()
        new_freq = int(freq_data) if freq_data is not None else 24
        settings.save(
            replace(
                settings.get_settings(),
                data_dir=new_dir,
                server_url=new_server,
                telemetry_opt_out=opt_out,
                update_check_frequency_hours=new_freq,
            )
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
