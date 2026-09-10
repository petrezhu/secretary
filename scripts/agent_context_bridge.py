""" Secretary → Agent 上下文桥（pre_llm_call shell hook）。

注入两部分内容到Agent上下文：

1. Secretary旁路播报：最近拦截（handle）的消息摘要
2. 记忆系统：用户说过的事、做过的决定、进行中的意图

约定：
- Secretary 侧 inbound.handle 把每次 handle 记录到
  $SECRETARY_DATA_DIR/secretary_handled.jsonl
- 记忆存储在 ~/.secretary/memory.jsonl
- 本脚本 tail 最近 N 条，渲染为紧凑摘要
- 无记录 / 文件不存在 → 输出空（无注入），零开销
- 任何异常 → exit 0 静默（fail-open，绝不阻塞 Agent）

Hermes shell hook 契约：stdout 输出 JSON {"context": "..."} 即注入用户消息。
"""

from __future__ import annotations

import json

# Secretary旁路播报
import os
import time
from pathlib import Path

_DATA_DIR = os.environ.get("SECRETARY_DATA_DIR", "")
LEDGER = Path(
    os.path.join(_DATA_DIR, "secretary_handled.jsonl") if _DATA_DIR else "/tmp/secretary_handled.jsonl"
)
MAX_ITEMS = 5
MAX_AGE_HOURS = 24
MAX_CHARS = 600

# 记忆系统
MEMORY_FILE = Path.home() / ".secretary" / "memory.jsonl"
MAX_MEMORIES = 8
MAX_MEMORY_CHARS = 400


def _collect_handled_entries() -> list[str]:
    """Collect recent Secretary-handled message summaries."""
    try:
        if not LEDGER.exists():
            return []
        now = time.time()
        entries: list[dict] = []
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = e.get("ts", 0)
                if now - ts <= MAX_AGE_HOURS * 3600:
                    entries.append(e)
        if not entries:
            return []

        recent = entries[-MAX_ITEMS:]
        lines = ["[Secretary旁路播报] 以下消息由 Secretary 直接处理，未经过你："]
        for e in recent:
            age_min = int((now - e.get("ts", now)) / 60)
            when = f"{age_min}分钟前" if age_min < 90 else f"{age_min // 60}小时前"
            text = str(e.get("text", ""))[:40]
            reply = str(e.get("reply", ""))[:50]
            intent = e.get("intent", "?")
            lines.append(f"- {when} 「{text}」→ {intent}: 「{reply}」")
        lines.append("（用户若问起这些消息，知悉 Secretary 已代答即可，勿重复回复）")
        return lines
    except Exception:
        return []


def _collect_memories() -> list[str]:
    """Collect recent active memories from the memory system."""
    try:
        if not MEMORY_FILE.exists():
            return []

        entries: list[dict] = []
        with open(MEMORY_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("status") == "active":
                    entries.append(e)

        if not entries:
            return []

        # Get most recent memories
        recent = entries[-MAX_MEMORIES:]
        lines = ["[Secretary记忆] 用户近期的相关记忆："]

        type_emoji = {
            "decision": "✅",
            "intent": "🎯",
            "preference": "💜",
            "fact": "📌",
        }

        for e in recent:
            emoji = type_emoji.get(e.get("type", "fact"), "·")
            content = str(e.get("content", ""))[:50]
            entities = e.get("entities", [])
            entity_str = f"（关于：{', '.join(entities[:2])}）" if entities else ""
            lines.append(f"{emoji} {content}{entity_str}")

        lines.append("（这些是用户之前表达过的意图/决定/偏好，回复时可参考）")
        return lines
    except Exception:
        return []


def main() -> None:
    try:
        all_lines: list[str] = []

        # Part 1: Secretary旁路播报
        handled_lines = _collect_handled_entries()
        all_lines.extend(handled_lines)

        # Part 2: 记忆系统
        memory_lines = _collect_memories()
        if memory_lines:
            if all_lines:
                all_lines.append("")  # separator
            all_lines.extend(memory_lines)

        if not all_lines:
            return

        context = "\n".join(all_lines)[:MAX_CHARS + MAX_MEMORY_CHARS]
        print(json.dumps({"context": context}, ensure_ascii=False))
    except Exception:
        # fail-open: hook 失败绝不阻塞 Agent
        return


if __name__ == "__main__":
    main()
