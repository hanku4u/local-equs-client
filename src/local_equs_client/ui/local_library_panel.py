"""On-disk library: pin/unpin, delete, total size (C2.8).

Surfaces what :class:`LocalLibrary` knows: every indexed parquet file as a
sortable row with the data you need to decide what to keep — tool, hour
bucket, time range, row count, on-disk size, pinned flag. Right-click a row
to delete (with a confirmation prompt). The footer reports total disk usage.

Eviction logic / quota is out of scope for M2 — pinning is a no-op flag for
now, queued for the future cache-pressure work.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.local_library import LocalFile, LocalLibrary

_FILE_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_BYTES_ROLE = int(Qt.ItemDataRole.UserRole) + 2

logger = logging.getLogger(__name__)


class LocalLibraryPanel(QDialog):
    """Dialog showing the local file index with pin/unpin and delete actions."""

    HEADERS: tuple[str, ...] = (
        "Tool",
        "Hour",
        "Start (UTC)",
        "End (UTC)",
        "Rows",
        "Size",
        "Pinned",
    )

    def __init__(self, library: LocalLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Local Library")
        self.resize(900, 520)
        self._library = library

        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, len(self.HEADERS), self)
        self._table.setHorizontalHeaderLabels(list(self.HEADERS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemChanged.connect(self._on_item_changed)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        footer_row = QHBoxLayout()
        self._footer = QLabel("Used: 0 B")
        footer_row.addWidget(self._footer)
        footer_row.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        footer_row.addWidget(refresh_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer_row.addWidget(close_btn)
        layout.addLayout(footer_row)

        self.refresh()

    # --- Data refresh ----------------------------------------------------

    def refresh(self) -> None:
        files = self._library.all_files()
        self._table.setSortingEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(0)
            for file in files:
                self._append_row(file)
        finally:
            self._table.blockSignals(False)
            self._table.setSortingEnabled(True)
        self._update_footer()

    def _append_row(self, file: LocalFile) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        cells = [
            _text_item(file.tool_id),
            _text_item(file.hour_bucket or ""),
            _text_item(_format_dt(file.min_ts)),
            _text_item(_format_dt(file.max_ts)),
            _numeric_item(file.row_count, f"{file.row_count:,}"),
            _bytes_item(file.size_bytes),
        ]
        for col, item in enumerate(cells):
            item.setData(_FILE_ID_ROLE, file.file_id)
            self._table.setItem(row, col, item)

        pinned_item = QTableWidgetItem()
        pinned_item.setFlags(
            Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        pinned_item.setCheckState(
            Qt.CheckState.Checked if file.pinned else Qt.CheckState.Unchecked
        )
        pinned_item.setData(_FILE_ID_ROLE, file.file_id)
        self._table.setItem(row, 6, pinned_item)

    def _update_footer(self) -> None:
        total = self._library.total_size_bytes()
        self._footer.setText(f"Used: {_format_bytes(total)}")

    # --- Interaction -----------------------------------------------------

    def _on_context_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        item = self._table.itemAt(pos)
        if item is None:
            return
        file_id = item.data(_FILE_ID_ROLE)
        if not file_id:
            return

        menu = QMenu(self)
        delete_action = menu.addAction("Delete file…")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is delete_action:
            self._delete_file(file_id)

    def _delete_file(self, file_id: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete file?",
            f"Delete '{file_id}' from disk?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self._library.delete(file_id)
        except OSError as exc:
            logger.warning("Failed to delete %s: %s", file_id, exc)
            QMessageBox.warning(self, "Delete failed", f"{file_id}: {exc}")
            return
        self.refresh()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 6:
            return
        file_id = item.data(_FILE_ID_ROLE)
        if not file_id:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._library.pin(file_id)
        else:
            self._library.unpin(file_id)


# --- Helpers ----------------------------------------------------------------


def _text_item(text: str) -> QTableWidgetItem:
    return QTableWidgetItem(text)


def _numeric_item(value: int, display: str) -> QTableWidgetItem:
    item = QTableWidgetItem(display)
    item.setData(Qt.ItemDataRole.EditRole, value)
    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
    return item


def _bytes_item(size: int) -> QTableWidgetItem:
    item = QTableWidgetItem(_format_bytes(size))
    item.setData(Qt.ItemDataRole.EditRole, size)
    item.setData(_BYTES_ROLE, size)
    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
    return item


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    units = ["KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PiB"


def _format_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")
