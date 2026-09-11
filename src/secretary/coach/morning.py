"""Morning briefing generator — aggregates data into a QQ-friendly message."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from secretary.coach.energy import EnergyState
from secretary.coach.focus import select_today_focus
from secretary.data.repository import Goal, Repository

logger = logging.getLogger(__name__)


@dataclass
class BriefingContext:
    """All data needed to generate the morning briefing."""

    greeting: str
    focus_title: str
    focus_reason: str
    focus_progress: int
    pending_task_count: int
    stuck_goals: list[Any] = field(default_factory=list)
    yesterday_completions: list[Any] = field(default_factory=list)
    overdue_goals: list[Any] = field(default_factory=list)
    energy_score: float = 50.0


async def collect_briefing_data(repo: Repository) -> BriefingContext:
    """Collect all data needed for the morning briefing.

    Pulls from goals.db and tasks.db via the Repository.
    """
    # 1. Get weekly goals for focus selection
    weekly_goals: list[Goal] = await repo.get_weekly_goals()

    # 2. Select today's focus
    focus = select_today_focus(weekly_goals)
    focus_title = getattr(focus.goal, "title", "自由安排") if focus.goal else "自由安排"
    focus_reason = focus.reason
    focus_progress = int(getattr(focus.goal, "progress", 0) or 0) if focus.goal else 0

    # 3. Count pending tasks
    active_tasks = await repo.get_active_tasks()
    pending_count = len(active_tasks)

    # 4. Stuck goals (in_progress > 14 days without update)
    stuck = await repo.get_stuck_goals(days=14)

    # 5. Overdue goals (past deadline)
    from secretary.coach.overdue import GoalOverdueDetector

    overdue_detector = GoalOverdueDetector()
    all_goals = await repo.get_weekly_goals() + await repo.get_longterm_goals()
    overdue = overdue_detector.detect(all_goals)

    # 5. Compute energy (completion rate from recent task history)
    # Use recently completed tasks (last 7 days) for energy calculation
    recent_completions = await repo.get_recent_completions(days=7)
    completed_count = len(recent_completions)
    total_tasks = len(active_tasks) + completed_count
    completion_rate = completed_count / total_tasks if total_tasks > 0 else 0.5

    # Estimate consecutive failures (stuck goals + overdue as proxy)
    consecutive_failures = len(stuck) + len(overdue)

    energy = EnergyState.compute(
        completion_rate=completion_rate,
        consecutive_failures=consecutive_failures,
    )

    # 6. Yesterday's completions — tasks completed in the last 24 hours
    yesterday_completions = await repo.get_recent_completions(days=1)

    return BriefingContext(
        greeting=energy.greeting_tone(),
        focus_title=focus_title,
        focus_reason=focus_reason,
        focus_progress=focus_progress,
        pending_task_count=pending_count,
        stuck_goals=stuck,
        yesterday_completions=yesterday_completions,
        overdue_goals=overdue,
        energy_score=energy.score,
    )


def render_morning_briefing(ctx: BriefingContext) -> str:
    """Render the morning briefing as a conversation-style QQ message.

    Uses 亲切助理体 (warm assistant tone) formatting.
    """
    now = datetime.now()
    date_str = now.strftime("%m月%d日")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]

    lines: list[str] = []

    # Greeting
    lines.append(f"☀️ 早安！{date_str} {weekday}")
    lines.append(f"{ctx.greeting}")
    lines.append("")

    # Today's focus
    lines.append(f"🎯 今日重点：{ctx.focus_title}")
    lines.append(f"   {ctx.focus_reason}")
    if ctx.focus_progress > 0:
        lines.append(f"   当前进度 {ctx.focus_progress}%")
    lines.append("")

    # Task overview
    lines.append(f"📋 待处理任务：{ctx.pending_task_count} 个")
    lines.append("")

    # Stuck goals warning
    if ctx.stuck_goals:
        lines.append(f"⚠️ 卡住的目标：{len(ctx.stuck_goals)} 个")
        for g in ctx.stuck_goals[:3]:  # Show at most 3
            title = getattr(g, "title", "未知")
            lines.append(f"   · {title}")
        lines.append("")

    # Overdue goals (gentle reminder, not pushy)
    if ctx.overdue_goals:
        lines.append(f"📅 小提醒：{len(ctx.overdue_goals)} 个目标比计划晚了一点")
        for og in ctx.overdue_goals[:3]:  # Show at most 3
            title = getattr(og.goal, "title", "未知")
            days = og.days_overdue
            if days <= 1:
                lines.append(f"   · {title}（刚到期）")
            elif days <= 7:
                lines.append(f"   · {title}（{days}天了）")
            else:
                lines.append(f"   · {title}（{days}天了，不着急）")
        lines.append("   不着急，有空的时候看看就好")
        lines.append("")

    # Yesterday's completions
    if ctx.yesterday_completions:
        lines.append(f"✅ 昨日完成：{len(ctx.yesterday_completions)} 项")
        for t in ctx.yesterday_completions[:3]:
            title = getattr(t, "request_text", None) or getattr(t, "title", "未知")
            lines.append(f"   · {title}")
        lines.append("")

    # Closing encouragement
    if ctx.energy_score >= 70:
        lines.append("💪 今天加油！有什么需要帮忙的随时说。")
    elif ctx.energy_score >= 40:
        lines.append("👍 稳步推进就好，有事随时叫我。")
    else:
        lines.append("🫂 今天量力而行，我一直在。")

    return "\n".join(lines)


async def generate_morning_briefing(repo: Repository) -> str:
    """Convenience: collect data + render in one call."""
    ctx = await collect_briefing_data(repo)
    return render_morning_briefing(ctx)
