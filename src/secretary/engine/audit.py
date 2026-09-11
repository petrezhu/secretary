"""Audit log — complete record of all automated operations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_PATH = Path(__file__).parent.parent.parent / "data" / "audit.jsonl"


@dataclass
class AuditEntry:
    """Single audit record."""

    id: int
    timestamp: datetime
    action: str
    target: str
    details: dict[str, Any]
    result: str  # success/failed/skipped
    reversible: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "target": self.target,
            "details": self.details,
            "result": self.result,
            "reversible": self.reversible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEntry:
        """Deserialize from a dict."""
        return cls(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            action=data["action"],
            target=data["target"],
            details=data["details"],
            result=data["result"],
            reversible=data.get("reversible", False),
        )


class AuditLog:
    """All automated operations are recorded here.

    Appends one JSON object per line (JSONL format).
    Supports querying by time range and action type.
    """

    def __init__(self, repo: Any = None, path: Path | str | None = None):
        self.repo = repo
        self._path = Path(path) if path else DEFAULT_AUDIT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._counter = self._next_id()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def log(
        self,
        action: str,
        target: str,
        details: dict[str, Any] | None = None,
        result: str = "success",
        reversible: bool = False,
    ) -> AuditEntry:
        """Append an operation to the audit log and return it."""
        entry = AuditEntry(
            id=self._counter,
            timestamp=datetime.now(),
            action=action,
            target=target,
            details=details or {},
            result=result,
            reversible=reversible,
        )
        self._counter += 1

        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        logger.info("Audit: %s → %s [%s]", action, target, result)
        return entry

    def query(
        self,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEntry]:
        """Read back entries, optionally filtered by action and/or time range."""
        entries = self._read_all()
        if action is not None:
            entries = [e for e in entries if e.action == action]
        if since is not None:
            entries = [e for e in entries if e.timestamp >= since]
        if until is not None:
            entries = [e for e in entries if e.timestamp <= until]
        return entries

    def read_all(self) -> list[AuditEntry]:
        """Return every entry in the log file."""
        return self._read_all()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        """Determine the next id from the maximum existing ID + 1."""
        if not self._path.exists():
            return 1
        max_id = 0
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    max_id = max(max_id, data.get("id", 0))
                except (json.JSONDecodeError, KeyError):
                    continue
        return max_id + 1

    def _read_all(self) -> list[AuditEntry]:
        """Parse the JSONL file into AuditEntry objects."""
        if not self._path.exists():
            return []
        entries: list[AuditEntry] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(AuditEntry.from_dict(data))
                except json.JSONDecodeError:
                    continue  # skip malformed lines
        return entries
