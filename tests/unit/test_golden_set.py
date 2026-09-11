"""Golden set regression tests — real messages from gateway.log mapped to
expected intent outcomes.

Each entry: (message_text, expected_handler_name_or_None)
- handler name  → message MUST be handled by that intent (reply not None)
- None          → message MUST fall through to the agent (dispatch → None
                  during daytime, or quiet-hours reply at night; we assert
                  "no patterned intent claims it")

Sourced from gateway.log (2026-07~09).
Keep in sync when adding new intents.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from secretary.gateway.intents import (
    build_default_registry,
)
from secretary.gateway.intents.base import _exact_keywords, _matches

GOLDEN_SET = [
    # ── greetings ─────────────────────────────────────────────────────
    ("你好", "greeting"),
    ("早上好", "greeting"),
    ("你好！", "greeting"),
    # ("早", "greeting"),  # '早' alone: covered by greeting regex
    ("谢谢", "thanks"),
    ("厉害", "praise"),
    ("哈哈 有点丑，但是就这样吧。", None),  # chatter → allow (day) / quiet (night)
    ("收到了", None),
    ("需要", None),
    ("写！", None),
    # ── tasks ─────────────────────────────────────────────────────────
    ("待办", "task_status"),
    ("待办有什么", "task_status"),
    ("今天有什么任务", "task_status"),
    # ── goals ─────────────────────────────────────────────────────────
    ("今天做什么", "today_focus"),
    ("收件箱", "inbox"),
    ("长期目标", "longterm_goals"),
    ("长期目标怎么样", None),  # exact match only, not substring
    # ── wealth ────────────────────────────────────────────────────────
    ("查一下持仓", "portfolio"),
    ("持仓", "portfolio"),
    ("更新持仓", None),  # exact match only, not substring
    ("持仓顾问skill", None),  # exact match only
    ("请用持仓顾问skill分析下持仓", None),  # exact match only
    ("[Voice] 今天持仓。合计收益是怎么样的？", None),  # exact match only
    ("大盘怎么样", None),  # exact match only, "大盘" is the keyword
    # ── system ────────────────────────────────────────────────────────
    ("服务器", "system_health"),
    ("服务器状态", None),  # exact match only, "服务器" is the keyword
    ("现在服务器的资源占用情况怎么样", None),  # exact match only
    # ── help ──────────────────────────────────────────────────────────
    ("帮助", "help"),
    ("有什么指令", "help"),
    # ── complex → must NOT be claimed by any patterned intent ────────
    ("调查一下方案C是否可行", None),
    ("用SVG画：你坐在高山草甸上看着对面的雪山", None),
    ("7.09卖出农业银行1400股", None),  # trade record → agent
    ("今天11.910清仓了 佳禾智能", None),
    ("今日的股价监测", None),  # ambiguous → agent decides
    ("我重启了gateway了，secretary-gateway插件 没有生效啊", None),
    ("上一条是Secretary的回复吗", None),
    ("你是什么模型id", None),
    ("你作为Secretary（root/git/s ecretary)运行两三天了，你自己思考一下怎么让", None),
    ("注意一下，TickTick同步: Token文件553小时未更新。  你可以设计个Token保活程序", None),
    ("“注意一下，TickTick同步: Token文件50小时未更新。” 你又提醒", None),
    ("这个token要3年才过期不是吗，把过期检测延长到一年", None),
    ("其他没问题，但是“Hermes Gateway”这一点，我想改成Hermes自己的Gateway停掉", None),
    ("/btw 当前对话的session Id 是多少", None),
]


def _is_quiet() -> bool:
    h = datetime.now().hour
    return h >= 23 or h < 8


def _find_claimed(registry, text: str) -> str | None:
    """Which intent claims this text (first keyword or pattern hit)."""
    for handler in registry._handlers:
        kw = getattr(handler, "keywords", None)
        if kw and _exact_keywords(text, kw):
            return handler.name
        if handler.patterns and _matches(handler.patterns, text):
            return handler.name
    return None


@pytest.mark.parametrize("text,expected", GOLDEN_SET, ids=[g[0][:14] for g in GOLDEN_SET])
def test_golden_message_routing(text, expected):
    registry = build_default_registry()
    claimed = _find_claimed(registry, text)
    if expected is None:
        assert claimed is None, f"{text!r}: 期望放行 Agent，但被意图 '{claimed}' 认领"
    else:
        assert claimed == expected, f"{text!r}: 期望意图 '{expected}'，实际被 '{claimed}' 认领"


def test_golden_set_stats():
    """Sanity: the golden set is meaningfully large and balanced."""
    claimed = sum(1 for _, e in GOLDEN_SET if e is not None)
    passthrough = sum(1 for _, e in GOLDEN_SET if e is None)
    assert len(GOLDEN_SET) >= 30
    assert claimed >= 15
    assert passthrough >= 10
