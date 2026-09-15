"""Tests for the ColdSkill loader — literal / attach / script modes,
validation, fail-open, and hot reload."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from secretary.gateway.coldskill import ColdSkillLoader, set_skills_dir
from secretary.gateway.intents import IntentContext, build_default_registry


# ── Fixtures ────────────────────────────────────────────────────────────────


from tests.helpers import make_ctx, write_skill


# ── literal mode ────────────────────────────────────────────────────────────


def test_literal_exact_match_returns_replies(tmp_path: Path):
    write_skill(tmp_path, "cxo", {
        "id": "cxo", "version": 1, "mode": "literal",
        "description": "CXO问答", "keywords": ["CXO是什么"], "replies": ["CXO指医药外包。"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    reply, name, confidence = None, None, None
    import asyncio
    async def run():
        nonlocal reply, name, confidence
        reply, name, confidence = await registry.dispatch(make_ctx("CXO是什么"))
    asyncio.run(run())
    assert reply == "CXO指医药外包。"
    assert name == "coldskill:cxo"
    assert confidence == "high"


def test_literal_no_substring_match(tmp_path: Path):
    """Exact-match discipline: '持仓怎么样' must NOT hit a '持仓' skill."""
    write_skill(tmp_path, "zc", {
        "id": "zc", "version": 1, "mode": "literal",
        "description": "持仓别名", "keywords": ["持仓"], "replies": ["技能答复"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    import asyncio
    async def run(text):
        return await registry.dispatch(make_ctx(text))
    # substring must not match -> falls through to core 'portfolio'? No —
    # core portfolio handler has its own keyword set; '持仓怎么样' is not
    # an exact keyword anywhere, so it must fall through to the agent.
    reply, name, confidence = asyncio.run(run("持仓怎么样"))
    assert name not in ("coldskill:zc",) or reply is None
    # while exact '持仓' still routes core-first (portfolio) or skill — both fine,
    # but must NOT look like a substring hit of the skill
    reply2, name2, confidence2 = asyncio.run(run("持仓"))
    assert confidence2 in ("high", "medium")  # some handler claimed it
    assert name2 != "coldskill:zc" or reply2 == "技能答复"


def test_literal_trailing_punctuation_still_matches(tmp_path: Path):
    write_skill(tmp_path, "cxo", {
        "id": "cxo", "version": 1, "mode": "literal",
        "keywords": ["CXO是什么"], "replies": ["医药外包"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    import asyncio
    async def run(text):
        return await registry.dispatch(make_ctx(text))
    reply, name, _ = asyncio.run(run("CXO是什么。"))
    assert reply == "医药外包"


# ── attach mode ─────────────────────────────────────────────────────────────


def test_attach_extends_core_handler_keywords(tmp_path: Path):
    write_skill(tmp_path, "alias", {
        "id": "alias", "version": 1, "mode": "attach",
        "target": "portfolio", "keywords": ["我买了什么", "看看仓位"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    # find core portfolio handler
    portfolio = next(h for h in registry._handlers if h.name == "portfolio")
    assert "我买了什么" in getattr(portfolio, "keywords", set())
    assert "看看仓位" in getattr(portfolio, "keywords", set())


def test_attach_missing_target_skipped(tmp_path: Path):
    write_skill(tmp_path, "ghost", {
        "id": "ghost", "version": 1, "mode": "attach",
        "target": "no_such_handler", "keywords": ["x"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)
    assert all(s.manifest.id != "ghost" for s in loader.skills)


def test_attach_unmerged_on_reload(tmp_path: Path):
    """Hot reload must first remove previously attached keywords."""
    write_skill(tmp_path, "alias", {
        "id": "alias", "version": 1, "mode": "attach",
        "target": "greeting", "keywords": ["奥利给"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)
    greeting = next(h for h in registry._handlers if h.name == "greeting")
    assert "奥利给" in getattr(greeting, "keywords", set())

    # Remove the skill dir, force reload (invalid manifest → skill skipped)
    import shutil
    shutil.rmtree(tmp_path / "alias")
    write_skill(tmp_path, "alias", {"id": "Bad!", "version": 1, "keywords": []})

    loader._signature_cache = None  # force rescan
    loader.sync(registry)
    assert "奥利给" not in getattr(greeting, "keywords", set())


# ── script mode ─────────────────────────────────────────────────────────────


def test_script_mode_loads_and_runs(tmp_path: Path):
    write_skill(tmp_path, "calc", {
        "id": "calc", "version": 1, "mode": "script",
        "keywords": ["算一下"], "handler": "skill.py", "entry": "run",
    }, {
        "skill.py": (
            "def run(ctx):\n"
            "    return 'ok:' + ctx.text\n"
        ),
        "test_skill.py": "def test_x(): pass\n",
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    import asyncio
    async def run():
        return await registry.dispatch(make_ctx("算一下"))
    reply, name, _ = asyncio.run(run())
    assert reply == "ok:算一下"
    assert name == "coldskill:calc"


def test_script_without_tests_rejected(tmp_path: Path):
    write_skill(tmp_path, "noisy", {
        "id": "noisy", "version": 1, "mode": "script",
        "keywords": ["x"], "handler": "skill.py",
    }, {"skill.py": "def run(ctx):\n    return 'x'\n"})
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)
    assert all(s.manifest.id != "noisy" for s in loader.skills)


def test_script_non_str_result_returns_none(tmp_path: Path):
    write_skill(tmp_path, "bad", {
        "id": "bad", "version": 1, "mode": "script",
        "keywords": ["badcmd"], "handler": "skill.py",
    }, {
        "skill.py": "def run(ctx):\n    return 42\n",
        "test_skill.py": "def test_x(): pass\n",
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    import asyncio
    async def run():
        return await registry.dispatch(make_ctx("badcmd"))
    reply, name, confidence = asyncio.run(run())
    # handler matched but produced no valid reply → medium (claimed, no data)
    assert name == "coldskill:bad"
    assert reply is None
    assert confidence == "medium"


def test_script_llm_reference_warns_but_loads(tmp_path: Path, caplog):
    """Soft rule: LLM-API-like strings → advisory warning, never a rejection."""
    write_skill(tmp_path, "sneaky", {
        "id": "sneaky", "version": 1, "mode": "script",
        "keywords": ["sneakycmd"], "handler": "skill.py",
    }, {
        "skill.py": (
            "def run(ctx):\n"
            "    # calls api.openai.com/chat/completions in production\n"
            "    return 'still works'\n"
        ),
        "test_skill.py": "def test_x(): pass\n",
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)
    assert any(s.manifest.id == "sneaky" for s in loader.skills)
    assert any("advisory" in r.message for r in caplog.records)


def test_manifest_carries_llm_allowed_flag(tmp_path: Path):
    write_skill(tmp_path, "flagged", {
        "id": "flagged", "version": 1, "mode": "literal",
        "keywords": ["旗"], "replies": ["x"], "llm_allowed": True,
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)
    flagged = next(s for s in loader.skills if s.manifest.id == "flagged")
    assert flagged.manifest.llm_allowed is True


# ── validation / fail-open ──────────────────────────────────────────────────


def test_invalid_manifest_rejected_others_loaded(tmp_path: Path):
    write_skill(tmp_path, "okskill", {
        "id": "okskill", "version": 1, "mode": "literal",
        "keywords": ["好技能"], "replies": ["赞"],
    })
    write_skill(tmp_path, "badskill", {
        "id": "Bad-Id!", "version": 1, "mode": "literal", "keywords": [], "replies": [],
    })
    write_skill(tmp_path, "nomode", {"id": "nomode", "version": 1, "keywords": ["x"]})
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)
    ids = {s.manifest.id for s in loader.skills}
    assert ids == {"okskill"}


def test_missing_repo_fail_open(tmp_path: Path):
    loader = ColdSkillLoader(tmp_path / "does-not-exist")
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)
    # core handlers intact, skill layer just silent
    assert any(h.name == "greeting" for h in registry._handlers)
    assert loader.skills == []


def test_core_handler_still_wins_over_skill(tmp_path: Path):
    """skill keywords must not shadow a core handler's own keywords."""
    write_skill(tmp_path, "dup", {
        "id": "dup", "version": 1, "mode": "literal",
        "keywords": ["你好"], "replies": ["技能占位"],
    })
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    import asyncio
    async def run():
        return await registry.dispatch(make_ctx("你好"))
    reply, name, _ = asyncio.run(run())
    assert name != "coldskill:dup"  # greeting wins (core first)


# ── hot reload ──────────────────────────────────────────────────────────────


def test_hot_reload_picks_up_new_skill(tmp_path: Path):
    loader = ColdSkillLoader(tmp_path)
    registry = build_default_registry()
    registry._skill_loader = loader
    loader.sync(registry)

    write_skill(tmp_path, "late", {
        "id": "late", "version": 1, "mode": "literal",
        "keywords": ["新技能"], "replies": ["热载生效"],
    })
    loader._signature_cache = None  # simulate dir change detection via forced rescan
    loader.sync(registry)

    import asyncio
    async def run():
        return await registry.dispatch(make_ctx("新技能"))
    reply, name, _ = asyncio.run(run())
    assert reply == "热载生效"
    assert name == "coldskill:late"