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

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        return None  # Filled in Task 7.


__all__ = ["_PagedRawValuesModel"]
