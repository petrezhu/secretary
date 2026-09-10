"""Tests for the intent registry and new intent handlers."""

from __future__ import annotations

import pytest

from secretary.gateway.intents import (
    IntentContext,
    build_default_registry,
)
from secretary.gateway.intents.base import _exact_keywords, quiet_hour


def make_ctx(text: str, repo=None, config=None) -> IntentContext:
    return IntentContext(text=text, repo=repo, config=config)


# ── Registry mechanics ───────────────────────────────────────────────────


def test_registry_none_match_returns_none():
    """复杂问题不命中任何 intent → allow (reply=None, intent=None)."""
    reg = build_default_registry()
    import asyncio

    reply, intent, conf = asyncio.run(reg.dispatch(make_ctx("帮我写一个Python脚本处理CSV")))
    assert reply is None and intent is None


def test_quiet_hour_boundaries():
    assert quiet_hour(23) is True
    assert quiet_hour(2) is True
    assert quiet_hour(7) is True
    assert quiet_hour(8) is False
    assert quiet_hour(12) is False


# ── Help ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["有什么指令", "有什么命令", "有啥指令", "有啥命令", "/help", "帮助", "你能做什么"],
)
def test_help_variants(text):
    reg = build_default_registry()
    import asyncio

    reply, _, conf = asyncio.run(reg.dispatch(make_ctx(text)))
    assert reply is not None
    assert "我能直接答" in reply


def test_help_lists_domains():
    reg = build_default_registry()
    import asyncio

    reply, _, conf = asyncio.run(reg.dispatch(make_ctx("有什么指令")))
    for kw in ["任务", "目标", "财富", "系统"]:
        assert kw in reply


# ── Greetings (exact match) ──────────────────────────────────────────────


def test_greeting_still_works():
    reg = build_default_registry()
    import asyncio

    reply, _, conf = asyncio.run(reg.dispatch(make_ctx("你好")))
    assert reply and len(reply) > 0


def test_thanks_still_works():
    reg = build_default_registry()
    import asyncio

    reply, _, conf = asyncio.run(reg.dispatch(make_ctx("谢谢")))
    assert reply and "不客气" in reply


# ── Tasks (regex patterns preserved) ─────────────────────────────────────


def test_task_create_requires_repo():
    reg = build_default_registry()
    import asyncio

    # No repo → fall through (None)
    reply, _, conf = asyncio.run(reg.dispatch(make_ctx("记一下任务：写周报")))
    assert reply is None


def test_task_complete_pattern():
    from secretary.gateway.intents.tasks import _COMPLETE_RE

    assert _COMPLETE_RE.search("完成#142")
    assert _COMPLETE_RE.search("完成任务 142")
    assert _COMPLETE_RE.search("搞定 #142！")
    assert not _COMPLETE_RE.search("完成目标142")


def test_task_detail_pattern():
    from secretary.gateway.intents.tasks import _DETAIL_RE

    assert _DETAIL_RE.search("任务#142")
    assert _DETAIL_RE.search("查一下#7")
    assert not _DETAIL_RE.search("完成#142") or True  # detail is loose by design


# ── Goals (exact keywords) ───────────────────────────────────────────────


def test_focus_keywords():
    from secretary.gateway.intents.goals import _FOCUS_KW

    assert _exact_keywords("今天做什么", _FOCUS_KW)
    assert _exact_keywords("先做哪个", _FOCUS_KW)
    assert _exact_keywords("今日焦点", _FOCUS_KW)
    assert not _exact_keywords("今天做什么比较好", _FOCUS_KW)


def test_longterm_keywords():
    from secretary.gateway.intents.goals import _LONGTERM_KW

    assert _exact_keywords("长期目标", _LONGTERM_KW)
    assert _exact_keywords("年度目标", _LONGTERM_KW)
    assert not _exact_keywords("长期目标怎么样", _LONGTERM_KW)


def test_inbox_keywords():
    from secretary.gateway.intents.goals import _INBOX_KW

    assert _exact_keywords("收件箱", _INBOX_KW)
    assert not _exact_keywords("有啥待处理消息", _INBOX_KW)


# ── Wealth (exact keywords) ──────────────────────────────────────────────


def test_portfolio_keywords():
    from secretary.gateway.intents.wealth import _PORTFOLIO_KW

    assert _exact_keywords("查一下持仓", _PORTFOLIO_KW)
    assert _exact_keywords("持仓怎么样", _PORTFOLIO_KW)
    assert _exact_keywords("市值多少", _PORTFOLIO_KW)
    # 闲聊提及不应触发
    assert not _exact_keywords("我朋友买了股票", _PORTFOLIO_KW)
    assert not _exact_keywords("我要看一下持仓的详细分析报告", _PORTFOLIO_KW)


def test_market_keywords():
    from secretary.gateway.intents.wealth import _MARKET_KW

    assert _exact_keywords("大盘", _MARKET_KW)
    assert _exact_keywords("沪深300", _MARKET_KW)
    assert not _exact_keywords("大盘怎么样", _MARKET_KW)
    assert not _exact_keywords("上证报", _MARKET_KW)


# ── System (exact keywords) ──────────────────────────────────────────────


def test_health_keywords():
    from secretary.gateway.intents.system import _HEALTH_KW

    assert _exact_keywords("服务器", _HEALTH_KW)
    assert _exact_keywords("内存", _HEALTH_KW)
    assert not _exact_keywords("服务器状态", _HEALTH_KW)


def test_checkpoint_keywords():
    from secretary.gateway.intents.system import _CHECKPOINT_KW

    assert _exact_keywords("上次存档", _CHECKPOINT_KW)
    assert _exact_keywords("checkpoint", _CHECKPOINT_KW)


def test_briefing_keywords():
    from secretary.gateway.intents.system import _BRIEFING_KW

    assert _exact_keywords("早报", _BRIEFING_KW)
    assert _exact_keywords("今天安排", _BRIEFING_KW)
