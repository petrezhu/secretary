"""Memory intents — query, add, and manage memories."""

from __future__ import annotations

import logging

from secretary.gateway.intents.base import IntentContext
from secretary.memory import MemoryStore, create_memory, extract_memory

logger = logging.getLogger(__name__)

# Keywords to trigger memory query
_MEMORY_QUERY_KW = {
    "我之前说过什么",
    "我说过什么",
    "之前说过什么",
    "有什么记忆",
    "记忆",
    "你记得什么",
    "我打算做什么",
}

# Keywords to trigger memory add
_MEMORY_ADD_KW = {
    "记住这个",
    "记下这个",
    "记住",
    "记下",
}

# Keywords to trigger pending intents
_MEMORY_PENDING_KW = {
    "待办意图",
    "有什么打算",
    "未完成意图",
}


class MemoryQueryHandler:
    """Handler for querying memories."""

    name = "memory_query"
    keywords = _MEMORY_QUERY_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        store = MemoryStore()

        # Get recent active memories
        memories = store.query(status="active", limit=10)
        if not memories:
            return "暂时没有记忆。有什么想让我记住的吗？"

        lines = ["📝 记忆中的事："]
        lines.append("")

        for m in memories:
            type_emoji = {
                "decision": "✅",
                "intent": "🎯",
                "preference": "💜",
                "fact": "📌",
            }
            emoji = type_emoji.get(m.type, "·")
            lines.append(f"{emoji} {m.content[:60]}")
            if m.entities:
                lines.append(f"   关于：{', '.join(m.entities[:3])}")
            lines.append("")

        count = store.count(status="active")
        if count > len(memories):
            lines.append(f"（共 {count} 条活跃记忆）")

        return "\n".join(lines)


class MemoryAddHandler:
    """Handler for explicitly adding memories."""

    name = "memory_add"
    keywords = _MEMORY_ADD_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        """Check if user explicitly asks to remember something."""
        text = ctx.text.strip()

        # Check for explicit "记住XXX" pattern
        import re

        match = re.match(r"(?:记住|记下)(?:这个)?[：:\s]*(.+)", text)
        if not match:
            return None

        content = match.group(1).strip()
        if not content:
            return None

        # Extract entities and tags
        extraction = extract_memory(content)

        store = MemoryStore()
        entry = create_memory(
            content=content,
            memory_type=extraction.memory_type,
            entities=extraction.entities,
            tags=extraction.tags,
            source="explicit",
        )
        store.append(entry)

        return f"已记住：{content[:50]}"


class MemoryPendingHandler:
    """Handler for querying pending intents/decisions."""

    name = "memory_pending"
    keywords = _MEMORY_PENDING_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        """Show pending intents and decisions."""
        store = MemoryStore()

        # Get pending intents
        intents = store.query(memory_type="intent", status="active", limit=5)
        decisions = store.query(memory_type="decision", status="active", limit=5)

        if not intents and not decisions:
            return "没有待办的意图或决定。"

        lines = []

        if intents:
            lines.append("🎯 待办意图：")
            for m in intents:
                lines.append(f"  · {m.content[:50]}")
            lines.append("")

        if decisions:
            lines.append("✅ 已做决定：")
            for m in decisions:
                lines.append(f"  · {m.content[:50]}")

        return "\n".join(lines)


HANDLERS = [MemoryQueryHandler(), MemoryAddHandler(), MemoryPendingHandler()]
