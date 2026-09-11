"""Unit tests for Repository with temporary SQLite databases."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from secretary.config import DataConfig
from secretary.data.repository import Goal, Repository, Task

# ---------------------------------------------------------------------------
# Helpers: create temp databases with real schema
# ---------------------------------------------------------------------------


def _create_goals_db(path: str) -> None:
    """Create a goals.db with the real schema."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS longterm_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'in_progress',
            priority INTEGER DEFAULT 5,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            target_date DATE,
            category TEXT,
            notes TEXT,
            ticktick_task_id TEXT,
            progress INTEGER DEFAULT 0,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS weekly_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            week INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'pending',
            priority TEXT,
            owner TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            parent_goal_id INTEGER,
            ticktick_task_id TEXT,
            ir_project_id INTEGER,
            deleted INTEGER DEFAULT 0,
            ir_action_id INTEGER,
            FOREIGN KEY (parent_goal_id) REFERENCES longterm_goals(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS monthly_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'in_progress',
            progress INTEGER DEFAULT 0,
            target_date DATE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            parent_goal_id INTEGER,
            ticktick_task_id TEXT,
            priority TEXT DEFAULT 'P1',
            FOREIGN KEY (parent_goal_id) REFERENCES longterm_goals(id) ON DELETE SET NULL
        );
        """
    )
    conn.close()


def _create_tasks_db(path: str) -> None:
    """Create a tasks.db with the real schema."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_text TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 5,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            completed_at DATETIME,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            session_id TEXT,
            channel_id TEXT,
            goal_id TEXT
        );
        """
    )
    conn.close()


def _seed_goals_db(
    path: str, *, include_stuck: bool = False, include_zombie_weekly: bool = False
) -> None:
    """Insert sample data into goals.db."""
    conn = sqlite3.connect(path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    # longterm goals
    conn.execute(
        "INSERT INTO longterm_goals (title, status, priority, progress, category, updated_at) "
        "VALUES (?, 'in_progress', 1, 20, '项目', ?)",
        ("完成Secretary项目", now),
    )
    conn.execute(
        "INSERT INTO longterm_goals (title, status, priority, progress, category, updated_at) "
        "VALUES (?, 'completed', 2, 100, '技能', ?)",
        ("学完Rust基础", now),
    )
    if include_stuck:
        conn.execute(
            "INSERT INTO longterm_goals (title, status, priority, progress, category, updated_at) "
            "VALUES (?, 'in_progress', 3, 10, '技能', ?)",
            ("长期停滞目标", old),
        )

    # weekly goals
    conn.execute(
        "INSERT INTO weekly_goals (year, week, title, status, priority, owner, deleted) "
        "VALUES (2026, 35, ?, 'in_progress', 'P0', 'bot', 0)",
        ("实现数据层",),
    )
    conn.execute(
        "INSERT INTO weekly_goals (year, week, title, status, priority, owner, deleted) "
        "VALUES (2026, 35, ?, 'pending', 'P1', 'user', 0)",
        ("写文档",),
    )
    conn.execute(
        "INSERT INTO weekly_goals (year, week, title, status, priority, owner, deleted) "
        "VALUES (2026, 35, ?, 'pending', 'P2', 'bot', 1)",
        ("已删除目标",),
    )

    # monthly goals
    conn.execute(
        "INSERT INTO monthly_goals (year, month, title, status, priority) "
        "VALUES (2026, 8, ?, 'in_progress', 'P0')",
        ("完成Secretary M0",),
    )
    conn.execute(
        "INSERT INTO monthly_goals (year, month, title, status, priority) "
        "VALUES (2026, 8, ?, 'completed', 'P1')",
        ("已完成月目标",),
    )

    conn.commit()
    conn.close()


def _seed_tasks_db(path: str, *, include_zombie: bool = False) -> None:
    """Insert sample data into tasks.db."""
    conn = sqlite3.connect(path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        "INSERT INTO tasks (request_text, status, priority, last_updated) "
        "VALUES (?, 'pending', 1, ?)",
        ("实现数据层", now),
    )
    conn.execute(
        "INSERT INTO tasks (request_text, status, priority, last_updated) "
        "VALUES (?, 'in_progress', 2, ?)",
        ("写单元测试", now),
    )
    conn.execute(
        "INSERT INTO tasks (request_text, status, priority, last_updated) "
        "VALUES (?, 'completed', 3, ?)",
        ("已完成任务", now),
    )
    if include_zombie:
        conn.execute(
            "INSERT INTO tasks (request_text, status, priority, last_updated) "
            "VALUES (?, 'in_progress', 4, ?)",
            ("僵尸任务", old),
        )
        conn.execute(
            "INSERT INTO tasks (request_text, status, priority, last_updated) "
            "VALUES (?, 'in_progress', 5, NULL)",
            ("从未更新的任务",),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_goals_db(tmp_path: Path) -> str:
    """Path to a temporary goals.db with schema + seed data."""
    p = str(tmp_path / "goals.db")
    _create_goals_db(p)
    return p


@pytest.fixture
def tmp_tasks_db(tmp_path: Path) -> str:
    """Path to a temporary tasks.db with schema + seed data."""
    p = str(tmp_path / "tasks.db")
    _create_tasks_db(p)
    return p


@pytest.fixture
async def repo(tmp_goals_db: str, tmp_tasks_db: str) -> Repository:
    """Initialised Repository against temp databases."""
    config = DataConfig(goals_db=tmp_goals_db, tasks_db=tmp_tasks_db)
    r = Repository(config)
    await r.initialize()
    yield r
    await r.close()


@pytest.fixture
async def seeded_repo(tmp_path: Path) -> Repository:
    """Repository with pre-seeded data."""
    goals_db = str(tmp_path / "goals.db")
    tasks_db = str(tmp_path / "tasks.db")
    _create_goals_db(goals_db)
    _create_tasks_db(tasks_db)
    _seed_goals_db(goals_db, include_stuck=True)
    _seed_tasks_db(tasks_db, include_zombie=True)

    config = DataConfig(goals_db=goals_db, tasks_db=tasks_db)
    r = Repository(config)
    await r.initialize()
    yield r
    await r.close()


# ---------------------------------------------------------------------------
# Tests: lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_initialize_and_close(self, tmp_goals_db: str, tmp_tasks_db: str):
        config = DataConfig(goals_db=tmp_goals_db, tasks_db=tmp_tasks_db)
        r = Repository(config)
        assert not r.is_initialized
        await r.initialize()
        assert r.is_initialized
        await r.close()
        assert not r.is_initialized

    async def test_initialize_missing_db_raises(self, tmp_path: Path):
        config = DataConfig(
            goals_db=str(tmp_path / "nope.db"),
            tasks_db=str(tmp_path / "nope2.db"),
        )
        r = Repository(config)
        with pytest.raises(FileNotFoundError):
            await r.initialize()

    async def test_create_mode(self, tmp_path: Path):
        """create=True should create missing DB files."""
        goals_db = str(tmp_path / "new_goals.db")
        tasks_db = str(tmp_path / "new_tasks.db")
        config = DataConfig(goals_db=goals_db, tasks_db=tasks_db)
        r = Repository(config, create=True)
        await r.initialize()
        assert r.is_initialized
        await r.close()
        assert Path(goals_db).exists()
        assert Path(tasks_db).exists()

    async def test_goals_db_context_not_initialized(self, tmp_path: Path):
        config = DataConfig(goals_db=str(tmp_path / "x.db"), tasks_db=str(tmp_path / "y.db"))
        r = Repository(config)
        with pytest.raises(RuntimeError, match="not initialized"):
            async with r.goals_db():
                pass

    async def test_tasks_db_context_not_initialized(self, tmp_path: Path):
        config = DataConfig(goals_db=str(tmp_path / "x.db"), tasks_db=str(tmp_path / "y.db"))
        r = Repository(config)
        with pytest.raises(RuntimeError, match="not initialized"):
            async with r.tasks_db():
                pass

    async def test_wal_mode_enabled(self, repo: Repository):
        async with repo.goals_db() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

        async with repo.tasks_db() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

    async def test_double_close_is_safe(self, repo: Repository):
        await repo.close()
        await repo.close()  # should not raise
        assert not repo.is_initialized


# ---------------------------------------------------------------------------
# Tests: data models
# ---------------------------------------------------------------------------


class TestModels:
    def test_goal_defaults(self):
        g = Goal(id=1, title="test", status="pending", goal_type="weekly", created_at="")
        assert g.progress == 0
        assert g.parent_goal_id is None
        assert g.area is None

    def test_task_defaults(self):
        t = Task(id=1, request_text="test", status="pending")
        assert t.priority == 5
        assert t.started_at is None
        assert t.goal_id is None

    def test_system_health_defaults(self):
        from secretary.data.repository import SystemHealth

        h = SystemHealth(cpu_percent=10.0, memory_percent=20.0, disk_percent=30.0)
        assert h.services == {}
        assert h.uptime_seconds == 0.0


# ---------------------------------------------------------------------------
# Tests: query methods
# ---------------------------------------------------------------------------


class TestWeeklyGoals:
    async def test_get_weekly_goals_excludes_deleted(self, seeded_repo: Repository):
        goals = await seeded_repo.get_weekly_goals()
        titles = [g.title for g in goals]
        assert "已删除目标" not in titles
        assert len(goals) == 2
        for g in goals:
            assert g.goal_type == "weekly"

    async def test_get_weekly_goals_returns_goal_objects(self, seeded_repo: Repository):
        goals = await seeded_repo.get_weekly_goals()
        assert all(isinstance(g, Goal) for g in goals)
        g = goals[0]
        assert g.id > 0
        assert g.title
        assert g.year == 2026
        assert g.week == 35

    async def test_get_weekly_goals_with_week_start(self, seeded_repo: Repository):
        # All seeded goals created_at is current timestamp, so none before a future date
        goals = await seeded_repo.get_weekly_goals(week_start="2099-01-01")
        assert len(goals) == 0

        # With an old date, all should show
        goals = await seeded_repo.get_weekly_goals(week_start="2020-01-01")
        assert len(goals) == 2

    async def test_get_weekly_goals_empty_db(self, repo: Repository):
        goals = await repo.get_weekly_goals()
        assert goals == []


class TestMonthlyGoals:
    async def test_get_monthly_goals_excludes_completed(self, seeded_repo: Repository):
        goals = await seeded_repo.get_monthly_goals()
        titles = [g.title for g in goals]
        assert "已完成月目标" not in titles
        assert len(goals) == 1
        assert goals[0].goal_type == "monthly"

    async def test_get_monthly_goals_empty_db(self, repo: Repository):
        goals = await repo.get_monthly_goals()
        assert goals == []


class TestLongtermGoals:
    async def test_get_longterm_goals_excludes_completed_and_paused(self, seeded_repo: Repository):
        goals = await seeded_repo.get_longterm_goals()
        titles = [g.title for g in goals]
        assert "学完Rust基础" not in titles  # completed
        assert len(goals) >= 1
        for g in goals:
            assert g.goal_type == "longterm"
            assert g.status not in ("completed", "paused")

    async def test_get_longterm_goals_empty_db(self, repo: Repository):
        goals = await repo.get_longterm_goals()
        assert goals == []


class TestActiveTasks:
    async def test_get_active_tasks(self, seeded_repo: Repository):
        tasks = await seeded_repo.get_active_tasks()
        statuses = {t.status for t in tasks}
        assert statuses <= {"pending", "in_progress"}
        assert len(tasks) >= 2  # pending + in_progress at minimum
        for t in tasks:
            assert isinstance(t, Task)

    async def test_get_active_tasks_excludes_completed(self, seeded_repo: Repository):
        tasks = await seeded_repo.get_active_tasks()
        titles = [t.request_text for t in tasks]
        assert "已完成任务" not in titles

    async def test_get_active_tasks_ordered_by_priority(self, seeded_repo: Repository):
        tasks = await seeded_repo.get_active_tasks()
        priorities = [t.priority for t in tasks]
        assert priorities == sorted(priorities)

    async def test_get_active_tasks_empty_db(self, repo: Repository):
        tasks = await repo.get_active_tasks()
        assert tasks == []


class TestStuckGoals:
    async def test_get_stuck_goals_finds_old_in_progress(self, seeded_repo: Repository):
        stuck = await seeded_repo.get_stuck_goals(days=14)
        titles = [g.title for g in stuck]
        assert "长期停滞目标" in titles
        assert all(g.status == "in_progress" for g in stuck)

    async def test_get_stuck_goals_excludes_fresh(self, seeded_repo: Repository):
        stuck = await seeded_repo.get_stuck_goals(days=14)
        titles = [g.title for g in stuck]
        assert "完成Secretary项目" not in titles  # recently updated

    async def test_get_stuck_goals_custom_days(self, seeded_repo: Repository):
        # The seeded "stuck" goal is 30 days old — days=20 should find it
        stuck = await seeded_repo.get_stuck_goals(days=20)
        assert len(stuck) >= 1

        # With days=60, the 30-day-old goal doesn't qualify
        stuck_strict = await seeded_repo.get_stuck_goals(days=60)
        titles = [g.title for g in stuck_strict]
        assert "长期停滞目标" not in titles

    async def test_get_stuck_goals_no_stuck(self, repo: Repository):
        stuck = await repo.get_stuck_goals()
        assert stuck == []


class TestZombieTasks:
    async def test_get_zombie_tasks_finds_stale(self, seeded_repo: Repository):
        zombies = await seeded_repo.get_zombie_tasks(days=7)
        titles = [t.request_text for t in zombies]
        assert "僵尸任务" in titles
        assert "从未更新的任务" in titles

    async def test_get_zombie_tasks_excludes_fresh(self, seeded_repo: Repository):
        zombies = await seeded_repo.get_zombie_tasks(days=7)
        titles = [t.request_text for t in zombies]
        assert "写单元测试" not in titles  # recently updated

    async def test_get_zombie_tasks_empty_db(self, repo: Repository):
        zombies = await repo.get_zombie_tasks()
        assert zombies == []


class TestDbSize:
    async def test_get_db_size(self, seeded_repo: Repository):
        sizes = await seeded_repo.get_db_size()
        assert "goals" in sizes
        assert "tasks" in sizes
        assert sizes["goals"] > 0
        assert sizes["tasks"] > 0

    async def test_get_db_size_empty_db(self, repo: Repository):
        sizes = await repo.get_db_size()
        assert "goals" in sizes
        assert "tasks" in sizes
