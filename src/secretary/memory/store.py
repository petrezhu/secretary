"""Memory store — JSONL-based persistent memory for Secretary.

Each memory entry is a JSON line in ~/.secretary/memory.jsonl.
Supports append, query by entity/tag/time, and status updates.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryType(Enum):
    """Type of memory entry."""
    DECISION = "decision"      # User made a decision
    INTENT = "intent"          # User expressed an intent (pending action)
    FACT = "fact"              # A fact about the user or their systems
    PREFERENCE = "preference"  # User preference


class MemoryStatus(Enum):
    """Status of memory entry."""
    ACTIVE = "active"          # Still relevant
    RESOLVED = "resolved"      # Fulfilled/completed
    EXPIRED = "expired"        # No longer relevant


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: str
    timestamp: str
    type: str
    content: str
    entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "qqbot"
    status: str = "active"
    resolved_at: str | None = None
    notes: str = ""


# Default memory file path
MEMORY_FILE = Path.home() / ".secretary" / "memory.jsonl"

# Max entries before rotation
MAX_ENTRIES = 1000


class MemoryStore:
    """Persistent memory store backed by JSONL file."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else MEMORY_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: MemoryEntry) -> None:
        """Append a memory entry to the store."""
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
            logger.debug("Memory appended: %s [%s]", entry.type, entry.content[:50])
        except Exception as e:
            logger.warning("Failed to append memory: %s", e)

    def query(
        self,
        entity: str | None = None,
        tag: str | None = None,
        memory_type: str | None = None,
        status: str = "active",
        limit: int = 10,
        days: int | None = None,
    ) -> list[MemoryEntry]:
        """Query memories by filters.

        Args:
            entity: Filter by entity (substring match)
            tag: Filter by tag (exact match)
            memory_type: Filter by type (exact match)
            status: Filter by status (default: active)
            limit: Max results
            days: Only include entries from last N days

        Returns:
            List of matching MemoryEntry objects
        """
        results = []
        cutoff = None
        if days:
            cutoff = time.time() - (days * 86400)

        try:
            if not self.path.exists():
                return []

            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Status filter
                    if status and data.get("status") != status:
                        continue

                    # Type filter
                    if memory_type and data.get("type") != memory_type:
                        continue

                    # Time filter
                    if cutoff:
                        ts = data.get("timestamp", "")
                        try:
                            entry_time = datetime.fromisoformat(ts).timestamp()
                            if entry_time < cutoff:
                                continue
                        except (ValueError, TypeError):
                            continue

                    # Entity filter (substring match)
                    if entity:
                        entities = data.get("entities", [])
                        content = data.get("content", "")
                        if not any(entity.lower() in e.lower() for e in entities) and \
                           entity.lower() not in content.lower():
                            continue

                    # Tag filter (exact match)
                    if tag:
                        tags = data.get("tags", [])
                        if tag not in tags:
                            continue

                    results.append(MemoryEntry(**{
                        k: v for k, v in data.items()
                        if k in MemoryEntry.__dataclass_fields__
                    }))

                    if len(results) >= limit:
                        break

        except Exception as e:
            logger.warning("Failed to query memories: %s", e)

        return results

    def get_recent(self, limit: int = 5) -> list[MemoryEntry]:
        """Get most recent memories (any status)."""
        results = []
        try:
            if not self.path.exists():
                return []

            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Read from end (most recent)
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    results.append(MemoryEntry(**{
                        k: v for k, v in data.items()
                        if k in MemoryEntry.__dataclass_fields__
                    }))
                    if len(results) >= limit:
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

        except Exception as e:
            logger.warning("Failed to get recent memories: %s", e)

        return results

    def resolve(self, memory_id: str) -> bool:
        """Mark a memory as resolved."""
        return self._update_status(memory_id, "resolved")

    def expire(self, memory_id: str) -> bool:
        """Mark a memory as expired."""
        return self._update_status(memory_id, "expired")

    def _update_status(self, memory_id: str, new_status: str) -> bool:
        """Update status of a memory entry by ID."""
        if not self.path.exists():
            return False

        lines = []
        updated = False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            new_lines = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("id") == memory_id:
                        data["status"] = new_status
                        if new_status == "resolved":
                            data["resolved_at"] = datetime.now().isoformat()
                        updated = True
                    new_lines.append(json.dumps(data, ensure_ascii=False))
                except json.JSONDecodeError:
                    new_lines.append(line)

            if updated:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write("\n".join(new_lines) + "\n")

        except Exception as e:
            logger.warning("Failed to update memory status: %s", e)

        return updated

    def count(self, status: str = "active") -> int:
        """Count memories with given status."""
        if not self.path.exists():
            return 0
        count = 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("status") == status:
                            count += 1
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return count

    def rotate(self, max_entries: int = MAX_ENTRIES) -> int:
        """Rotate old entries if exceeding max_entries.

        Keeps the most recent max_entries active entries.
        Resolved/expired entries older than 30 days are removed.

        Returns number of entries removed.
        """
        if not self.path.exists():
            return 0

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            entries = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(data)
                except json.JSONDecodeError:
                    continue

            # Remove old resolved/expired (30+ days)
            cutoff = time.time() - (30 * 86400)
            cleaned = []
            removed = 0
            for e in entries:
                status = e.get("status", "active")
                ts = e.get("timestamp", "")
                try:
                    entry_time = datetime.fromisoformat(ts).timestamp()
                except (ValueError, TypeError):
                    entry_time = 0

                if status in ("resolved", "expired") and entry_time < cutoff:
                    removed += 1
                    continue
                cleaned.append(e)

            # If still too many, keep most recent
            if len(cleaned) > max_entries:
                # Sort by timestamp descending
                cleaned.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                removed += len(cleaned) - max_entries
                cleaned = cleaned[:max_entries]

            # Write back
            with open(self.path, "w", encoding="utf-8") as f:
                for entry in cleaned:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            if removed > 0:
                logger.info("Memory rotated: removed %d entries", removed)

            return removed

        except Exception as e:
            logger.warning("Failed to rotate memories: %s", e)
            return 0


def create_memory(
    content: str,
    memory_type: str = "fact",
    entities: list[str] | None = None,
    tags: list[str] | None = None,
    source: str = "qqbot",
    notes: str = "",
) -> MemoryEntry:
    """Create a new memory entry."""
    return MemoryEntry(
        id=str(uuid.uuid4())[:8],
        timestamp=datetime.now().isoformat(),
        type=memory_type,
        content=content,
        entities=entities or [],
        tags=tags or [],
        source=source,
        status="active",
        notes=notes,
    )
