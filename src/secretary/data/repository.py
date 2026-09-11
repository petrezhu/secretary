"""Unified data repository with SQLite connection pooling."""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from secretary.config import DataConfig

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Goal:
    """Unified goal model covering weekly, monthly, and longterm goals."""

    id: int
    title: str
    status: str  # pending/in_progress/completed/paused
    goal_type: str  # weekly/monthly/longterm
    created_at: str
    priority: str | int = ""  # P0/P1/P2/P3 for weekly/monthly; int for longterm
    description: str | None = None
    progress: int = 0
    deadline: str | None = None  # target_date
    area: str | None = None  # category (longterm)
    owner: str | None = None  # weekly only
    parent_goal_id: int | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    year: int | None = None
    week: int | None = None
    month: int | None = None


@dataclass
class Task:
    """Task model matching tasks.db schema."""

    id: int
    request_text: str
    status: str  # pending/in_progress/completed/cancelled
    priority: int = 5
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    last_updated: str | None = None
    notes: str | None = None
    session_id: str | None = None
    channel_id: str | None = None
    goal_id: str | None = None


@dataclass
class SystemHealth:
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    services: dict[str, bool] = field(default_factory=dict)
    uptime_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Connection pool (simple single-connection per DB, WAL-optimised)
# ---------------------------------------------------------------------------


class _Pool:
    """Managed SQLite connection with WAL mode."""

    def __init__(self, db_path: str, *, create: bool = False):
        self._path = db_path
        self._conn: sqlite3.Connection | None = None
        self._create = create

    @property
    def path(self) -> str:
        return self._path

    def acquire(self) -> sqlite3.Connection:
        if self._conn is None:
            p = Path(self._path)
            if not self._create and not p.exists():
                raise FileNotFoundError(f"Database not found: {self._path}")
            p.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            with suppress(Exception):
                self._conn.close()
            self._conn = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None


# ---------------------------------------------------------------------------
# Row → model converters
# ---------------------------------------------------------------------------


