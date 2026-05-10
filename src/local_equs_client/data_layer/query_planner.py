"""Plans per-tool DuckDB queries from a Selection + ViewMode (C0.6, C1.7, C3.2, C4.2).

C0.6 defines the contract; C1.7 provides the M1 implementation; C3.2 expands
canonical → raw resolution; C4.2 picks resolution per ViewMode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from local_equs_client.selection.types import Selection, TimeRange, ViewMode


@dataclass(frozen=True, slots=True)
class ToolQuery:
    """A single DuckDB query against one tool's parquet files."""

    tool_id: str
    file_paths: tuple[Path, ...]
    raw_columns: tuple[str, ...]
    time_range: TimeRange


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Result of :meth:`QueryPlanner.plan`. The Query Engine executes ``per_tool_queries``."""

    per_tool_queries: list[ToolQuery]
    target_resolution: timedelta
    partial_data_warnings: list[str]


class QueryPlanner:
    """Translates a :class:`Selection` + view mode into per-tool queries."""

    def plan(
        self, selection: Selection, mode: ViewMode, viewport_width_px: int
    ) -> QueryPlan:
        """Pick files, columns, and resolution for the current selection.

        ``target_resolution`` is chosen so each chart yields roughly the
        per-mode point budget (overview ~100, standard ~2000, focus ~5000)
        rounded to a clean bucket. ``partial_data_warnings`` carries
        human-readable notes when the requested range exceeds the local
        extent or when a tool lacks a mapping for a selected canonical.
        """
        raise NotImplementedError
