"""Plans per-tool DuckDB queries from a Selection + ViewMode (C0.6, C1.7, C3.2, C4.2).

C0.6 defined the contract. C1.7 picked files from the Local Library and chose
the resolution. C3.2 (this revision) expands ``selection.sensors_canonical``
into per-tool raw column names via :class:`MetadataCache`, and records any
``(tool_id, canonical)`` pair the metadata cache can't map. C4.2 will scale
the point budget per ViewMode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from local_equs_client.data_layer.local_library import LocalFile, LocalLibrary
from local_equs_client.selection.types import Selection, TimeRange, ViewMode

if TYPE_CHECKING:
    from local_equs_client.data_layer.metadata_cache import MetadataCache

_TARGET_POINTS_STANDARD = 2000

# Clean buckets that DuckDB's time_bucket() handles cleanly. Ordered ascending;
# the smallest bucket whose interval is >= the raw "range / target_points" wins.
_BUCKETS: tuple[timedelta, ...] = (
    timedelta(seconds=1),
    timedelta(seconds=10),
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(hours=1),
    timedelta(days=1),
)


@dataclass(frozen=True, slots=True)
class ToolQuery:
    """A single DuckDB query against one tool's parquet files."""

    tool_id: str
    file_paths: tuple[Path, ...]
    raw_columns: tuple[str, ...]
    time_range: TimeRange


@dataclass(frozen=True, slots=True)
class MissingMapping:
    """A canonical sensor the metadata cache can't translate for this tool."""

    tool_id: str
    canonical_name: str


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Result of :meth:`QueryPlanner.plan`. The Query Engine executes ``per_tool_queries``."""

    per_tool_queries: list[ToolQuery]
    target_resolution: timedelta
    partial_data_warnings: list[str]
    missing_mappings: list[MissingMapping] = field(default_factory=list)


class QueryPlanner:
    """Translates a :class:`Selection` + view mode into per-tool queries."""

    def __init__(
        self,
        library: LocalLibrary,
        metadata_cache: MetadataCache | None = None,
    ) -> None:
        self._library = library
        self._cache = metadata_cache

    def plan(
        self, selection: Selection, mode: ViewMode, viewport_width_px: int
    ) -> QueryPlan:
        """Pick files, columns, and resolution for the current selection.

        Canonical sensors are expanded per tool through ``MetadataCache.mapping``;
        missing mappings are surfaced via :attr:`QueryPlan.missing_mappings` and
        the human-readable warnings list. M1 chose ``target_resolution`` for
        ~2000 points per chart regardless of ``mode`` and ``viewport_width_px``
        — C4.2 wires those in.
        """
        target_resolution = _pick_bucket(selection.time_range, _TARGET_POINTS_STANDARD)

        per_tool: list[ToolQuery] = []
        warnings: list[str] = []
        missing: list[MissingMapping] = []

        for tool_id in selection.tools:
            files = self._library.files_for(tool_id, selection.time_range)
            file_paths = tuple(f.path for f in files)

            raw_columns = self._resolve_columns(tool_id, selection, warnings, missing)

            per_tool.append(
                ToolQuery(
                    tool_id=tool_id,
                    file_paths=file_paths,
                    raw_columns=raw_columns,
                    time_range=selection.time_range,
                )
            )

            warnings.extend(_coverage_warnings(tool_id, selection.time_range, files))

        return QueryPlan(
            per_tool_queries=per_tool,
            target_resolution=target_resolution,
            partial_data_warnings=warnings,
            missing_mappings=missing,
        )

    def _resolve_columns(
        self,
        tool_id: str,
        selection: Selection,
        warnings: list[str],
        missing: list[MissingMapping],
    ) -> tuple[str, ...]:
        """Combine raw + canonical-derived columns for one tool. Dedupes order-preserving."""
        ordered: dict[str, None] = {}
        for raw in selection.sensors_raw:
            ordered[raw] = None

        for canonical in selection.sensors_canonical:
            if self._cache is None:
                warnings.append(
                    f"{tool_id}: no metadata cache; canonical '{canonical}' skipped."
                )
                missing.append(MissingMapping(tool_id=tool_id, canonical_name=canonical))
                continue
            mapped = self._cache.mapping(tool_id, canonical)
            if mapped is None:
                warnings.append(
                    f"{tool_id}: no mapping for '{canonical}'; chart will show no data."
                )
                missing.append(MissingMapping(tool_id=tool_id, canonical_name=canonical))
                continue
            ordered[mapped] = None

        return tuple(ordered)


def _pick_bucket(time_range: TimeRange, target_points: int) -> timedelta:
    """Smallest bucket whose interval is >= ``range / target_points``."""
    range_seconds = max(0.0, (time_range.end - time_range.start).total_seconds())
    raw_seconds = range_seconds / target_points if target_points > 0 else 0.0
    for bucket in _BUCKETS:
        if bucket.total_seconds() >= raw_seconds:
            return bucket
    return _BUCKETS[-1]


def _coverage_warnings(
    tool_id: str,
    time_range: TimeRange,
    files: list[LocalFile],
) -> list[str]:
    if not files:
        return [f"No local data for {tool_id} in the requested range."]

    earliest = min(f.min_ts for f in files)
    latest = max(f.max_ts for f in files)
    warnings: list[str] = []
    if earliest > time_range.start:
        warnings.append(
            f"{tool_id}: range starts before local data (earliest {earliest.isoformat()})."
        )
    if latest < time_range.end:
        warnings.append(
            f"{tool_id}: range ends after local data (latest {latest.isoformat()})."
        )
    return warnings


__all__ = [
    "MissingMapping",
    "QueryPlan",
    "QueryPlanner",
    "ToolQuery",
]
