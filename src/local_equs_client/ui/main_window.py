"""Top-level QMainWindow with picker / chart grid / time range layout (C1.1)."""

from PySide6.QtWidgets import QMainWindow


class MainWindow(QMainWindow):
    """Empty skeleton main window — to be fleshed out in C1.1."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Local EQUS")
        self.resize(1200, 800)
