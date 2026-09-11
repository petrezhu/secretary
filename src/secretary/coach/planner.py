"""Adaptive planner — adjust task list based on energy state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from secretary.data.repository import Task

if TYPE_CHECKING:
    from secretary.coach import EnergyState


@dataclass
class PlanAdjustment:
    """Describes how the plan was adjusted."""

    original_count: int
    adjusted_count: int
    reason: str
    tasks: list[Task] = field(default_factory=list)


class AdaptivePlanner:
    """Adjust daily task list based on energy and recent performance."""

    # Energy thresholds
    LOW_ENERGY = 40
    HIGH_ENERGY = 75

    def __init__(
        self,
        max_tasks: int = 8,
        consecutive_failures: int = 0,
        recent_completion_rate: float = 0.5,
    ):
        self.max_tasks = max_tasks
        self.consecutive_failures = consecutive_failures
        self.recent_completion_rate = recent_completion_rate

    def plan(
        self,
        tasks: Sequence[Task],
        energy: EnergyState,
    ) -> PlanAdjustment:
        """Select and adjust tasks based on energy state and history."""
        available = [t for t in tasks if t.status in ("pending", "in_progress")]
        original_count = len(available)

        if not available:
            return PlanAdjustment(
                original_count=0,
                adjusted_count=0,
                reason="没有待办任务",
                tasks=[],
            )

        # Sort by priority (lower number = higher priority)
        available.sort(key=lambda t: t.priority)

        # Apply adjustments
        adjusted, reason = self._apply_rules(available, energy)

        return PlanAdjustment(
            original_count=original_count,
            adjusted_count=len(adjusted),
            reason=reason,
            tasks=adjusted,
        )

    def _apply_rules(
        self,
        tasks: list[Task],
        energy: EnergyState,
    ) -> tuple[list[Task], str]:
        """Apply planning rules. Returns (selected_tasks, reason)."""

        # Rule 1: Low energy → reduce quantity
        if energy.score < self.LOW_ENERGY:
            limit = max(1, len(tasks) // 3)
            return tasks[:limit], f"精力不足({energy.score:.0f})，今天只做最重要的 {limit} 件事"

        # Rule 2: Consecutive failures → reduce difficulty (pick only easy/low-pri tasks)
        if self.consecutive_failures >= 3:
            # Keep only the top-priority tasks, max 2
            easy = tasks[:2]
            return easy, f"连续失败 {self.consecutive_failures} 次，先从简单的事开始"

        # Rule 3: High completion rate + high energy → add challenge
        if self.recent_completion_rate >= 0.8 and energy.score >= self.HIGH_ENERGY:
            limit = min(self.max_tasks + 2, len(tasks))
            return tasks[:limit], f"最近完成率 {self.recent_completion_rate:.0%}，可以多做一点"

        # Default: normal planning
        limit = min(self.max_tasks, len(tasks))
        return tasks[:limit], "按正常节奏推进"