def _row_to_goal(row: sqlite3.Row, goal_type: str) -> Goal:
    """Convert a sqlite3.Row to a Goal dataclass."""
    d = dict(row)
    return Goal(
        id=d["id"],
        title=d.get("title", ""),
        status=d.get("status", "pending"),
        goal_type=goal_type,
        created_at=d.get("created_at", ""),
        priority=d.get("priority", ""),
        description=d.get("description"),
        progress=int(d.get("progress", 0) or 0),
        deadline=d.get("target_date") or d.get("deadline"),
        area=d.get("area") or d.get("category"),
        owner=d.get("owner"),
        parent_goal_id=d.get("parent_goal_id"),
        updated_at=d.get("updated_at"),
        completed_at=d.get("completed_at"),
        year=d.get("year"),
        week=d.get("week"),
        month=d.get("month"),
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    """Convert a sqlite3.Row to a Task dataclass."""
    d = dict(row)
    return Task(
        id=d["id"],
        request_text=d.get("request_text", ""),
        status=d.get("status", "pending"),
        priority=int(d.get("priority", 5) or 5),
        created_at=d.get("created_at", ""),
        started_at=d.get("started_at"),
        completed_at=d.get("completed_at"),
        last_updated=d.get("last_updated"),
        notes=d.get("notes"),
        session_id=d.get("session_id"),
        channel_id=d.get("channel_id"),
        goal_id=d.get("goal_id"),
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class Repository:
    """Unified data repository with connection pooling (WAL mode).

    Connects to goals.db and tasks.db.  All public query methods return
    structured dataclass instances; SQL is fully parameterised.
    """

    def __init__(self, config: DataConfig, *, create: bool = False):
        self.config = config
        self._goals_pool = _Pool(config.goals_db, create=create)
        self._tasks_pool = _Pool(config.tasks_db, create=create)

    # -- lifecycle ----------------------------------------------------------

    async def initialize(self) -> None:
        """Open database connections (WAL mode enabled)."""
        self._goals_pool.acquire()
        self._tasks_pool.acquire()

    async def close(self) -> None:
        """Close all pooled connections."""
        self._goals_pool.close()
        self._tasks_pool.close()

    @property
    def is_initialized(self) -> bool:
        return self._goals_pool.is_open and self._tasks_pool.is_open

    # -- context managers ---------------------------------------------------

    @asynccontextmanager
    async def goals_db(self):
        """Yield the goals.db connection (raises if not initialised)."""
        if not self._goals_pool.is_open:
            raise RuntimeError("Repository not initialized")
        yield self._goals_pool.acquire()

    @asynccontextmanager
    async def tasks_db(self):
        """Yield the tasks.db connection (raises if not initialised)."""
        if not self._tasks_pool.is_open:
            raise RuntimeError("Repository not initialized")
        yield self._tasks_pool.acquire()

    # -- queries: goals -----------------------------------------------------

    async def get_weekly_goals(self, week_start: str | None = None) -> list[Goal]:
        """Return active weekly goals (not soft-deleted)."""
        async with self.goals_db() as conn:
            if week_start is not None:
                # week_start expected as "YYYY-MM-DD"
                cursor = conn.execute(
                    "SELECT * FROM weekly_goals "
                    "WHERE deleted = 0 AND created_at >= ? "
                    "ORDER BY priority",
                    (week_start,),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM weekly_goals WHERE deleted = 0 ORDER BY priority"
                )
            return [_row_to_goal(row, "weekly") for row in cursor.fetchall()]

    async def get_monthly_goals(self) -> list[Goal]:
        """Return active monthly goals."""
        async with self.goals_db() as conn:
            cursor = conn.execute(
                "SELECT * FROM monthly_goals WHERE status != 'completed' ORDER BY priority"
            )
            return [_row_to_goal(row, "monthly") for row in cursor.fetchall()]

    async def get_longterm_goals(self) -> list[Goal]:
        """Return active long-term goals."""
        async with self.goals_db() as conn:
            cursor = conn.execute(
                "SELECT * FROM longterm_goals WHERE status NOT IN ('completed', 'paused') "
                "ORDER BY priority"
            )
            return [_row_to_goal(row, "longterm") for row in cursor.fetchall()]

    # -- queries: tasks -----------------------------------------------------

    async def get_active_tasks(self) -> list[Task]:
        """Return pending and in-progress tasks from tasks.db."""
        async with self.tasks_db() as conn:
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('pending', 'in_progress') "
                "ORDER BY priority ASC"
            )
            return [_row_to_task(row) for row in cursor.fetchall()]

    async def get_recent_completions(self, days: int = 1) -> list[Task]:
        """Return tasks completed in the last N days."""
        async with self.tasks_db() as conn:
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE status = 'completed' "
                "AND completed_at IS NOT NULL "
                "AND completed_at >= datetime('now', ? || ' days') "
                "ORDER BY completed_at DESC",
                (f"-{days}",),
            )
            return [_row_to_task(row) for row in cursor.fetchall()]

    # -- queries: cross-cutting ---------------------------------------------

    async def get_stuck_goals(self, days: int = 14) -> list[Goal]:
        """Longterm goals stuck in_progress for > *days* without update."""
        async with self.goals_db() as conn:
            cursor = conn.execute(
                "SELECT * FROM longterm_goals "
                "WHERE status = 'in_progress' "
                "AND updated_at IS NOT NULL "
                "AND updated_at < datetime('now', ? || ' days')",
                (f"-{days}",),
            )
            return [_row_to_goal(row, "longterm") for row in cursor.fetchall()]

    async def get_zombie_tasks(self, days: int = 7) -> list[Task]:
        """In-progress tasks with no recent heartbeat (last_updated)."""
        async with self.tasks_db() as conn:
            cursor = conn.execute(
                "SELECT * FROM tasks "
                "WHERE status = 'in_progress' "
                "AND (last_updated IS NULL "
                "OR last_updated < datetime('now', ? || ' days'))",
                (f"-{days}",),
            )
            return [_row_to_task(row) for row in cursor.fetchall()]

    async def get_inbox_items(self, limit: int = 5) -> list[dict[str, Any]]:
        """Unprocessed inbox entries from goals.db."""
        async with self.goals_db() as conn:
            cursor = conn.execute(
                "SELECT id, source, content, created_at FROM inbox "
                "WHERE processed = 0 ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    async def complete_task(self, task_id: int) -> bool:
        """Mark a pending/in-progress task completed. True if a row changed."""
        async with self.tasks_db() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status='completed', completed_at=datetime('now'), "
                "last_updated=datetime('now') "
                "WHERE id=? AND status IN ('pending','in_progress')",
                (task_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    async def get_db_size(self) -> dict[str, float]:
        """Database file sizes in MB."""
        sizes: dict[str, float] = {}
        for name, path in [
            ("goals", self.config.goals_db),
            ("tasks", self.config.tasks_db),
        ]:
            p = Path(path)
            if p.exists():
                sizes[name] = round(p.stat().st_size / (1024 * 1024), 3)
        return sizes
