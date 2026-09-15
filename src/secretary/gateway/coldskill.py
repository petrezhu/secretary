"""ColdSkill loader — 0-token deterministic capability units.

Scans the user-level cold-skill repo and wraps each valid skill into an
intent handler that participates in normal exact-keyword dispatch:

  - literal:  keywords -> fixed replies (manifest only)
  - attach:   keywords merged into an existing core handler's keyword set
  - script:   manifest + script.py exposing ``run(ctx) -> str | None``
              (a ``test_*.py`` file in the same dir is mandatory)

Design rules:
- No LLM anywhere in this module.
- fail-open: any error while loading a skill logs a warning and skips it;
  a missing repo disables the whole skill layer silently.
- Hot reload: skill dir mtime is probed before each dispatch (lstat cost);
  on change the loader re-scans and rewires handlers without a restart.
- Exact-match discipline: handlers reuse ``_exact_keywords`` — substring
  does NOT match, same as all core intents.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from secretary.gateway.intents.base import IntentContext, _exact_keywords

logger = logging.getLogger(__name__)

SKILLS_DIR_ENV = "SECRETARY_COLD_SKILLS_DIR"
DEFAULT_SKILLS_DIR = "/root/git/secretary-cold-skills"

_VALID_MODES = {"literal", "attach", "script"}
_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ── Manifest schema ─────────────────────────────────────────────────────────


class SkillManifest(BaseModel):
    """Validated skill.json schema. Invalid manifests are rejected wholesale."""

    id: str
    version: int
    mode: str
    description: str = ""
    keywords: list[str]
    replies: list[str] = Field(default_factory=list)
    target: str | None = None
    handler: str | None = None
    entry: str = "run"
    priority: int = 100
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    llm_allowed: bool = False  # self-declared only; never enforced (soft rule)

    def cross_check(self) -> str | None:
        """Per-mode requirements beyond pydantic's field checks."""
        if not _SNAKE_RE.match(self.id):
            return f"id '{self.id}' not snake_case"
        if self.mode not in _VALID_MODES:
            return f"unknown mode '{self.mode}'"
        if not self.keywords or any(not str(k).strip() for k in self.keywords):
            return "keywords must be non-empty"
        if self.mode == "literal" and not self.replies:
            return "literal mode requires replies"
        if self.mode == "attach" and not self.target:
            return "attach mode requires target"
        if self.mode == "script" and not self.handler:
            return "script mode requires handler"
        return None


# ── Loaded skill record ─────────────────────────────────────────────────────


@dataclass
class LoadedSkill:
    """A skill that passed validation and (for script mode) runtime checks."""

    manifest: SkillManifest
    directory: Path
    run_fn: Any = None  # only for script mode
    wired: bool = False  # True once actually connected to the registry


# ── Skill-backed intent handlers ────────────────────────────────────────────


class _LiteralSkillHandler:
    """Exact-keyword match -> fixed reply text."""

    def __init__(self, name: str, keywords: set[str], replies: list[str]):
        self.name = name
        self.keywords = keywords
        self.patterns = []
        self._text = "\n".join(str(r) for r in replies)

    async def handle(self, ctx: IntentContext) -> str | None:
        return self._text


class _ScriptSkillHandler:
    """Exact-keyword match -> deterministic ``run(ctx)`` result."""

    def __init__(self, name: str, keywords: set[str], run_fn: Any):
        self.name = name
        self.keywords = keywords
        self.patterns = []
        self._run_fn = run_fn

    async def handle(self, ctx: IntentContext) -> str | None:
        try:
            result = self._run_fn(ctx)
            return result if isinstance(result, str) else None
        except Exception as exc:
            logger.warning("ColdSkill '%s' run failed: %s", self.name, exc)
            return None  # fail-open to agent


# ── Loader ──────────────────────────────────────────────────────────────────


