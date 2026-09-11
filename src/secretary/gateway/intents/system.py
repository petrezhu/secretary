"""System intents — server health, last checkpoint, morning briefing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from secretary.gateway.intents.base import IntentContext

_HEALTH_KW = {
    "服务器",
    "系统状态",
    "内存",
    "磁盘",
    "cpu",
    "CPU",
    "健康状态",
    "健康检查",
    "健康",
    "service",
    "服务状态",
}

_CHECKPOINT_KW = {"上次存档", "存档点", "checkpoint"}

_BRIEFING_KW = {"早报", "简报", "日报", "今天安排", "今日安排"}

_CLEANUP_KW = {"清理内存", "清理进程", "孤儿进程", "内存清理", "process cleanup", "memory cleanup"}

# psutil-free quick thresholds for reporting
_WARN = 90.0


class HealthHandler:
    name = "system_health"
    keywords = _HEALTH_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        import asyncio

        import psutil

        loop = asyncio.get_running_loop()

        def _collect() -> dict:
            disk = psutil.disk_usage("/")
            return {
                "mem": psutil.virtual_memory().percent,
                "cpu": psutil.cpu_percent(interval=0.3),
                "disk": disk.percent,
            }

        stats = await loop.run_in_executor(None, _collect)
        issues = [f"{k} {v}%" for k, v in stats.items() if v >= _WARN]
        if issues:
            return f"⚠️ 系统有点紧张：{'、'.join(issues)}。其他正常。"
        return (
            f"🖥️ 系统：内存 {stats['mem']:.0f}% / CPU {stats['cpu']:.0f}% / "
            f"磁盘 {stats['disk']:.0f}%，一切正常。"
        )


class CheckpointHandler:
    name = "checkpoint"
    keywords = _CHECKPOINT_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        path = getattr(getattr(ctx.config, "data", None), "checkpoint_file", None)
        if not path or not Path(path).exists():
            return None
        last_line = ""
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return None
        entry = json.loads(last_line)
        date = entry.get("date", "未知")
        progress = (entry.get("progress") or "")[:40]
        try:
            days = (datetime.now() - datetime.strptime(date, "%Y-%m-%d")).days
        except ValueError:
            days = None
        age = f"，已经 {days} 天了" if days is not None and days >= 1 else ""
        return f"💾 上次存档：{date}「{progress}」{age}。需要存一下吗？"


class BriefingHandler:
    name = "morning_briefing"
    keywords = _BRIEFING_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        if not ctx.repo:
            return None
        from secretary.coach.morning import generate_morning_briefing

        return await generate_morning_briefing(ctx.repo)


class MemoryCleanupHandler:
    name = "memory_cleanup"
    keywords = _CLEANUP_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        import asyncio
        import importlib.util
        import sys
        from pathlib import Path

        # Import the cleanup script
        script_path = Path(__file__).resolve().parents[4] / "scripts" / "memory_cleanup.py"
        if not script_path.exists():
            return "⚠️ 清理脚本不存在，请检查 scripts/memory_cleanup.py"

        spec = importlib.util.spec_from_file_location("memory_cleanup", script_path)
        if not spec or not spec.loader:
            return "⚠️ 无法加载清理脚本"
        mod = importlib.util.module_from_spec(spec)
        sys.modules["memory_cleanup"] = mod  # dataclasses needs this
        spec.loader.exec_module(mod)

        loop = asyncio.get_running_loop()
        # dry-run first
        report = await loop.run_in_executor(None, mod.run_cleanup, True)

        if not report.found:
            return report.summary()

        # Execute actual cleanup
        report_kill = await loop.run_in_executor(None, mod.run_cleanup, False)
        return report_kill.summary()


HANDLERS = [MemoryCleanupHandler(), HealthHandler(), CheckpointHandler(), BriefingHandler()]
