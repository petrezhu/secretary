"""Memory extractor — extract entities and intent from user messages.

Uses keyword rules (no LLM dependency) to identify:
- Entities: stock names, system names, people, etc.
- Intent type: decision, intent, fact, preference
- Tags: investment, task, system, etc.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"


@dataclass
class ExtractionResult:
    """Result of memory extraction from text."""

    entities: list[str]
    memory_type: str  # decision, intent, fact, preference
    tags: list[str]
    should_memorize: bool
    summary: str  # Short summary for memory content


# ── Entity patterns ──────────────────────────────────────────────────────────


# Stock names — loaded from config/local_stocks.json (gitignored)
def _load_stock_patterns(local_file: Path | None = None) -> list[str]:
    """Load personal stock patterns from config/local_stocks.json.

    ``local_file`` override exists for tests; defaults to the project config.
    """
    patterns: list[str] = []
    path = local_file if local_file is not None else _CONFIG_DIR / "local_stocks.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data.get("stock_patterns", []):
                p = entry.get("pattern", "")
                if p:
                    patterns.append(p)
        except Exception:
            pass
    return patterns


_STOCK_PATTERNS = _load_stock_patterns()

# System names
_BOT_NAME = os.environ.get("SECRETARY_BOT_NAME", "")
_BOT_PATTERNS = [r"(Secretary|秘书)"]
if _BOT_NAME:
    _BOT_PATTERNS.append(rf"({_BOT_NAME})")

_SYSTEM_PATTERNS = [
    *_BOT_PATTERNS,
    r"(Hermes)",
    r"(NewAPI|新API)",
    r"(ARK|方舟)",
    r"(Forgejo|Git)",
    r"(Obsidian|笔记)",
    r"(东方财富|东财)",
    r"(TickTick|滴答)",
]

# Decision indicators
_DECISION_PATTERNS = [
    r"(决定|确认|确定|选择|改成|改为|换成|就用|就定)",
    r"(同意|接受|批准|通过)",
    r"(不要|不做|放弃|排除|删掉)",
]

# Intent indicators (future actions)
_INTENT_PATTERNS = [
    r"(等.*再|回头|下次|以后|有空|有时间)",
    r"(打算|计划|准备|想要|想要)",
    r"(待办|记一下|记住|别忘了)",
    r"(补仓|加仓|减仓|清仓|卖出|买入)",
]

# Preference indicators
_PREFERENCE_PATTERNS = [
    r"(喜欢|偏好|习惯|风格)",
    r"(不要|别|不想|不需要)",
    r"(最好|希望|期望|期待)",
]

# Tag patterns
_TAG_PATTERNS = {
    "投资": [r"(持仓|股票|基金|盈亏|收益|补仓|减仓|清仓|板块|大盘)"],
    "任务": [r"(任务|待办|完成|TODO|todo)"],
    "系统": [r"(服务器|部署|端口|配置|重启|docker|nginx)"],
    "目标": [r"(目标|进度|计划|deadline|截止)"],
    "偏好": [r"(喜欢|偏好|习惯|风格|不要|别)"],
}


def extract_memory(text: str) -> ExtractionResult:
    """Extract memory-worthy information from user message.

    Args:
        text: User message text

    Returns:
        ExtractionResult with entities, type, tags, and whether to memorize
    """
    entities = []
    tags = []
    memory_type = "fact"
    should_memorize = False

    # Extract stock entities
    for pattern in _STOCK_PATTERNS:
        matches = re.findall(pattern, text)
        entities.extend(matches)

    # Extract system entities
    for pattern in _SYSTEM_PATTERNS:
        matches = re.findall(pattern, text)
        entities.extend(matches)

    # Determine memory type
    for pattern in _DECISION_PATTERNS:
        if re.search(pattern, text):
            memory_type = "decision"
            should_memorize = True
            break

    if not should_memorize:
        for pattern in _INTENT_PATTERNS:
            if re.search(pattern, text):
                memory_type = "intent"
                should_memorize = True
                break

    if not should_memorize:
        for pattern in _PREFERENCE_PATTERNS:
            if re.search(pattern, text):
                memory_type = "preference"
                should_memorize = True
                break

    # Extract tags
    for tag, patterns in _TAG_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                if tag not in tags:
                    tags.append(tag)
                break

    # If entities found, mark for memorization
    if entities and not should_memorize:
        should_memorize = True
        memory_type = "fact"

    # Generate summary
    summary = _generate_summary(text, memory_type, entities)

    # Deduplicate entities
    entities = list(dict.fromkeys(entities))

    return ExtractionResult(
        entities=entities,
        memory_type=memory_type,
        tags=tags,
        should_memorize=should_memorize,
        summary=summary,
    )


def _generate_summary(text: str, memory_type: str, entities: list[str]) -> str:
    """Generate a short summary for the memory."""
    # Truncate text to reasonable length
    if len(text) > 100:
        text = text[:97] + "..."

    type_prefix = {
        "decision": "决定",
        "intent": "打算",
        "preference": "偏好",
        "fact": "记录",
    }
    prefix = type_prefix.get(memory_type, "记录")
    return f"{prefix}：{text}"


def is_memorable_message(text: str) -> bool:
    """Quick check if a message is worth memorizing (cheap pre-filter)."""
    # Skip very short messages
    if len(text) < 5:
        return False

    # Skip greetings and thanks
    if text.strip() in ("你好", "早安", "晚安", "谢谢", "辛苦了", "厉害"):
        return False

    # Skip slash commands
    return not text.startswith("/")
