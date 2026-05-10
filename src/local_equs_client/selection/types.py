"""Value types shared by the Selection Model, Query Planner, and Local Library (C0.6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ViewMode = Literal["overview", "standard", "focus"]
GroupBy = Literal["sensor", "tool", "both"]


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Closed-open interval ``[start, end)`` in absolute UTC time."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class Selection:
    """Immutable snapshot of the user's current selection.

    ``sensors_canonical`` is populated once C3.1 lands; M1 selections only fill
    ``sensors_raw``.
    """

    tools: tuple[str, ...]
    sensors_canonical: tuple[str, ...]
    sensors_raw: tuple[str, ...]
    time_range: TimeRange
