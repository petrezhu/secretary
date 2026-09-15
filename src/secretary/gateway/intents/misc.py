"""Misc intents — /help capability table, emotional support."""

from __future__ import annotations

import logging
from typing import Any

from secretary.coach.perception import Emotion, Urgency
from secretary.gateway.intents.base import IntentContext

logger = logging.getLogger(__name__)

_HELP_KW = {
    "/help", "帮助", "你能做什么", "你会什么",
    "有什么指令", "有什么命令", "有什么功能",
    "有啥指令", "有啥命令", "有啥功能",
    "怎么用你", "你会干嘛",
}

# Domain labels for display
_DOMAIN_LABELS = {
    "help": "帮助",
    "greeting": "闲聊",
    "thanks": "闲聊",
    "praise": "闲聊",
    "task_status": "任务",
    "task_create": "任务",
    "task_complete": "任务",
    "task_detail": "任务",
    "today_focus": "目标",
    "longterm_goals": "目标",
    "inbox": "目标",
    "portfolio": "财富",
    "market": "财富",
    "qdii": "财富",
    "gold": "财富",
    "system_health": "系统",
    "checkpoint": "系统",
    "morning_briefing": "系统",
    "system_registry": "系统",
    "system_detail": "系统",
    "memory_cleanup": "系统",
    "emotion": "情绪",
    "memory_query": "记忆",
    "memory_add": "记忆",
    "memory_pending": "记忆",
}

# Human-readable descriptions for each handler
_HANDLER_DESCRIPTIONS = {
    "greeting": "你好 / 早安 / 晚安 — 时段问候",
    "thanks": "谢谢 / 辛苦了 — 客套回复",
    "praise": "厉害 / 牛 / 好样的 — 夸奖回复",
    "task_status": "待办 / 有什么任务 — 活跃任务+周目标摘要",
    "task_create": "记一下任务：写周报 — 快速记录待办",
    "task_complete": "完成#142 — 标记完成",
    "task_detail": "任务#142 — 查任务详情",
    "today_focus": "今天干什么 / 今日焦点 — 焦点推荐",
    "longterm_goals": "长期目标 / 年度目标 — 各领域进度",
    "inbox": "收件箱 — 未处理消息条数",
    "portfolio": "持仓 / 查持仓 — 详细持仓列表",
    "market": "大盘 / 行情 / 沪深300 — 指数实时涨跌",
    "qdii": "qdii / 溢价 — 引导 Agent 做套利分析",
    "gold": "金价 / 黄金 — 上金所Au(T+D)实时价",
    "system_health": "服务器 / 内存 / 磁盘 — CPU/内存/磁盘",
    "checkpoint": "上次存档 — 距上次存档多少天",
    "morning_briefing": "早报 / 日报 — 重发今日简报",
    "system_registry": "系统列表 — 查看所有自持系统",
    "memory_cleanup": "清理内存 / 孤儿进程 — 自动清理",
    "emotion": "说累了/烦 — 我会安抚你",
    "memory_query": "我之前说过什么 — 查看记忆",
    "memory_add": "记住XXX — 显式记住某事",
    "memory_pending": "有什么打算 — 查看待办意图",
}


