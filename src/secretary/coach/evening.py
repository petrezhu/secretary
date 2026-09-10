"""Evening review generator — end-of-day recap with gentle tone."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from secretary.coach.energy import EnergyState
from secretary.coach.focus import select_today_focus
from secretary.data.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class EveningContext:
    """Data for the evening review briefing."""

    greeting: str
    completed_today: list[Any] = field(default_factory=list)
    pending_tasks: list[Any] = field(default_factory=list)
    tomorrow_focus_title: str = ""
    tomorrow_focus_reason: str = ""
    energy_score: float = 50.0
    energy_state: str = "medium"  # high/medium/low
    wealth_summary: str = ""  # Portfolio summary for the day


async def collect_evening_data(repo: Repository) -> EveningContext:
    """Collect data for the evening review."""

    # 1. Today's completions
    completed = await repo.get_recent_completions(days=1)

    # 2. Pending tasks
    active_tasks = await repo.get_active_tasks()

    # 3. Tomorrow's focus (from weekly goals)
    weekly_goals = await repo.get_weekly_goals()
    focus = select_today_focus(weekly_goals)
    focus_title = getattr(focus.goal, "title", "自由安排") if focus.goal else "自由安排"
    focus_reason = focus.reason

    # 4. Energy score
    completed_count = len(completed)
    total = len(active_tasks) + completed_count
    completion_rate = completed_count / total if total > 0 else 0.5
    energy = EnergyState.compute(
        completion_rate=completion_rate,
        consecutive_failures=0,
    )

    # 5. Wealth summary (portfolio snapshot)
    wealth_summary = ""
    try:
        from secretary.wealth.portfolio import get_portfolio_snapshot
        _portfolio_env = os.environ.get("SECRETARY_PORTFOLIO_PATH", "").strip()
        if _portfolio_env:
            portfolio_path = Path(_portfolio_env)
        else:
            portfolio_path = None
        if portfolio_path and portfolio_path.is_file():
            snapshot = await get_portfolio_snapshot(portfolio_path)
            if snapshot.holdings:
                total_pnl = snapshot.total_pnl_pct
                emoji = "📈" if total_pnl >= 0 else "📉"
                wealth_summary = (
                    f"{emoji} 持仓 {len(snapshot.holdings)} 只，"
                    f"总盈亏 {total_pnl:+.1f}%"
                )
    except Exception as e:
        logger.debug("Failed to get wealth summary: %s", e)

    # 6. Energy state label
    if energy.score >= 70:
        energy_state = "high"
    elif energy.score >= 40:
        energy_state = "medium"
    else:
        energy_state = "low"

    return EveningContext(
        greeting=energy.greeting_tone(),
        completed_today=completed,
        pending_tasks=active_tasks,
        tomorrow_focus_title=focus_title,
        tomorrow_focus_reason=focus_reason,
        energy_score=energy.score,
        energy_state=energy_state,
        wealth_summary=wealth_summary,
    )


def render_evening_review(ctx: EveningContext) -> str:
    """Render the evening review as a gentle QQ message.

    Tone: reflective, not pushy. Ends with '愿您享受安宁的夜晚'.
    """
    now = datetime.now()
    date_str = now.strftime("%m月%d日")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]

    lines: list[str] = []

    # Greeting
    lines.append(f"🌙 {date_str} {weekday} · 今日小结")
    lines.append("")
    lines.append(f"{ctx.greeting}")
    lines.append("")

    # Energy state
    energy_emoji = {"high": "💪", "medium": "👍", "low": "🫂"}
    energy_label = {"high": "精力充沛", "medium": "状态平稳", "low": "有点疲惫"}
    emoji = energy_emoji.get(ctx.energy_state, "👍")
    label = energy_label.get(ctx.energy_state, "状态平稳")
    lines.append(f"{emoji} 今日能量：{label}（{ctx.energy_score:.0f}分）")
    lines.append("")

    # Today's completions
    if ctx.completed_today:
        lines.append(f"✅ 今日完成了 {len(ctx.completed_today)} 件事：")
        for t in ctx.completed_today[:5]:
            title = getattr(t, "request_text", None) or getattr(t, "title", "未知")
            lines.append(f"   · {title}")
        lines.append("")
    else:
        lines.append("今天没有完成新的任务，也没关系。")
        lines.append("")

    # Pending tasks (just count, don't list)
    if ctx.pending_tasks:
        lines.append(f"📋 还有 {len(ctx.pending_tasks)} 个任务在进行中")
        lines.append("")

    # Wealth summary
    if ctx.wealth_summary:
        lines.append(f"💰 {ctx.wealth_summary}")
        lines.append("")

    # Tomorrow's focus
    if ctx.tomorrow_focus_title and ctx.tomorrow_focus_title != "自由安排":
        lines.append(f"🎯 明天可以重点关注：{ctx.tomorrow_focus_title}")
        if ctx.tomorrow_focus_reason:
            lines.append(f"   {ctx.tomorrow_focus_reason}")
        lines.append("")

    # Closing — gentle, not pushy
    lines.append("愿您享受安宁的夜晚。")

    return "\n".join(lines)


async def generate_evening_review(repo: Repository) -> str:
    """Convenience: collect data + render in one call."""
    ctx = await collect_evening_data(repo)
    return render_evening_review(ctx)