class ColdSkillLoader:
    """Scans the cold-skill repo and rewires the intent registry.

    ``sync(registry)`` is a cheap no-op unless the repo dir mtime changed
    (hot reload) or the loader has never run. It removes handlers it inserted
    previously and re-inserts fresh ones just before the fallback handler
    (EmotionHandler), so core intents always win over skills.
    """

    def __init__(self, dir_path: str | Path | None = None) -> None:
        path = Path(dir_path) if dir_path else Path(
            os.environ.get(SKILLS_DIR_ENV, DEFAULT_SKILLS_DIR)
        )
        self.dir = path
        self._signature_cache: tuple | None = None
        self._loaded: list[LoadedSkill] = []  # last successful scan
        self._inserted_names: list[str] = []  # handler names we inserted
        self._attached: dict[str, set[str]] = {}  # target -> keywords we added

    # ── Public surface ──────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self.dir.is_dir()

    @property
    def skills(self) -> list[LoadedSkill]:
        """Currently loaded skills (for help-menu rendering etc.)."""
        return list(self._loaded)

    def sync(self, registry: Any) -> bool:
        """Probe repo signature; reload + rewire if changed. Never raises."""
        try:
            if not self.available:
                self._teardown(registry)
                return False
            current = self._signature()
            if current is not None and current == self._signature_cache and self._loaded is not None:
                return False
            self._signature_cache = current
            self._load_and_rewire(registry)
            return True
        except Exception as exc:
            logger.warning("ColdSkillLoader.sync failed: %s", exc)
            return False

    # ── Internals ───────────────────────────────────────────────────────

    def _signature(self):
        """Repo change signature: dir mtime + sorted skill-dir names+mtimes.

        Directory-name changes are included because some filesystems have
        coarse mtime granularity — a brand-new skill dir must still trigger
        a reload even if the parent mtime didn't move.
        """
        try:
            parts = [("mtime", self.dir.stat().st_mtime)]
            for entry in sorted(self.dir.iterdir()):
                if entry.is_dir():
                    parts.append((entry.name, entry.stat().st_mtime))
            return tuple(parts)
        except OSError:
            return None

    def _scan(self) -> list[LoadedSkill]:
        """Parse + validate every <dir>/skill.json; skip bad ones."""
        loaded: list[LoadedSkill] = []
        try:
            entries = sorted(self.dir.iterdir())
        except OSError as exc:
            logger.warning("ColdSkill repo unreadable: %s", exc)
            return []
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            skill = self._load_one(entry)
            if skill:
                loaded.append(skill)
        loaded.sort(key=lambda s: (-s.manifest.priority, s.manifest.id))
        return loaded

    def _load_one(self, sdir: Path) -> LoadedSkill | None:
        manifest_path = sdir / "skill.json"
        if not manifest_path.is_file():
            logger.warning("ColdSkill %s: skill.json missing, skipped", sdir.name)
            return None
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = SkillManifest(**raw)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            logger.warning("ColdSkill %s: invalid manifest (%s), skipped", sdir.name, exc)
            return None
        problem = manifest.cross_check()
        if problem:
            logger.warning("ColdSkill %s: %s, skipped", sdir.name, problem)
            return None
        if not manifest.enabled:
            return None
        skill = LoadedSkill(manifest=manifest, directory=sdir)
        if manifest.mode == "script":
            run_fn = self._load_script_fn(sdir, manifest)
            if run_fn is None:
                return None
            skill.run_fn = run_fn
        return skill

    def _load_script_fn(self, sdir: Path, manifest: SkillManifest) -> Any | None:
        if not manifest.handler:
            logger.warning("ColdSkill %s: script mode requires handler, skipped", manifest.id)
            return None
        script_path = sdir / manifest.handler
        if not script_path.is_file():
            logger.warning("ColdSkill %s: script '%s' missing, skipped", manifest.id, manifest.handler)
            return None
        tests = list(sdir.glob("test_*.py"))
        if not tests:
            logger.warning("ColdSkill %s: script mode requires test_*.py, skipped", manifest.id)
            return None
        try:
            spec = importlib.util.spec_from_file_location(
                f"coldskill_{manifest.id}", script_path
            )
            if spec is None or spec.loader is None:
                logger.warning("ColdSkill %s: cannot build import spec, skipped", manifest.id)
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            run_fn = getattr(module, manifest.entry, None)
        except Exception as exc:
            logger.warning("ColdSkill %s: script import failed (%s), skipped", manifest.id, exc)
            return None
        if not callable(run_fn):
            logger.warning("ColdSkill %s: entry '%s' not callable, skipped", manifest.id, manifest.entry)
            return None
        # Soft rule (spec): LLM API references trigger an advisory warning
        # only — never a rejection. Cold/hot boundary is the human's call.
        try:
            src = script_path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            src = ""
        if any(k in src for k in ("chat/completions", "openai", "anthropic", "api.deepseek", "llm")):
            logger.warning(
                "ColdSkill %s: script references LLM-API-like strings (advisory only, "
                "not enforced) — keep 0-token discipline in mind",
                manifest.id,
            )
        return run_fn

    def _load_and_rewire(self, registry: Any) -> None:
        """Remove our old handlers, then re-scan and insert fresh ones."""
        self._teardown(registry)
        scanned = self._scan()
        self._insert(scanned, registry)
        # skills property reflects only skills actually wired into the registry
        self._loaded = [s for s in scanned if s.wired]
        logger.info(
            "ColdSkill layer loaded: %d skill(s) from %s", len(self._loaded), self.dir
        )

    def _teardown(self, registry: Any) -> None:
        """Un-insert our handlers and un-merge any attached keywords."""
        if self._inserted_names:
            registry._handlers[:] = [
                h for h in registry._handlers
                if getattr(h, "name", None) not in self._inserted_names
            ]
        self._inserted_names = []
        for target, added_kw in self._attached.items():
            handler = self._find_handler(registry, target)
            if handler is not None and hasattr(handler, "keywords"):
                try:
                    handler.keywords -= added_kw
                except Exception:
                    pass
        self._attached = {}

    def _insert(self, skills: list[LoadedSkill], registry: Any) -> None:
        attach_pending: list[LoadedSkill] = []
        for skill in skills:
            manifest = skill.manifest
            if manifest.mode == "attach":
                attach_pending.append(skill)
                continue
            name = f"coldskill:{manifest.id}"
            keywords = set(manifest.keywords)
            if manifest.mode == "script":
                handler = _ScriptSkillHandler(name, keywords, skill.run_fn)
            else:
                handler = _LiteralSkillHandler(name, keywords, manifest.replies)
            self._insert_before_fallback(registry, handler)
            self._inserted_names.append(name)
            skill.wired = True
        # attach merges: runtime keywords aliases on core handlers
        for skill in attach_pending:
            self._apply_attach(registry, skill)

    def _apply_attach(self, registry: Any, skill: LoadedSkill) -> None:
        manifest = skill.manifest
        if not manifest.target:
            logger.warning("ColdSkill %s: attach target missing, skipped", manifest.id)
            return
        target = manifest.target
        handler = self._find_handler(registry, target)
        if handler is None:
            logger.warning(
                "ColdSkill %s: attach target '%s' not found, skipped",
                manifest.id, target,
            )
            return
        kw = getattr(handler, "keywords", None)
        if kw is None:
            logger.warning("ColdSkill %s: target '%s' has no keywords", manifest.id, target)
            return
        added = set(manifest.keywords)
        kw.update(added)
        self._attached.setdefault(target, set()).update(added)
        self._inserted_names.append(f"coldskill:{manifest.id}")  # mark handled
        skill.wired = True

    def _insert_before_fallback(self, registry: Any, handler: Any) -> None:
        """Insert before the first catch-all (no keywords, no patterns) handler."""
        handlers = registry._handlers
        for i, h in enumerate(handlers):
            kw = getattr(h, "keywords", None)
            pats = getattr(h, "patterns", [])
            if not kw and not pats:  # fallback
                handlers.insert(i, handler)
                return
        handlers.append(handler)

    @staticmethod
    def _find_handler(registry: Any, name: str) -> Any | None:
        for h in registry._handlers:
            if getattr(h, "name", None) == name:
                return h
        return None


# ── Singleton access ────────────────────────────────────────────────────────

_loader: ColdSkillLoader | None = None


def get_skill_loader() -> ColdSkillLoader:
    """Module-level singleton honouring the env var (read at first use)."""
    global _loader
    if _loader is None:
        _loader = ColdSkillLoader()
    return _loader


def set_skills_dir(path: str | Path) -> None:
    """Test hook: re-point the singleton at an arbitrary directory."""
    global _loader
    _loader = ColdSkillLoader(path)