def generate_help_text(registry: Any) -> str:
    """Dynamically generate help text from the intent registry.

    Scans all registered handlers and builds a grouped keyword table.
    ColdSkills render in their own section from the loader's manifest data.
    """
    lines = ["📖 我能直接答这些（不用等 Agent）："]
    lines.append("")

    # Group handlers by domain
    domains: dict[str, list[str]] = {}
    seen_handlers = set()

    for handler in registry._handlers:
        name = handler.name
        if name in seen_handlers:
            continue
        seen_handlers.add(name)

        # ColdSkill-backed handlers render in the dedicated section instead
        if name.startswith("coldskill:"):
            continue

        # Get domain label
        domain = _DOMAIN_LABELS.get(name, "其他")
        desc = _HANDLER_DESCRIPTIONS.get(name)
        if not desc:
            # Auto-generate from keywords
            kw = getattr(handler, "keywords", None)
            if kw:
                sample = sorted(kw)[:3]
                desc = " / ".join(sample) + " ..."
            else:
                continue

        if domain not in domains:
            domains[domain] = []
        domains[domain].append(f"  {desc}")

    # Domain display order and emojis
    domain_order = [
        ("闲聊", "👋"),
        ("任务", "📋"),
        ("目标", "🎯"),
        ("财富", "💼"),
        ("系统", "🖥️"),
        ("情绪", "💭"),
        ("记忆", "🧠"),
    ]

    for domain_name, emoji in domain_order:
        if domain_name in domains:
            lines.append(f"{emoji} {domain_name}")
            lines.extend(domains[domain_name])
            lines.append("")

    # ── ColdSkill section ──────────────────────────────────────────────
    loader = getattr(registry, "_skill_loader", None)
    skills = loader.skills if loader is not None else []
    if skills:
        lines.append("🧊 冷技能")
        for skill in skills:
            m = skill.manifest
            keywords = " / ".join(sorted(m.keywords)[:5])
            desc = m.description or m.id
            lines.append(f"  {desc} — {keywords}")
        lines.append("")

    lines.append("⚠️ 以上关键词需精确输入才会直接响应。")
    lines.append("包含更多内容的句子会转给 Agent 处理。")

    return "\n".join(lines)


class HelpHandler:
    name = "help"
    keywords = _HELP_KW

    def __init__(self, registry: Any = None):
        self.patterns = []
        self._registry = registry
        self._cached_text: str | None = None
        self._cached_fp: str | None = None

    def set_registry(self, registry: Any) -> None:
        """Set the registry reference for dynamic help generation."""
        self._registry = registry
        self._cached_text = None  # Invalidate cache
        self._cached_fp = None

    def _skills_fingerprint(self) -> str:
        """Change only when the rendered cold-skill lines would change."""
        registry = self._registry
        if registry is None:
            return "none"
        loader = getattr(registry, "_skill_loader", None)
        if loader is None:
            return "none"
        parts = []
        for skill in loader.skills:
            m = skill.manifest
            parts.append(
                f"{m.id}|{m.description}|{'~'.join(sorted(m.keywords))}"
            )
        return "::".join(parts)

    async def handle(self, ctx: IntentContext) -> str:
        if self._registry is None:
            return _STATIC_HELP_TEXT

        fp = self._skills_fingerprint()
        if fp != self._cached_fp:
            self._cached_fp = fp
            text = generate_help_text(self._registry)
            self._cached_text = text
        assert self._cached_text is not None
        return self._cached_text


# Fallback static text (if registry not available)
_STATIC_HELP_TEXT = """📖 我能直接答这些（不用等 Agent）：

👋 闲聊
  你好 / 早安 / 晚安 — 时段问候
  谢谢 / 辛苦了 — 客套回复

📋 任务
  待办 / 有什么任务 — 活跃任务+周目标摘要
  记一下任务：写周报 — 快速记录待办

🎯 目标
  今天干什么 / 今日焦点 — 焦点推荐
  长期目标 / 年度目标 — 各领域进度

💼 财富
  持仓 / 查持仓 — 详细持仓列表
  大盘 / 行情 — 指数实时涨跌

🖥️ 系统
  服务器 / 内存 / 磁盘 — 系统状态
  系统列表 — 查看所有自持系统

🧠 记忆
  我之前说过什么 — 查看记忆
  记住XXX — 显式记住某事

⚠️ 以上关键词需精确输入才会直接响应。
包含更多内容的句子会转给 Agent 处理。"""


class EmotionHandler:
    """Emotional support — same behaviour as the legacy inline branches."""

    name = "emotion"
    keywords = set()  # never matched by keywords; invoked as fallback

    def __init__(self):
        self.patterns = []  # no keywords, no patterns → catch-all fallback

    async def handle(self, ctx: IntentContext) -> str | None:
        from secretary.coach.perception import PerceptionEngine

        p = PerceptionEngine().perceive(ctx.text)
        if p.emotion == Emotion.TIRED and p.urgency in (Urgency.LOW, Urgency.MEDIUM):
            return "注意休息，别太累了。有紧急的事我再叫你。"
        if p.emotion == Emotion.FRUSTRATED:
            return "遇到困难了？需要我帮忙分析一下吗？"
        return None


HANDLERS = [HelpHandler(), EmotionHandler()]
