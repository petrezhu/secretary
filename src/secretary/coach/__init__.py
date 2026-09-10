"""Coach layer — goal coaching, energy assessment, plan adjustment."""

from __future__ import annotations

from secretary.coach.energy import EnergyState
from secretary.coach.focus import FocusResult, select_today_focus
from secretary.coach.morning import (
    BriefingContext,
    collect_briefing_data,
    generate_morning_briefing,
    render_morning_briefing,
)
from secretary.coach.overdue import GoalOverdueDetector, OverdueGoal, Severity
from secretary.coach.perception import Emotion, PerceptionEngine, PerceptionResult, Urgency
from secretary.coach.planner import AdaptivePlanner, PlanAdjustment

__all__ = [
    "AdaptivePlanner",
    "BriefingContext",
    "Emotion",
    "EnergyState",
    "FocusResult",
    "GoalOverdueDetector",
    "OverdueGoal",
    "PerceptionEngine",
    "PerceptionResult",
    "PlanAdjustment",
    "Severity",
    "Urgency",
    "collect_briefing_data",
    "generate_morning_briefing",
    "render_morning_briefing",
    "select_today_focus",
]
