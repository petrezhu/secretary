"""Task intents — query, create, complete, detail."""

from __future__ import annotations

import re

from secretary.gateway.intents.base import IntentContext

# Query: pending/in-progress summary
_STATUS_RE = re.compile(
    r"(今天有什么|待办|有什么任务|今日安排|日程|status|todo|what.*do)",
    re.IGNORECASE,
)

# Create: 帮我记一下任务：xxx / 记一个待办 xxx
_CREATE_RE = re.compile(
    r"(?:帮我|记一下|添加|创建|新建|记住|记)\s*(?:一个)?(?:任务|待办|todo)[：:\s]*(.+)",
    re.IGNORECASE,
)

# Complete: 完成(任务|待办) #142 / 完成#142
_COMPLETE_RE = re.compile(
    r"(?:完成|搞定|做完)\s*(?:任务|待办)?\s*#?(\d+)\s*[！!。.]*$",
    re.IGNORECASE,
)

# Detail: 任务#142 / 查一下#142
_DETAIL_RE = re.compile(
    r"(?:任务|待办|查(?:一下)?)\s*#(\d+)",
    re.IGNORECASE,
)


class TaskStatusHandler:
    name = "task_status"
    patterns = [_STATUS_RE]

    async def handle(self, ctx: IntentContext) -> str | None:
        if not ctx.repo:
            return None
        tasks = await ctx.repo.get_active_tasks()
        weekly = await ctx.repo.get_weekly_goals()
        return _render_status(tasks, weekly)


class TaskCreateHandler:
    name = "task_create"
    patterns = [_CREATE_RE]

    async def handle(self, ctx: IntentContext) -> str | None:
        if not ctx.repo:
            return None
        m = _CREATE_RE.search(ctx.text)
        title = (m.group(1) if m else "").strip()
        if not title:
            return None
        async with ctx.repo.tasks_db() as conn:
            cursor = conn.execute(
                "INSERT INTO tasks (request_text, status, priority) VALUES (?, 'pending', 3)",
                (title,),
            )
            conn.commit()
            task_id = cursor.lastrowid
        return f"收到，已记录待办：{title}（#{task_id}）"


class TaskCompleteHandler:
    name = "task_complete"
    patterns = [_COMPLETE_RE]

    async def handle(self, ctx: IntentContext) -> str | None:
        if not ctx.repo:
            return None
        m = _COMPLETE_RE.search(ctx.text)
        if not m:
            return None
        task_id = int(m.group(1))
        if not await ctx.repo.complete_task(task_id):
            # Not found or already done — let the agent explain.
            return None
        return f"收到，#{task_id} 已完成 ✅"


class TaskDetailHandler:
    name = "task_detail"
    patterns = [_DETAIL_RE]

    async def handle(self, ctx: IntentContext) -> str | None:
        if not ctx.repo:
            return None
        m = _DETAIL_RE.search(ctx.text)
        if not m:
            return None
        task_id = int(m.group(1))
        async with ctx.repo.tasks_db() as conn:
            row = conn.execute(
                "SELECT id, request_text, status, priority, created_at, notes "
                "FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            return f"没有找到 #{task_id} 这条任务。"
        return (
            f"#{row['id']} [{row['status']}] 优先级{row['priority']}\n"
            f"{row['request_text']}\n"
            f"创建: {row['created_at']}"
            + (f"\n备注: {row['notes']}" if row["notes"] else "")
        )


def _render_status(tasks, weekly) -> str:
    pending = [t for t in tasks if t.status == "pending"]
    in_progress = [t for t in tasks if t.status == "in_progress"]
    active_weekly = [
        g for g in weekly if g.status in ("active", "in_progress")
    ]

    lines = []
    if in_progress:
        lines.append(f"🔄 进行中: {len(in_progress)} 项")
        for t in in_progress[:3]:
            lines.append(f"  • {t.request_text[:30]}")
    if pending:
        lines.append(f"📋 待办: {len(pending)} 项")
        for t in pending[:3]:
            lines.append(f"  • {t.request_text[:30]}")
    if active_weekly:
        lines.append(f"🎯 周目标: {len(active_weekly)} 项")
        for g in active_weekly[:3]:
            lines.append(f"  • {g.title[:30]}")

    if not lines:
        return "当前没有活跃的待办和目标，难得清闲！"
    return "\n".join(lines)


HANDLERS = [
    TaskStatusHandler(),
    TaskCreateHandler(),
    TaskCompleteHandler(),
    TaskDetailHandler(),
]
