"""Virtualized raw-values data table view (C5.2).

A ``QTableView`` + status ``QLabel`` pair that renders one page of raw rows
from the user's current selection at a time. Pages are 200 rows; outside
that window, the model returns a placeholder and schedules a background
fetch through :class:`RawQueryEngine`. ``ts`` is the only sortable column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import QHeaderView, QLabel, QTableView, QVBoxLayout, QWidget

if TYPE_CHECKING:
    pass

_PAGE_SIZE = 200
_PLACEHOLDER = "…"


class _PagedRawValuesModel(QAbstractTableModel):
    """One-page-in-memory model backed by an external paged fetcher."""

    def __init__(self) -> None:
        super().__init__()
        self._columns: tuple[str, ...] = ()
        self._total: int = 0
        self._page_offset: int | None = None
        self._page: pa.Table | None = None

    # --- public mutation API ---------------------------------------------

    def set_columns(self, columns: tuple[str, ...]) -> None:
        self.beginResetModel()
        self._columns = columns
        self._page_offset = None
        self._page = None
        self.endResetModel()

    def set_total_count(self, total: int) -> None:
        self.beginResetModel()
        self._total = max(0, int(total))
        self._page_offset = None
        self._page = None
        self.endResetModel()

    # --- Qt model API ----------------------------------------------------

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: B008
    ) -> int:
        if parent.isValid():
            return 0
        return self._total

    def columnCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: B008
    ) -> int:
        if parent.isValid():
            return 0
        return len(self._columns)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._columns):
            return self._columns[section]
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def set_page(self, *, offset: int, page: pa.Table) -> None:
        """Install a freshly fetched page; emit dataChanged for its row range."""
        self._page_offset = max(0, int(offset))
        self._page = page
        if page.num_rows == 0 or not self._columns:
            return
        last = self._page_offset + page.num_rows - 1
        if last >= self._total:
            last = self._total - 1
        top_left = self.index(self._page_offset, 0)
        bottom_right = self.index(last, len(self._columns) - 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole])

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole or not index.isValid():
            return None
        if self._page is None or self._page_offset is None:
            return _PLACEHOLDER
        row = index.row()
        local_row = row - self._page_offset
        if local_row < 0 or local_row >= self._page.num_rows:
            return _PLACEHOLDER
        col_name = self._columns[index.column()]
        if col_name not in self._page.column_names:
            return _PLACEHOLDER
        value = self._page.column(col_name)[local_row].as_py()
        return "" if value is None else str(value)


_EMPTY_SELECTION_TEXT = (
    "Empty selection — pick a tool and sensor to view raw data."
)
_NO_MAPPING_TEXT = "No mapped sensors for the selected tools."
_STATUS_NORMAL_STYLE = "color: rgb(180, 180, 180); padding: 4px 6px;"
_STATUS_ERROR_STYLE = "color: rgb(220, 100, 100); padding: 4px 6px;"


class DataTableView(QWidget):
    """Raw-rows table tab — empty-state shell (engine wiring in later tasks)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._status = QLabel(_EMPTY_SELECTION_TEXT)
        self._status.setStyleSheet(_STATUS_NORMAL_STYLE)
        layout.addWidget(self._status)

        self._model = _PagedRawValuesModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSortingEnabled(False)
        self._table.verticalHeader().setDefaultSectionSize(20)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        layout.addWidget(self._table, stretch=1)

    # --- public helpers (used by tests + later tasks) --------------------

    def status_text(self) -> str:
        return self._status.text()

    def status_label_style(self) -> str:
        return self._status.styleSheet()

    def show_error(self, message: str) -> None:
        self._status.setText(message)
        self._status.setStyleSheet(_STATUS_ERROR_STYLE)

    def show_normal(self, message: str) -> None:
        self._status.setText(message)
        self._status.setStyleSheet(_STATUS_NORMAL_STYLE)


__all__ = ["DataTableView", "_PagedRawValuesModel"]
