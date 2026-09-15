"""System registry intents — query the self-hosted systems list."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from secretary.gateway.intents.base import IntentContext

logger = logging.getLogger(__name__)

# Systems registry file
_SYSTEMS_FILE = Path(__file__).parent.parent.parent.parent.parent / "config" / "systems.json"
_LOCAL_FILE = _SYSTEMS_FILE.parent / "local.json"

# Keywords to trigger system lookup
_SYSTEMS_KW = {
    "系统列表", "自持系统", "系统在哪", "系统状态",
    "服务列表", "服务在哪", "有哪些系统", "有哪些服务",
}


def _load_systems(
    systems_file: Path | None = None,
    local_file: Path | None = None,
) -> list[dict]:
    """Load systems registry from systems.json + local.json (merged).

    File overrides exist for tests; default to the project config files.
    """
    systems: list[dict] = []
    main_file = systems_file if systems_file is not None else _SYSTEMS_FILE
    try:
        if main_file.exists():
            data = json.loads(main_file.read_text())
            systems = data.get("systems", [])
    except Exception as e:
        logger.warning("Failed to load systems registry: %s", e)

    # Merge local.json (user's personal systems, gitignored)
    local = local_file if local_file is not None else _LOCAL_FILE
    try:
        if local.exists():
            local_data = json.loads(local.read_text())
            local_systems = local_data.get("systems", [])
            existing_names = {s.get("name") for s in systems}
            for s in local_systems:
                if s.get("name") not in existing_names:
                    systems.append(s)
    except Exception as e:
        logger.warning("Failed to load local systems: %s", e)

    return systems


class SystemRegistryHandler:
    """Handler for querying the self-hosted systems list."""

    name = "system_registry"
    keywords = _SYSTEMS_KW

    def __init__(self):
        self.patterns = []  # exact match, no regex

    async def handle(self, ctx: IntentContext) -> str | None:
        systems = _load_systems()
        if not systems:
            return None

        lines = ["🖥️ 自持系统列表："]
        lines.append("")
        for sys in systems:
            name = sys.get("name", "未知")
            desc = sys.get("description", "")
            port = sys.get("port", "")
            notes = sys.get("notes", "")

            # Format: name + description
            line = f"  · {name}"
            if desc:
                # Truncate description if too long
                if len(desc) > 40:
                    desc = desc[:37] + "..."
                line += f" — {desc}"
            lines.append(line)

            # Add port if available
            if port:
                lines.append(f"    端口: {port}")

            # Add notes if available
            if notes:
                lines.append(f"    备注: {notes}")

        lines.append("")
        lines.append("问具体系统的命令或位置，我会详细告诉你。")
        return "\n".join(lines)


class SystemDetailHandler:
    """Handler for querying details of a specific system."""

    name = "system_detail"

    def __init__(self):
        self.patterns = []  # Not pattern-based

    async def handle(self, ctx: IntentContext) -> str | None:
        """Check if user is asking about a specific system."""
        text = ctx.text.strip()
        systems = _load_systems()

        if not systems:
            return None

        # Try to match system name in text
        for sys in systems:
            name = sys.get("name", "").lower()
            if name and name in text.lower():
                return self._format_system_detail(sys)

        return None

    def _format_system_detail(self, sys: dict) -> str:
        """Format detailed info for a single system."""
        name = sys.get("name", "未知")
        desc = sys.get("description", "")
        location = sys.get("location", "")
        commands = sys.get("commands", {})
        port = sys.get("port", "")
        url = sys.get("url", "")
        notes = sys.get("notes", "")

        lines = [f"📋 {name}"]
        if desc:
            lines.append(f"  {desc}")
        lines.append("")

        if location:
            lines.append(f"  📁 位置: {location}")
        if port:
            lines.append(f"  🔌 端口: {port}")
        if url:
            lines.append(f"  🌐 地址: {url}")
        if notes:
            lines.append(f"  📝 备注: {notes}")

        if commands:
            lines.append("")
            lines.append("  常用命令：")
            for cmd_name, cmd_value in commands.items():
                lines.append(f"    {cmd_name}: {cmd_value}")

        return "\n".join(lines)


HANDLERS = [SystemRegistryHandler(), SystemDetailHandler()]
