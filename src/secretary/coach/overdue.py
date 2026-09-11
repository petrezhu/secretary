"""Goal overdue detection and coaching message generation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from secretary.data.repository import Goal


class Severity(Enum):
    """Overdue severity levels."""

    TODAY = "today"
    THIS_WEEK = "this_week"
    LONG_OVERDUE = "long_overdue"


@dataclass
class OverdueGoal:
    """A goal that has passed its deadline."""

    goal: Goal
    severity: Severity
    days_overdue: int
    coaching_message: str


# ── Coaching message templates (教练体) ─────────────────────────────────────

_TEMPLATES_TODAY = [
    "这个目标今天到期了，{title}——你打算什么时候动手？",
    "{title} 今天就要到了，先做第一步？",
    "今天是 {title} 的截止日，现在开始还不晚。",
]

_TEMPLATES_WEEK = [
    "这个目标你已经拖了{days}天了，是什么卡住了？",
    "{title} 过期{days}天了。是目标太难，还是没有动力？",
    "{title} 已经超期{days}天了，要不要拆成更小的步骤？",
]

_TEMPLATES_LONG = [
    "{title} 已经拖了{days}天了……是时候重新评估一下这个目标了。",
    "这个目标({title})已经{days}天没有进展了。是继续还是放弃？",
    "{title} 超期{days}天。是目标本身需要调整，还是需要换个方式？",
]


class GoalOverdueDetector:
    """Scan goals for overdue items and generate coaching messages."""

    def __init__(self, today: datetime | None = None):
        self._today = today or datetime.now()

    def detect(self, goals: Sequence[Goal]) -> list[OverdueGoal]:
        """Return overdue goals sorted by severity (most urgent first)."""
        overdue: list[OverdueGoal] = []
        for goal in goals:
            if goal.status == "completed":
                continue
            if not goal.deadline:
                continue
            try:
                deadline = datetime.strptime(goal.deadline, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            days = (self._today - deadline).days
            if days < 0:
                continue  # not yet overdue
            severity = self._classify(days)
            msg = self._generate_message(goal, severity, days)
            overdue.append(
                OverdueGoal(
                    goal=goal,
                    severity=severity,
                    days_overdue=days,
                    coaching_message=msg,
                )
            )
        # Sort: LONG_OVERDUE > THIS_WEEK > TODAY
        severity_order = {Severity.LONG_OVERDUE: 0, Severity.THIS_WEEK: 1, Severity.TODAY: 2}
        overdue.sort(key=lambda o: (severity_order[o.severity], -o.days_overdue))
        return overdue

    def generate_summary(self, overdue_goals: list[OverdueGoal]) -> str:
        """Generate a coaching summary for all overdue goals."""
        if not overdue_goals:
            return "目前没有超期的目标，保持节奏！"

        lines = [f"你有 {len(overdue_goals)} 个超期目标需要关注：\n"]
        for og in overdue_goals:
            lines.append(f"• {og.coaching_message}")
        return "\n".join(lines)

    @staticmethod
    def _classify(days_overdue: int) -> Severity:
        if days_overdue <= 0:
            return Severity.TODAY
        elif days_overdue <= 7:
            return Severity.THIS_WEEK
        else:
            return Severity.LONG_OVERDUE

    def _generate_message(self, goal: Goal, severity: Severity, days: int) -> str:

        title = goal.title or "未命名目标"
        # Deterministic template selection based on goal id
        idx = hash(goal.id) % 3

        if severity == Severity.TODAY:
            template = _TEMPLATES_TODAY[idx]
        elif severity == Severity.THIS_WEEK:
            template = _TEMPLATES_WEEK[idx]
        else:
            template = _TEMPLATES_LONG[idx]

        return template.format(title=title, days=days)
