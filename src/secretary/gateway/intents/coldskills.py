"""ColdSkill adoption intents — 建议 / 采纳建议#N / 挂到 / 撤销技能 / 技能列表.

These are the user-facing controls of the sedimentation loop (P2):

  - "建议"                                  → candidate list (pull-only)
  - "采纳建议#N 答复：xxx"                   → literal ColdSkill (fixed reply)
  - "采纳建议#N 挂到 持仓"                   → attach ColdSkill (alias for a core intent)
  - "技能列表"                               → skills currently wired
  - "撤销技能 <id>"                          → remove skill dir + git push

All skill mutations happen in the cold-skill git repo (SECRETARY_COLD_SKILLS_DIR).
Writes are committed and pushed; a failed push never blocks local effect.

No LLM anywhere in this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from secretary.gateway.coldskill import SKILLS_DIR_ENV, DEFAULT_SKILLS_DIR
from secretary.gateway.intents.base import IntentContext

logger = logging.getLogger(__name__)

_ADOPT_RE = re.compile(
    r"^采纳建议#(\d+)(?:\s*答复[:：]?\s*(.+)|(?:\s*挂到\s+(.+)))?$"
)
_REVOKE_RE = re.compile(r"^撤销技能\s+(\S+)$")


# ── Repo plumbing ────────────────────────────────────────────────────────────


def skills_repo_dir() -> Path:
    return Path(os.environ.get(SKILLS_DIR_ENV, DEFAULT_SKILLS_DIR))


def _git_run(repodir: Path, *args: str) -> tuple[bool, str]:
    """Run a git command in the skills repo; return (ok, output)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repodir), *args],
            capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, out
    except Exception as exc:
        return False, str(exc)[:200]


def _commit_and_push(repodir: Path, message: str) -> list[str]:
    """git add+commit+push; returns warning fragments (empty = clean)."""
    notes: list[str] = []
    ok, out = _git_run(repodir, "add", "-A")
    if not ok:
        notes.append(f"git add 失败: {out}")
        return notes
    ok, out = _git_run(repodir, "commit", "-m", message)
    if not ok and "nothing to commit" not in out:
        notes.append(f"git commit 失败: {out}")
        return notes
    ok, out = _git_run(repodir, "push", "origin", "HEAD")
    if not ok:
        notes.append(f"推送失败({out[:120]})——本地已生效，稍后可手动推送")
    return notes


def _candidate_file() -> Path:
    from secretary.coldskill.mining import candidates_path

    return candidates_path()


def _load_candidates() -> list[dict]:
    path = _candidate_file()
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("candidates", []))
    except (OSError, json.JSONDecodeError):
        return []


# ── Skill creation ───────────────────────────────────────────────────────────


def _make_skill_id(text: str) -> str:
    """Deterministic snake_case id from the original query text."""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"faq_{digest}"


