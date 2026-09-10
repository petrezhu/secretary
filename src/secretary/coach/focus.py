"""Focus selection — pick today's single most important goal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Priority sort key: P0 < P1 < P2 < P3 < empty
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _priority_key(goal: Any) -> int:
    """Extract numeric priority for sorting (lower = more important)."""
    p = getattr(goal, "priority", None) or ""
    if isinstance(p, int):
        return p
    return _PRIORITY_ORDER.get(str(p).upper().strip(), 99)


def _deadline_key(goal: Any) -> float:
    """Return days until deadline (float('inf') if no deadline or past)."""
    raw = getattr(goal, "deadline", None)
    if not raw:
        return float("inf")
    try:
        # Support both "YYYY-MM-DD" and ISO datetime
        target = datetime.fromisoformat(str(raw))
        delta = (target - datetime.now()).total_seconds()
        # Overdue deadlines: negative seconds still sort first (most urgent),
        # only "no deadline" maps to inf.
        return delta
    except (ValueError, TypeError):
        return float("inf")


def _progress_key(goal: Any) -> int:
    """Return progress percentage (lower = should be prioritised first)."""
    return int(getattr(goal, "progress", 0) or 0)


@dataclass
class FocusResult:
    """Result of focus selection."""

    goal: Any | None  # The Goal dataclass, or None if no goals available
    reason: str  # Human-readable reason why this goal was chosen


def select_today_focus(goals: list[Any]) -> FocusResult:
    """From a list of weekly goals, pick the single most important one.

    Selection priority (in order):
    1. Highest priority (P0 > P1 > P2 > P3)
    2. Nearest deadline
    3. Lowest progress

    Parameters
    ----------
    goals:
        List of Goal dataclass instances (or anything with .priority,
        .deadline, .progress attributes).

    Returns
    -------
    FocusResult
        Contains the chosen goal and a reason string.  If *goals* is empty,
        returns ``FocusResult(None, "本周没有活跃目标，可以自由安排")``.
    """
    if not goals:
        return FocusResult(goal=None, reason="本周没有活跃目标，可以自由安排")

    # Sort by (priority, deadline, progress)
    sorted_goals = sorted(
        goals,
        key=lambda g: (_priority_key(g), _deadline_key(g), _progress_key(g)),
    )
    chosen = sorted_goals[0]

    # Build reason
    parts: list[str] = []
    p = getattr(chosen, "priority", None) or ""
    if p:
        parts.append(f"优先级 {p}")
    dl = getattr(chosen, "deadline", None)
    if dl:
        parts.append(f"截止 {dl}")
    prog = getattr(chosen, "progress", 0) or 0
    parts.append(f"进度 {prog}%")

    reason = "，".join(parts) if parts else "最优先目标"
    return FocusResult(goal=chosen, reason=reason)
