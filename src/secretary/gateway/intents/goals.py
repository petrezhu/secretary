"""Goal intents — today's focus, longterm progress, inbox."""

from __future__ import annotations

from secretary.coach.focus import select_today_focus
from secretary.gateway.intents.base import IntentContext

_FOCUS_KW = {
    "今天干什么",
    "今天做什么",
    "今天忙什么",
    "今日焦点",
    "焦点",
    "先做哪个",
    "下一步",
    "重点是啥",
    "重点",
}

_LONGTERM_KW = {"长期目标", "年度目标", "人生目标", "目标进度"}

_INBOX_KW = {"收件箱", "inbox", "未处理消息", "待处理消息"}


class FocusHandler:
    name = "today_focus"
    keywords = _FOCUS_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        if not ctx.repo:
            return None
        weekly = await ctx.repo.get_weekly_goals()
        if not weekly:
            return None
        result = select_today_focus(weekly)
        goal = getattr(result, "goal", None)
        if goal is None:
            return None
        deadline = f"，截止 {goal.deadline}" if goal.deadline else ""
        return (
            f"🎯 今日焦点：{goal.title}\n"
            f"进度 {goal.progress}%{deadline}\n"
            f"建议：先推进 30 分钟，启动比完美重要。"
        )


class LongtermHandler:
    name = "longterm_goals"
    keywords = _LONGTERM_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        if not ctx.repo:
            return None
        goals = await ctx.repo.get_longterm_goals()
        if not goals:
            return None
        by_area: dict[str, list] = {}
        for g in goals:
            by_area.setdefault(g.area or "其他", []).append(g)
        lines = ["🌱 长期目标："]
        for area, items in by_area.items():
            for g in items[:2]:
                lines.append(f"  • [{area}] {g.title[:24]} ({g.progress}%)")
        return "\n".join(lines)


class InboxHandler:
    name = "inbox"
    keywords = _INBOX_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        if not ctx.repo:
            return None
        items = await ctx.repo.get_inbox_items(limit=5)
        if not items:
            return "收件箱是空的，都处理完啦 ✨"
        lines = [f"📥 收件箱 {len(items)} 条未处理："]
        for it in items[:3]:
            content = (it.get("content") or "")[:28]
            lines.append(f"  • {content}")
        return "\n".join(lines)


HANDLERS = [FocusHandler(), LongtermHandler(), InboxHandler()]