def _write_literal_skill(repodir: Path, sid: str, trigger: str, reply: str) -> Path:
    sdir = repodir / sid
    sdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": sid,
        "version": 1,
        "mode": "literal",
        "description": f"FAQs: {trigger[:40]}",
        "keywords": [trigger.strip()],
        "replies": [reply.strip()],
        "enabled": True,
        "tags": ["faq", "literal"],
    }
    (sdir / "skill.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return sdir


def _write_attach_skill(repodir: Path, sid: str, trigger: str, target: str) -> Path:
    sdir = repodir / sid
    sdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": sid,
        "version": 1,
        "mode": "attach",
        "description": f"别名: {trigger[:40]} → {target}",
        "keywords": [trigger.strip()],
        "target": target,
        "enabled": True,
        "tags": ["alias", "attach"],
    }
    (sdir / "skill.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return sdir


def _resolve_attach_target(registry: Any, word: str) -> str | None:
    """Map a user-supplied word to a core intent's handler name.

    Accepts a handler name directly ("portfolio") or any exact keyword of
    a core handler ("持仓" → "portfolio").
    """
    from secretary.gateway.intents.base import _exact_keywords

    for handler in registry._handlers:
        name = getattr(handler, "name", "")
        if name.startswith("coldskill:"):
            continue  # skills cannot be attach targets
        if word == name:
            return name
        kw = getattr(handler, "keywords", None)
        if kw and _exact_keywords(word, kw):
            return name
    return None


# ── Intent handlers ──────────────────────────────────────────────────────────


class SuggestHandler:
    """“建议” — pull the mined candidate list (never pushes)."""

    name = "coldskill_suggest"
    keywords = {"建议", "有什么建议", "候选"}

    def __init__(self) -> None:
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        candidates = _load_candidates()
        if not candidates:
            return (
                "🆕 暂无冷技能候选。\n"
                "你反复问过而由 Agent 处理的确定性问法，会在一段时间后被自动沉淀为候选。"
            )
        lines = ["🆕 冷技能候选（采纳后秘书将直接答复，0 token）：", ""]
        for i, cand in enumerate(candidates, 1):
            text = str(cand.get("text", "?"))[:60]
            count = cand.get("count", 0)
            lines.append(f"  #{i} 「{text}」（{count}次）")
        lines.append("")
        lines.append("采纳：#N 答复：xxx → 固定答复 | #N 挂到 持仓 → 别名挂靠")
        lines.append("撤销：撤销技能 <id>")
        return "\n".join(lines)


class AdoptHandler:
    """“采纳建议#N [答复：xxx | 挂到 目标]” — mint a ColdSkill and push."""

    name = "coldskill_adopt"

    def __init__(self) -> None:
        self.patterns = [_ADOPT_RE]

    async def handle(self, ctx: IntentContext) -> str | None:
        m = _ADOPT_RE.match(ctx.text.strip())
        if not m:
            return None
        index = int(m.group(1))
        reply_text = m.group(2)
        attach_word = m.group(3)

        candidates = _load_candidates()
        if not candidates or index < 1 or index > len(candidates):
            return f"候选 #{index} 不存在。输入「建议」查看当前清单。"

        cand = candidates[index - 1]
        trigger = str(cand.get("text", "")).strip()
        if not trigger:
            return "该候选内容异常，无法采纳。"

        repodir = skills_repo_dir()
        if not repodir.is_dir():
            return f"冷技能仓库不存在（{repodir}），无法采纳。稍后通知管理员。"

        sid = _make_skill_id(trigger)

        if reply_text and reply_text.strip():
            _write_literal_skill(repodir, sid, trigger, reply_text)
            notes = _commit_and_push(repodir, f"feat(coldskill): 采纳建议#{index} {trigger} → literal")
            mode_desc = "固定答复"
        elif attach_word and attach_word.strip():
            registry = _registry_from_ctx(ctx)
            target = _resolve_attach_target(registry, attach_word.strip())
            if target is None:
                return f"「{attach_word.strip()}」不是已知意图或关键词，无法挂靠。输入「技能列表」或「帮助」查看可用意图。"
            _write_attach_skill(repodir, sid, trigger, target)
            notes = _commit_and_push(repodir, f"feat(coldskill): 采纳建议#{index} {trigger} → attach {target}")
            mode_desc = f"别名挂靠到「{target}」"
        else:
            return "采纳格式：采纳建议#N 答复：xxx，或 采纳建议#N 挂到 持仓"

        if (repodir / sid).is_dir():
            head = f"✅ 已采纳并生效：新冷技能 {sid}（{mode_desc}）。"
        else:
            head = f"⚠️ 技能写入异常：{sid}"
        if notes:
            return head + "\n" + "\n".join(f"  · {n}" for n in notes)
        return head


def _registry_from_ctx(ctx: IntentContext) -> Any:
    """Prefer the registry carried in ctx (tests, embedded use); else live."""
    registry = getattr(ctx, "extras", {}).get("registry")
    if registry is not None:
        return registry
    return _current_registry()


class SkillListHandler:
    """“技能列表” — skills currently wired into the registry."""

    name = "coldskill_list"
    keywords = {"技能列表", "冷技能列表", "有哪些技能"}

    def __init__(self) -> None:
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        loader = getattr(_registry_from_ctx(ctx), "_skill_loader", None)
        skills = loader.skills if loader is not None else []
        if not skills:
            return "🧊 冷技能仓库暂无生效技能。候选出现后输入「建议」可查看。"
        lines = ["🧊 冷技能（已生效）：", ""]
        for skill in skills:
            m = skill.manifest
            kw = " / ".join(sorted(m.keywords)[:3])
            lines.append(f"  {m.id} [{m.mode}] {m.description} — {kw}")
        lines.append("")
        lines.append("撤销：撤销技能 <id>")
        return "\n".join(lines)


class RevokeHandler:
    """“撤销技能 <id>” — delete the skill dir and push."""

    name = "coldskill_revoke"

    def __init__(self) -> None:
        self.patterns = [_REVOKE_RE]

    async def handle(self, ctx: IntentContext) -> str | None:
        m = _REVOKE_RE.match(ctx.text.strip())
        if not m:
            return None
        sid = m.group(1)
        repodir = skills_repo_dir()
        sdir = repodir / sid
        if not sdir.is_dir():
            return f"技能 {sid} 不存在。输入「技能列表」查看。"
        try:
            shutil.rmtree(sdir)
        except OSError as exc:
            return f"删除技能目录失败：{exc}"
        notes = _commit_and_push(repodir, f"revert(coldskill): 撤销技能 {sid}")
        if notes:
            return f"✅ 已撤销技能 {sid}（本地已失效）。\n" + "\n".join(f"  · {n}" for n in notes)
        return f"✅ 已撤销技能 {sid}，立即失效。"


def _current_registry() -> Any:
    """Access the live registry through the inbound module (set at import)."""
    from secretary.gateway import inbound

    return inbound._registry


HANDLERS = [SuggestHandler(), AdoptHandler(), SkillListHandler(), RevokeHandler()]