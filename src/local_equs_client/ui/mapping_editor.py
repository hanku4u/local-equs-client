"""Canonical x tool mapping matrix editor (C3.8, C3.9, C5.3-5).

C3.8 ships the read-only matrix view. C3.9 (this revision) adds the right-side
collapsible detail panel and persists the splitter state via ``QSettings``.

Layout:

::

    +------------------------------------------------------------+
    | Process group: [etcher           ▼]                        |
    +------------------------------------------------------------+
    | [Mappings]   [Categories — disabled]                       |
    +-------------------------------+----------------------------+
    |                               | Details                    |
    |   canonical | etch_a1 | …     |   chamber_pressure         |
    |   chamber_p | PCham   | …     |   Description: …           |
    |   rf_power  |   —     | …     |   Units: torr              |
    |   ...                         |   Mappings:                |
    |   (empty cells styled red)    |     etch_a1 → PCham_torr   |
    |                               |   Audit: Coming in M5      |
    +-------------------------------+----------------------------+

Editing comes in C5.3 — every cell is read-only here. The Categories tab is a
disabled placeholder until C5.6 lands the admin UI. ``Settings.permissions_simulate_admin``
gates editing affordances when M5 wires them in; for now it's just a settings
field.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from local_equs_client.data_layer.metadata_cache import CanonicalSensor, MetadataCache

logger = logging.getLogger(__name__)

_EMPTY_CELL_BG = QColor(255, 220, 220)
_QSETTINGS_SPLITTER = "mapping_editor/splitter"


class MappingEditor(QDialog):
    """Canonical x tool mapping matrix (read-only in M3)."""

    def __init__(
        self,
        metadata_cache: MetadataCache,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache = metadata_cache
        self._qsettings = QSettings("LocalEQUS", "Client")
        self.setWindowTitle("Mapping Editor")
        self.resize(1100, 600)

        self._build_ui()
        self._wire()
        self._populate_prc_groups()
        self._restore_splitter_state()
        if self._prc_group_combo.count() > 0:
            self._populate_matrix(self._prc_group_combo.currentText())

    # --- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Process group:"))
        self._prc_group_combo = QComboBox()
        toolbar.addWidget(self._prc_group_combo)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, stretch=1)

        # --- Mappings tab ------------------------------------------------
        mappings_tab = QWidget()
        mappings_layout = QVBoxLayout(mappings_tab)
        mappings_layout.setContentsMargins(0, 0, 0, 0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        mappings_layout.addWidget(self._splitter)

        self._table = QTableWidget(0, 0)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._splitter.addWidget(self._table)

        # Detail panel
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_box = QLabel("Details")
        font = detail_box.font()
        font.setBold(True)
        detail_box.setFont(font)
        detail_layout.addWidget(detail_box)

        detail_form = QFormLayout()
        self._detail_name = QLabel("(select a row)")
        self._detail_description = QLabel("")
        self._detail_description.setWordWrap(True)
        self._detail_units = QLabel("")
        detail_form.addRow("Name:", self._detail_name)
        detail_form.addRow("Description:", self._detail_description)
        detail_form.addRow("Units:", self._detail_units)
        detail_layout.addLayout(detail_form)

        detail_layout.addWidget(QLabel("Mappings:"))
        self._detail_mappings = QLabel("(none)")
        self._detail_mappings.setStyleSheet("color: gray;")
        self._detail_mappings.setWordWrap(True)
        detail_layout.addWidget(self._detail_mappings)

        self._audit_label = QLabel("Audit history: Coming in M5")
        self._audit_label.setStyleSheet("color: gray; font-style: italic;")
        detail_layout.addWidget(self._audit_label)
        detail_layout.addStretch()

        self._splitter.addWidget(detail_widget)
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 1)

        self._tabs.addTab(mappings_tab, "Mappings")

        # --- Categories tab (disabled) -----------------------------------
        categories_tab = QWidget()
        cat_layout = QVBoxLayout(categories_tab)
        cat_msg = QLabel(
            "Category management arrives in C5.6 — admin-only edit UI."
        )
        cat_msg.setStyleSheet("color: gray; font-style: italic;")
        cat_layout.addWidget(cat_msg)
        cat_layout.addStretch()
        self._tabs.addTab(categories_tab, "Categories")
        self._tabs.setTabEnabled(1, False)

        # Footer
        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    def _wire(self) -> None:
        self._prc_group_combo.currentTextChanged.connect(self._populate_matrix)
        self._table.itemSelectionChanged.connect(self._on_row_changed)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)

    # --- Data --------------------------------------------------------------

    def _populate_prc_groups(self) -> None:
        prc_groups = self._cache.prc_groups()
        self._prc_group_combo.clear()
        if not prc_groups:
            self._prc_group_combo.addItem("(no metadata)")
            self._prc_group_combo.setEnabled(False)
        else:
            self._prc_group_combo.setEnabled(True)
            for pg in prc_groups:
                self._prc_group_combo.addItem(pg)

    def _populate_matrix(self, prc_group: str) -> None:
        if not prc_group or prc_group == "(no metadata)":
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            return

        sensors = self._cache.canonical_sensors(prc_group)
        tools = self._tools_for_prc_group(prc_group)

        self._table.setRowCount(len(sensors))
        self._table.setColumnCount(len(tools))
        self._table.setHorizontalHeaderLabels(tools)
        self._table.setVerticalHeaderLabels([s.name for s in sensors])
        self._table.verticalHeader().setVisible(True)

        for r, sensor in enumerate(sensors):
            for c, tool in enumerate(tools):
                raw = self._cache.mapping(tool, sensor.name)
                if raw:
                    item = QTableWidgetItem(raw)
                else:
                    item = QTableWidgetItem("—")
                    item.setBackground(QBrush(_EMPTY_CELL_BG))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, sensor.name)
                self._table.setItem(r, c, item)

        if sensors:
            self._table.selectRow(0)

    def _tools_for_prc_group(self, prc_group: str) -> list[str]:
        """Tools that appear in this prc_group's cached mappings."""
        out: list[str] = []
        for tool_id, pg in sorted(self._cache._tool_to_pg().items()):  # noqa: SLF001
            if pg == prc_group:
                out.append(tool_id)
        return out

    # --- Detail pane -----------------------------------------------------

    def _on_row_changed(self) -> None:
        row = self._table.currentRow()
        if row < 0 or self._table.rowCount() == 0:
            return
        prc_group = self._prc_group_combo.currentText()
        sensors = self._cache.canonical_sensors(prc_group)
        if row >= len(sensors):
            return
        sensor = sensors[row]
        self._show_detail(sensor)

    def _show_detail(self, sensor: CanonicalSensor) -> None:
        self._detail_name.setText(sensor.name)
        self._detail_description.setText(sensor.description or "(no description)")
        self._detail_units.setText(sensor.units or "—")

        mappings_lines: list[str] = []
        for tool_id in self._tools_for_prc_group(self._prc_group_combo.currentText()):
            raw = self._cache.mapping(tool_id, sensor.name)
            if raw:
                mappings_lines.append(f"  {tool_id} → {raw}")
            else:
                mappings_lines.append(f"  {tool_id} → (no mapping)")
        if mappings_lines:
            self._detail_mappings.setText("\n".join(mappings_lines))
            self._detail_mappings.setStyleSheet("")
        else:
            self._detail_mappings.setText("(no tools in this prc_group)")
            self._detail_mappings.setStyleSheet("color: gray;")

    # --- Splitter persistence -------------------------------------------

    def _restore_splitter_state(self) -> None:
        state = self._qsettings.value(_QSETTINGS_SPLITTER)
        if state is not None:
            self._splitter.restoreState(state)

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        self._qsettings.setValue(_QSETTINGS_SPLITTER, self._splitter.saveState())


__all__ = ["MappingEditor"]
