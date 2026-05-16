"""Virtualized raw-values data table view (C5.2).

A ``QTableView`` + status ``QLabel`` pair that renders one page of raw rows
from the user's current selection at a time. Pages are 200 rows; outside
that window, the model returns a placeholder and schedules a background
fetch through :class:`RawQueryEngine`. ``ts`` is the only sortable column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pyarrow as pa
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import QHeaderView, QLabel, QTableView, QVBoxLayout, QWidget

from local_equs_client.data_layer.query_planner import QueryPlan

if TYPE_CHECKING:
    pass


class _Engine(Protocol):
    def count(self, plan: QueryPlan, *, cancelled: object = None) -> int: ...

    def fetch_page(
        self,
        plan: QueryPlan,
        *,
        offset: int,
        limit: int,
        order: str = "asc",
        cancelled: object = None,
    ) -> pa.Table: ...

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


def _displayed_columns_for_plan(plan: QueryPlan) -> tuple[str, ...]:
    """Return sorted alphabetical union of raw_columns across all tool queries."""
    seen: set[str] = set()
    for q in plan.per_tool_queries:
        seen.update(q.raw_columns)
    return tuple(sorted(seen))


class DataTableView(QWidget):
    """Raw-rows table tab — engine-driven (count + first page on set_plan)."""

    def __init__(
        self,
        *,
        engine: _Engine | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._plan: QueryPlan | None = None
        self._order: str = "asc"

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
        self._table.verticalScrollBar().valueChanged.connect(
            lambda _: self.ensure_row_loaded(self._table.rowAt(0) or 0)
        )

    def set_plan(self, plan: QueryPlan) -> None:
        self._plan = plan
        if not plan.per_tool_queries:
            self.show_normal(_EMPTY_SELECTION_TEXT)
            self._model.set_columns(())
            self._model.set_total_count(0)
            return
        if all(not q.raw_columns for q in plan.per_tool_queries):
            self.show_normal(_NO_MAPPING_TEXT)
            self._model.set_columns(())
            self._model.set_total_count(0)
            return
        self._refresh()

    def _refresh(self) -> None:
        plan = self._plan
        engine = self._engine
        if plan is None or engine is None:
            return
        total = engine.count(plan)
        displayed = _displayed_columns_for_plan(plan)
        self._model.set_columns(("tool_id", "ts", *displayed))
        self._model.set_total_count(total)
        page = engine.fetch_page(plan, offset=0, limit=_PAGE_SIZE, order=self._order)
        self._model.set_page(offset=0, page=page)
        first = 1 if total > 0 else 0
        last = min(_PAGE_SIZE, total)
        status = f"Showing {first}–{last} of {total:,} rows"
        if plan.partial_data_warnings:
            status += f" (partial data: {'; '.join(plan.partial_data_warnings)})"
        self.show_normal(status)

    def ensure_row_loaded(self, row: int) -> None:
        plan = self._plan
        engine = self._engine
        if plan is None or engine is None or self._model.rowCount() == 0:
            return
        target_offset = (max(0, row) // _PAGE_SIZE) * _PAGE_SIZE
        if target_offset == self._model._page_offset:  # noqa: SLF001
            return
        page = engine.fetch_page(
            plan, offset=target_offset, limit=_PAGE_SIZE, order=self._order
        )
        self._model.set_page(offset=target_offset, page=page)
        total = self._model.rowCount()
        first = target_offset + 1
        last = min(target_offset + _PAGE_SIZE, total)
        status = f"Showing {first}–{last} of {total:,} rows"
        if plan.partial_data_warnings:
            status += f" (partial data: {'; '.join(plan.partial_data_warnings)})"
        self.show_normal(status)

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
