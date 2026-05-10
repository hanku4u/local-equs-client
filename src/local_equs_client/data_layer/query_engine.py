"""Executes QueryPlans against DuckDB with cancellation support (C1.8, C1.12, C5.2).

Each ``ToolQuery`` runs in its own thread against a fresh in-process DuckDB
connection. Per-tool aggregation is ``time_bucket()`` + ``avg/min/max`` over the
selected raw columns. Results are returned as ``pyarrow.Table`` so consumers
can zero-copy to numpy.

C1.12 adds per-tool error isolation: a failure on one tool comes back as a
:class:`QueryError` for that tool while every other tool's table flows through
normally. ``QueryCancelled`` is the one exception that still propagates — that
signals "stop the whole query," not "this tool failed."

Cancellation is cooperative: pass a ``cancelled()`` callable. When it returns
``True`` between completed queries, in-flight DuckDB connections are
interrupted and :class:`QueryCancelled` is raised.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import timedelta

import duckdb
import pyarrow as pa

from local_equs_client.data_layer.query_cache import CacheKey, QueryCache
from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery

_POLL_INTERVAL_S = 0.05
_MAX_WORKERS = 4

logger = logging.getLogger(__name__)


class QueryCancelled(Exception):
    """Raised by :meth:`QueryEngine.execute` when ``cancelled()`` returns True."""


@dataclass(frozen=True, slots=True)
class QueryError:
    """One tool's query failed. Other tools' results still come back."""

    tool_id: str
    message: str


# Result type for one tool. ``QueryError`` reports a localized failure
# (corrupt parquet, schema mismatch, …) without aborting the whole plan.
type ToolResult = pa.Table | QueryError


class QueryEngine:
    """Executes a :class:`QueryPlan` and returns one result per tool."""

    def __init__(self, cache: QueryCache | None = None) -> None:
        self._cache = cache

    def execute(
        self,
        plan: QueryPlan,
        cancelled: Callable[[], bool] | None = None,
        on_tool_complete: Callable[[str, ToolResult], None] | None = None,
        tool_priority: list[str] | None = None,
    ) -> dict[str, ToolResult]:
        """Execute ``plan`` and return ``{tool_id: ToolResult}``.

        Args:
            plan: The query plan to execute.
            cancelled: Polled between completed queries; raising on True.
            on_tool_complete: Called with ``(tool_id, result)`` as each tool's
                query lands. The C4.4 progressive renderer hooks here so the
                chart grid can fill plots one tool at a time.
            tool_priority: C4.5 — ``tool_id`` order to submit in. Tools listed
                first are submitted first; unlisted tools tail-submit.
        """
        if not plan.per_tool_queries:
            return {}

        is_cancelled = cancelled or (lambda: False)
        active_connections: dict[str, duckdb.DuckDBPyConnection] = {}
        results: dict[str, ToolResult] = {}

        def run_one(tool_query: ToolQuery) -> tuple[str, ToolResult]:
            cache_key = (
                CacheKey.from_tool_query(tool_query, plan.target_resolution)
                if self._cache is not None and tool_query.raw_columns
                else None
            )
            if cache_key is not None:
                cached = self._cache.get(cache_key) if self._cache is not None else None
                if cached is not None:
                    return tool_query.tool_id, cached

            conn = duckdb.connect(":memory:")
            active_connections[tool_query.tool_id] = conn
            try:
                sql = _build_sql(tool_query, plan.target_resolution)
                if not sql:
                    return tool_query.tool_id, pa.table({})
                table = conn.execute(sql).to_arrow_table()
                if self._cache is not None and cache_key is not None:
                    self._cache.put(cache_key, table)
                return tool_query.tool_id, table
            except Exception as exc:  # noqa: BLE001 — boundary capture by design
                logger.warning(
                    "Tool %s query failed: %s", tool_query.tool_id, exc, exc_info=True
                )
                return tool_query.tool_id, QueryError(
                    tool_id=tool_query.tool_id, message=str(exc)
                )
            finally:
                conn.close()
                active_connections.pop(tool_query.tool_id, None)

        ordered_queries = _order_by_priority(plan.per_tool_queries, tool_priority)
        max_workers = min(len(ordered_queries), _MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict[Future[tuple[str, ToolResult]], ToolQuery] = {
                pool.submit(run_one, q): q for q in ordered_queries
            }
            pending: set[Future[tuple[str, ToolResult]]] = set(futures)

            while pending:
                if is_cancelled():
                    for conn in list(active_connections.values()):
                        try:
                            conn.interrupt()
                        except Exception:  # noqa: BLE001 — best-effort interrupt
                            pass
                    for fut in pending:
                        fut.cancel()
                    raise QueryCancelled()

                done, pending = wait(
                    pending, timeout=_POLL_INTERVAL_S, return_when=FIRST_COMPLETED
                )
                for fut in done:
                    tool_id, result = fut.result()
                    results[tool_id] = result
                    if on_tool_complete is not None:
                        try:
                            on_tool_complete(tool_id, result)
                        except Exception:  # noqa: BLE001 — callback errors must not kill the engine
                            logger.warning(
                                "on_tool_complete callback failed for %s", tool_id, exc_info=True
                            )

        return results


def _order_by_priority(
    queries: list[ToolQuery], priority: list[str] | None
) -> list[ToolQuery]:
    """Stable-sort ``queries`` so tools in ``priority`` come first, original order tail."""
    if not priority:
        return list(queries)
    priority_index = {tool_id: i for i, tool_id in enumerate(priority)}
    return sorted(
        queries,
        key=lambda q: (priority_index.get(q.tool_id, len(priority)), q.tool_id),
    )


def _build_sql(query: ToolQuery, resolution: timedelta) -> str:
    """Build the DuckDB SQL for one tool. Returns empty string when nothing to query."""
    if not query.file_paths or not query.raw_columns:
        return ""

    paths_sql = ", ".join(_quote_string(str(p)) for p in query.file_paths)
    interval_sql = _interval_string(resolution)
    start_sql = _quote_string(query.time_range.start.strftime("%Y-%m-%d %H:%M:%S.%f"))
    end_sql = _quote_string(query.time_range.end.strftime("%Y-%m-%d %H:%M:%S.%f"))

    select_parts = [f"time_bucket({interval_sql}, ts) AS bucket"]
    for col in query.raw_columns:
        c = _quote_ident(col)
        select_parts.extend(
            [
                f"avg({c}) AS {_quote_ident(col + '_avg')}",
                f"min({c}) AS {_quote_ident(col + '_min')}",
                f"max({c}) AS {_quote_ident(col + '_max')}",
            ]
        )

    return (
        f"SELECT {', '.join(select_parts)}\n"
        f"FROM read_parquet([{paths_sql}], union_by_name = TRUE)\n"
        f"WHERE ts >= TIMESTAMP {start_sql} AND ts < TIMESTAMP {end_sql}\n"
        f"GROUP BY bucket\n"
        f"ORDER BY bucket"
    )


def _interval_string(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    if seconds <= 0:
        seconds = 1
    if seconds % 86400 == 0:
        return f"INTERVAL '{seconds // 86400} days'"
    if seconds % 3600 == 0:
        return f"INTERVAL '{seconds // 3600} hours'"
    if seconds % 60 == 0:
        return f"INTERVAL '{seconds // 60} minutes'"
    return f"INTERVAL '{seconds} seconds'"


def _quote_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


__all__ = ["QueryCancelled", "QueryEngine", "QueryError", "ToolResult"]
