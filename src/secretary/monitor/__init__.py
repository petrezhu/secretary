"""Monitor layer — anomaly detection and health checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol


@dataclass
class CheckResult:
    name: str
    status: Literal["ok", "warning", "critical"]
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


class Checker(Protocol):
    """Protocol for all checkers."""

    name: str

    async def check(self, repo: Any) -> CheckResult: ...


class CheckerRegistry:
    """Registry for all checkers."""

    def __init__(self):
        self._checkers: list[Checker] = []

    def register(self, checker: Checker) -> None:
        self._checkers.append(checker)

    async def run_all(self, repo: Any) -> list[CheckResult]:
        """Run all registered checkers."""
        results = []
        for checker in self._checkers:
            try:
                result = await checker.check(repo)
                results.append(result)
            except Exception as e:
                results.append(
                    CheckResult(
                        name=checker.name,
                        status="critical",
                        message=f"Checker failed: {e}",
                    )
                )
        return results
