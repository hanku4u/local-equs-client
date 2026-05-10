"""Application entrypoint: builds the QApplication and the main window."""

import sys

from PySide6.QtWidgets import QApplication

from local_equs_client.ui.main_window import MainWindow


def main() -> None:
    """Create the QApplication, show the main window, and enter the event loop."""
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
