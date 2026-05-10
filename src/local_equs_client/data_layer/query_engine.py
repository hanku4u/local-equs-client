"""Executes QueryPlans against DuckDB with cancellation support (C1.8, C5.2).

Each ``ToolQuery`` runs in its own thread against a fresh in-process DuckDB
connection. Per-tool aggregation is ``time_bucket()`` + ``avg/min/max`` over the
selected raw columns. Results are returned as ``pyarrow.Table`` so consumers
can zero-copy to numpy.

Cancellation is cooperative: pass a ``cancelled()`` callable. When it returns
``True`` between completed queries, in-flight DuckDB connections are
interrupted and :class:`QueryCancelled` is raised.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import timedelta

import duckdb
import pyarrow as pa

from local_equs_client.data_layer.query_planner import QueryPlan, ToolQuery

_POLL_INTERVAL_S = 0.05
_MAX_WORKERS = 4


class QueryCancelled(Exception):
    """Raised by :meth:`QueryEngine.execute` when ``cancelled()`` returns True."""


class QueryEngine:
    """Executes a :class:`QueryPlan` and returns one Arrow table per tool."""

    def execute(
        self,
        plan: QueryPlan,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, pa.Table]:
        if not plan.per_tool_queries:
            return {}

        is_cancelled = cancelled or (lambda: False)
        active_connections: dict[str, duckdb.DuckDBPyConnection] = {}
        results: dict[str, pa.Table] = {}

        def run_one(tool_query: ToolQuery) -> tuple[str, pa.Table]:
            conn = duckdb.connect(":memory:")
            active_connections[tool_query.tool_id] = conn
            try:
                sql = _build_sql(tool_query, plan.target_resolution)
                if not sql:
                    return tool_query.tool_id, pa.table({})
                table = conn.execute(sql).to_arrow_table()
                return tool_query.tool_id, table
            finally:
                conn.close()
                active_connections.pop(tool_query.tool_id, None)

        max_workers = min(len(plan.per_tool_queries), _MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict[Future[tuple[str, pa.Table]], ToolQuery] = {
                pool.submit(run_one, q): q for q in plan.per_tool_queries
            }
            pending: set[Future[tuple[str, pa.Table]]] = set(futures)

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
                    tool_id, table = fut.result()
                    results[tool_id] = table

        return results


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


__all__ = ["QueryCancelled", "QueryEngine"]
