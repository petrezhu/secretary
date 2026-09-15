"""Tests for the help-menu ColdSkill section (T6)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from secretary.gateway.coldskill import ColdSkillLoader
from secretary.gateway.intents import IntentContext, build_default_registry


from tests.helpers import make_ctx, write_skill


async def get_help(registry) -> str:
    ctx = make_ctx("/help")
    reply, _, _ = await registry.dispatch(ctx)
    return reply


def test_help_renders_coldskill_section(tmp_path: Path):
    write_skill(tmp_path, "cxo", {
        "id": "cxo", "version": 1, "mode": "literal",
        "description": "CXO与QDII关系问答", "keywords": ["CXO是什么"],
        "replies": ["……"], "tags": ["财富", "faq"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    text = asyncio.run(get_help(registry))
    assert "🧊 冷技能" in text
    assert "CXO与QDII关系问答" in text
    assert "CXO是什么" in text


def test_help_hides_section_when_no_skills(tmp_path: Path):
    loader = ColdSkillLoader(tmp_path)  # empty dir
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    text = asyncio.run(get_help(registry))
    assert "🧊 冷技能" not in text
    # core sections intact
    assert "👋 闲聊" in text
    assert "📋 任务" in text


def test_help_shows_only_wired_skills(tmp_path: Path):
    write_skill(tmp_path, "ghost", {
        "id": "ghost", "version": 1, "mode": "attach",
        "target": "no_such_handler", "keywords": ["x"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    text = asyncio.run(get_help(registry))
    assert "🧊 冷技能" not in text  # nothing wired → section hidden


def test_help_refreshes_after_skill_added(tmp_path: Path):
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    first = asyncio.run(get_help(registry))
    assert "🧊 冷技能" not in first

    write_skill(tmp_path, "cxo", {
        "id": "cxo", "version": 1, "mode": "literal",
        "description": "CXO问答", "keywords": ["CXO是什么"], "replies": ["……"],
    })
    loader.sync(registry)  # hot reload

    second = asyncio.run(get_help(registry))
    assert "🧊 冷技能" in second
    assert "CXO问答" in second