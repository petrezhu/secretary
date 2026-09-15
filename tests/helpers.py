"""Shared test helpers — Duplicated Code fix: Q2."""

from __future__ import annotations

import json
from pathlib import Path

from secretary.gateway.intents import IntentContext


def make_ctx(text: str, extras: dict | None = None, **kwargs) -> IntentContext:
    """Create an IntentContext with ``text`` plus any extra fields.

    ``extras`` is merged into the context's extras dict (used for passing
    the registry to coldskill adoption handlers in tests).
    """
    ctx = IntentContext(text=text, **kwargs)
    if extras:
        ctx.extras.update(extras)
    return ctx


def write_skill(
    skills_dir: Path,
    sdir: str,
    manifest: dict,
    files: dict | None = None,
) -> Path:
    """Create a skill directory with skill.json (+ optional script files).

    Returns the directory path for assertions.
    """
    d = skills_dir / sdir
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    for name, content in (files or {}).items():
        (d / name).write_text(content, encoding="utf-8")
    return d