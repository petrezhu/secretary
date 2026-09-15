"""Tests for the ColdSkill adoption intents (建议/采纳/撤销/技能列表)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from secretary.gateway.coldskill import set_skills_dir
from secretary.gateway.intents import IntentContext, build_default_registry
from secretary.gateway.intents.coldskills import (
    _load_candidates,
    _make_skill_id,
    _resolve_attach_target,
    skills_repo_dir,
)


from tests.helpers import make_ctx


async def dispatch(registry, text: str):
    return await registry.dispatch(make_ctx(text, extras={"registry": registry}))


def _git_init_repo(tmp_path: Path) -> Path:
    """Create a bare-ish local git repo for skill mutations (no remote)."""
    repo = tmp_path / "skills-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"],
                   check=True, capture_output=True)
    return repo


# ── 建议 ─────────────────────────────────────────────────────────────────────


def test_suggest_lists_candidates(monkeypatch, tmp_path: Path):
    cand_file = tmp_path / "rule_candidates.json"
    cand_file.write_text(json.dumps({
        "generated_at": "2026-09-10T02:00:00+00:00",
        "candidates": [
            {"text": "xx怎么用", "count": 4, "last_seen": "x", "suggested_action": "literal"},
            {"text": "yy是什么", "count": 3, "last_seen": "x", "suggested_action": "literal"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    from secretary.gateway.intents import coldskills as mod

    monkeypatch.setattr(mod, "_candidate_file", lambda: cand_file)
    registry = build_default_registry()
    reply, name, conf = asyncio.run(dispatch(registry, "建议"))
    assert name == "coldskill_suggest"
    assert "#1 「xx怎么用」（4次）" in reply
    assert "#2 「yy是什么」（3次）" in reply


def test_suggest_empty(monkeypatch, tmp_path: Path):
    from secretary.gateway.intents import coldskills as mod

    monkeypatch.setattr(mod, "_candidate_file", lambda: tmp_path / "nope.json")
    registry = build_default_registry()
    reply, name, _ = asyncio.run(dispatch(registry, "建议"))
    assert name == "coldskill_suggest"
    assert "暂无" in reply


# ── 采纳: literal ────────────────────────────────────────────────────────────


def test_adopt_literal_creates_and_wires_skill(monkeypatch, tmp_path: Path):
    from secretary.gateway.intents import coldskills as mod

    cand_file = tmp_path / "rule_candidates.json"
    cand_file.write_text(json.dumps({
        "generated_at": "x",
        "candidates": [{"text": "CXO是什么意思", "count": 4, "last_seen": "x"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "_candidate_file", lambda: cand_file)

    repo = _git_init_repo(tmp_path)
    monkeypatch.setattr(mod, "skills_repo_dir", lambda: repo)
    set_skills_dir(repo)

    registry = build_default_registry()
    reply, name, _ = asyncio.run(dispatch(registry, "采纳建议#1 答复：CXO指医药外包合同研究组织"))
    assert name == "coldskill_adopt"
    assert "已采纳" in reply

    sid = _make_skill_id("CXO是什么意思")
    manifest_path = repo / sid / "skill.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["mode"] == "literal"
    assert manifest["keywords"] == ["CXO是什么意思"]

    # hot wired — the skill replies immediately, zero LLM
    import os
    os.environ["SECRETARY_COLD_SKILLS_DIR"] = str(repo)
    loader = registry._skill_loader
    assert loader is not None
    loader._signature_cache = None
    loader.sync(registry)
    reply2, name2, conf2 = asyncio.run(dispatch(registry, "CXO是什么意思"))
    assert reply2 and "医药外包" in reply2
    assert name2 == f"coldskill:{sid}"


def test_adopt_missing_candidate_number(monkeypatch, tmp_path: Path):
    from secretary.gateway.intents import coldskills as mod

    cand_file = tmp_path / "rule_candidates.json"
    cand_file.write_text(json.dumps({
        "candidates": [{"text": "xx", "count": 3, "last_seen": "x"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "_candidate_file", lambda: cand_file)

    registry = build_default_registry()
    reply, name, _ = asyncio.run(dispatch(registry, "采纳建议#5 答复：你好"))
    assert name == "coldskill_adopt"
    assert "不存在" in reply


# ── 采纳: attach ─────────────────────────────────────────────────────────────


def test_adopt_attach_maps_keyword_to_core_handler(monkeypatch, tmp_path: Path):
    from secretary.gateway.intents import coldskills as mod

    cand_file = tmp_path / "rule_candidates.json"
    cand_file.write_text(json.dumps({
        "candidates": [{"text": "我买了啥", "count": 3, "last_seen": "x"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "_candidate_file", lambda: cand_file)

    repo = _git_init_repo(tmp_path)
    monkeypatch.setattr(mod, "skills_repo_dir", lambda: repo)
    set_skills_dir(repo)

    registry = build_default_registry()
    reply, name, _ = asyncio.run(dispatch(registry, "采纳建议#1 挂到 持仓"))
    assert name == "coldskill_adopt"
    assert "已采纳" in reply

    sid = _make_skill_id("我买了啥")
    manifest = json.loads((repo / sid / "skill.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "attach"
    assert manifest["target"] == "portfolio"

    # portfolio keywords extended, hot
    loader = registry._skill_loader
    assert loader is not None
    loader._signature_cache = None
    loader.sync(registry)
    portfolio = next(h for h in registry._handlers if h.name == "portfolio")
    assert "我买了啥" in getattr(portfolio, "keywords", set())


def test_adopt_attach_unknown_target_rejected(monkeypatch, tmp_path: Path):
    from secretary.gateway.intents import coldskills as mod

    cand_file = tmp_path / "rule_candidates.json"
    cand_file.write_text(json.dumps({
        "candidates": [{"text": "xx", "count": 3, "last_seen": "x"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "_candidate_file", lambda: cand_file)

    registry = build_default_registry()
    reply, name, _ = asyncio.run(dispatch(registry, "采纳建议#1 挂到 不存在的意图"))
    assert name == "coldskill_adopt"
    assert "无法挂靠" in reply


# ── 技能列表 / 撤销 ──────────────────────────────────────────────────────────


def test_skill_list_shows_wired_skills(monkeypatch, tmp_path: Path):
    from secretary.gateway.intents import coldskills as mod

    repo = _git_init_repo(tmp_path)
    monkeypatch.setattr(mod, "skills_repo_dir", lambda: repo)
    set_skills_dir(repo)

    (repo / "demo").mkdir()
    (repo / "demo" / "skill.json").write_text(json.dumps({
        "id": "demo", "version": 1, "mode": "literal",
        "description": "演示技能", "keywords": ["演示"], "replies": ["嗯"],
    }, ensure_ascii=False), encoding="utf-8")

    registry = build_default_registry()
    loader = registry._skill_loader
    assert loader is not None
    loader._signature_cache = None
    loader.sync(registry)

    reply, name, _ = asyncio.run(dispatch(registry, "技能列表"))
    assert name == "coldskill_list"
    assert "demo" in reply
    assert "演示技能" in reply


def test_revoke_removes_skill(monkeypatch, tmp_path: Path):
    from secretary.gateway.intents import coldskills as mod

    repo = _git_init_repo(tmp_path)
    monkeypatch.setattr(mod, "skills_repo_dir", lambda: repo)
    set_skills_dir(repo)

    (repo / "demo").mkdir()
    (repo / "demo" / "skill.json").write_text(json.dumps({
        "id": "demo", "version": 1, "mode": "literal",
        "keywords": ["撤销测试"], "replies": ["x"],
    }, ensure_ascii=False), encoding="utf-8")

    registry = build_default_registry()
    loader = registry._skill_loader
    assert loader is not None
    loader._signature_cache = None
    loader.sync(registry)

    reply, name, _ = asyncio.run(dispatch(registry, "撤销技能 demo"))
    assert name == "coldskill_revoke"
    assert "已撤销" in reply
    assert not (repo / "demo").exists()

    # unwired after reload
    loader._signature_cache = None
    loader.sync(registry)
    reply2, name2, _ = asyncio.run(dispatch(registry, "撤销测试"))
    assert name2 != "coldskill:demo"


def test_revoke_missing_skill(monkeypatch, tmp_path: Path):
    from secretary.gateway.intents import coldskills as mod

    repo = _git_init_repo(tmp_path)
    monkeypatch.setattr(mod, "skills_repo_dir", lambda: repo)
    set_skills_dir(repo)

    registry = build_default_registry()
    reply, name, _ = asyncio.run(dispatch(registry, "撤销技能 nope"))
    assert name == "coldskill_revoke"
    assert "不存在" in reply


# ── helpers ──────────────────────────────────────────────────────────────────


def test_make_skill_id_snake_case_and_stable():
    a = _make_skill_id("CXO是什么意思")
    b = _make_skill_id("CXO是什么意思")
    assert a == b
    assert a.startswith("faq_")
    assert a.islower() and " " not in a


def test_resolve_attach_target_by_keyword_and_name():
    registry = build_default_registry()
    assert _resolve_attach_target(registry, "持仓") == "portfolio"
    assert _resolve_attach_target(registry, "portfolio") == "portfolio"
    assert _resolve_attach_target(registry, "你好") == "greeting"
    assert _resolve_attach_target(registry, "不存在词") is None