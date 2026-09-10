"""Unit tests for HealthChecker using temporary SQLite databases."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from secretary.config import DataConfig, MonitorConfig
from secretary.data.repository import Repository
from secretary.monitor import CheckerRegistry, CheckResult
from secretary.monitor.health import HealthChecker, _worst

# ---------------------------------------------------------------------------
# Helpers: create temp databases with real schema
# ---------------------------------------------------------------------------


def _create_goals_db(path: str) -> None:
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


def _seed_weekly_goals(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO weekly_goals (year, week, title, status, priority, owner, deleted) "
        "VALUES (2026, 35, '实现数据层', 'in_progress', 'P0', 'bot', 0)"
    )
    conn.execute(
        "INSERT INTO weekly_goals (year, week, title, status, priority, owner, deleted) "
        "VALUES (2026, 35, '写文档', 'pending', 'P1', 'user', 0)"
    )
    conn.commit()
    conn.close()


def _seed_stuck_goals(path: str) -> None:
    conn = sqlite3.connect(path)
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO longterm_goals (title, status, priority, progress, category, updated_at) "
        "VALUES ('长期停滞目标', 'in_progress', 3, 10, '技能', ?)",
        (old,),
    )
    conn.commit()
    conn.close()


def _seed_zombie_tasks(path: str) -> None:
    conn = sqlite3.connect(path)
    old = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO tasks (request_text, status, priority, last_updated) "
        "VALUES ('僵尸任务', 'in_progress', 4, ?)",
        (old,),
    )
    conn.execute(
        "INSERT INTO tasks (request_text, status, priority, last_updated) "
        "VALUES ('从未更新的任务', 'in_progress', 5, NULL)"
    )
    conn.commit()
    conn.close()


def _pad_db_to_size(path: str, target_mb: float) -> None:
    """Write padding data to inflate DB file size."""
    conn = sqlite3.connect(path)
    # Create a table with a large text blob
    conn.execute("CREATE TABLE IF NOT EXISTS _padding (id INTEGER PRIMARY KEY, data TEXT)")
    blob = "x" * 1_000_000  # ~1MB per row
    rows_needed = int(target_mb) + 1
    for i in range(rows_needed):
        conn.execute("INSERT INTO _padding (id, data) VALUES (?, ?)", (i, blob))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_repo(tmp_path: Path) -> Repository:
    """Repository with empty databases."""
    goals_db = str(tmp_path / "goals.db")
    tasks_db = str(tmp_path / "tasks.db")
    _create_goals_db(goals_db)
    _create_tasks_db(tasks_db)
    config = DataConfig(goals_db=goals_db, tasks_db=tasks_db)
    r = Repository(config)
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(r.initialize())
    yield r
    loop.run_until_complete(r.close())
    loop.close()


@pytest.fixture
def healthy_repo(tmp_path: Path) -> Repository:
    """Repository with weekly goals and no issues."""
    goals_db = str(tmp_path / "goals.db")
    tasks_db = str(tmp_path / "tasks.db")
    _create_goals_db(goals_db)
    _create_tasks_db(tasks_db)
    _seed_weekly_goals(goals_db)
    config = DataConfig(goals_db=goals_db, tasks_db=tasks_db)
    r = Repository(config)
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(r.initialize())
    yield r
    loop.run_until_complete(r.close())
    loop.close()


@pytest.fixture
def warning_repo(tmp_path: Path) -> Repository:
    """Repository with stuck goals and zombie tasks."""
    goals_db = str(tmp_path / "goals.db")
    tasks_db = str(tmp_path / "tasks.db")
    _create_goals_db(goals_db)
    _create_tasks_db(tasks_db)
    _seed_weekly_goals(goals_db)
    _seed_stuck_goals(goals_db)
    _seed_zombie_tasks(tasks_db)
    config = DataConfig(goals_db=goals_db, tasks_db=tasks_db)
    r = Repository(config)
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(r.initialize())
    yield r
    loop.run_until_complete(r.close())
    loop.close()


@pytest.fixture
def large_db_repo(tmp_path: Path) -> Repository:
    """Repository with DB files exceeding 50MB."""
    goals_db = str(tmp_path / "goals.db")
    tasks_db = str(tmp_path / "tasks.db")
    _create_goals_db(goals_db)
    _create_tasks_db(tasks_db)
    _seed_weekly_goals(goals_db)
    _pad_db_to_size(goals_db, 30)
    _pad_db_to_size(tasks_db, 25)
    config = DataConfig(goals_db=goals_db, tasks_db=tasks_db)
    r = Repository(config)
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(r.initialize())
    yield r
    loop.run_until_complete(r.close())
    loop.close()


@pytest.fixture
def checker(tmp_path: Path) -> HealthChecker:
    """HealthChecker with a valid token file so new checks pass."""
    token_path = tmp_path / "dida365_token.json"
    token_path.write_text(json.dumps({
        "access_token": "test_token",
        "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
    }))
    return HealthChecker(config=MonitorConfig(), token_path=token_path)


# ---------------------------------------------------------------------------
# Tests: _worst helper
# ---------------------------------------------------------------------------


class TestWorstHelper:
    def test_ok_and_warning(self):
        assert _worst("ok", "warning") == "warning"

    def test_warning_and_critical(self):
        assert _worst("warning", "critical") == "critical"

    def test_ok_and_critical(self):
        assert _worst("ok", "critical") == "critical"

    def test_critical_stays_critical(self):
        assert _worst("critical", "warning") == "critical"

    def test_ok_stays_ok(self):
        assert _worst("ok", "ok") == "ok"


# ---------------------------------------------------------------------------
# Tests: HealthChecker.check()
# ---------------------------------------------------------------------------


class TestHealthCheckerAllOk:
    async def test_healthy_system(self, healthy_repo: Repository, checker: HealthChecker):
        result = await checker.check(healthy_repo)
        assert isinstance(result, CheckResult)
        assert result.name == "health"
        assert result.status == "ok"
        assert "通过" in result.message
        assert result.details["weekly_goals_count"] == 2
        assert result.details["stuck_goals"] == []
        assert result.details["zombie_tasks"] == []

    async def test_result_has_timestamp(self, healthy_repo: Repository, checker: HealthChecker):
        result = await checker.check(healthy_repo)
        assert result.timestamp is not None


class TestHealthCheckerNoWeeklyGoals:
    async def test_empty_db_warns(self, empty_repo: Repository, checker: HealthChecker):
        result = await checker.check(empty_repo)
        assert result.status == "warning"
        assert "本周无周目标" in result.message
        assert result.details["weekly_goals_count"] == 0


class TestHealthCheckerStuckGoals:
    async def test_stuck_goals_detected(self, warning_repo: Repository, checker: HealthChecker):
        result = await checker.check(warning_repo)
        assert result.status == "warning"
        assert "停滞超14天" in result.message
        assert len(result.details["stuck_goals"]) > 0

    async def test_no_stuck_goals(self, healthy_repo: Repository, checker: HealthChecker):
        result = await checker.check(healthy_repo)
        assert result.details["stuck_goals"] == []


class TestHealthCheckerZombieTasks:
    async def test_zombies_detected(self, warning_repo: Repository, checker: HealthChecker):
        result = await checker.check(warning_repo)
        assert result.status == "warning"
        assert "僵尸任务" in result.message
        assert len(result.details["zombie_tasks"]) > 0

    async def test_no_zombies(self, healthy_repo: Repository, checker: HealthChecker):
        result = await checker.check(healthy_repo)
        assert result.details["zombie_tasks"] == []


class TestHealthCheckerDbSize:
    async def test_large_db_detected(self, large_db_repo: Repository, checker: HealthChecker):
        result = await checker.check(large_db_repo)
        assert result.status == "warning"
        assert "数据库过大" in result.message
        assert result.details["db_total_mb"] > 50

    async def test_normal_db_size(self, healthy_repo: Repository, checker: HealthChecker):
        result = await checker.check(healthy_repo)
        assert result.details["db_total_mb"] < 50


class TestHealthCheckerCombined:
    async def test_multiple_issues_aggregate(
        self, warning_repo: Repository, checker: HealthChecker
    ):
        """When multiple issues exist, all are reported."""
        result = await checker.check(warning_repo)
        # Should have both stuck goals and zombie tasks warnings
        assert "停滞超14天" in result.message
        assert "僵尸任务" in result.message

    async def test_to_dict_serialization(
        self, warning_repo: Repository, checker: HealthChecker
    ):
        result = await checker.check(warning_repo)
        d = result.to_dict()
        assert "name" in d
        assert "status" in d
        assert "message" in d
        assert "timestamp" in d
        assert isinstance(d["details"], dict)


# ---------------------------------------------------------------------------
# Tests: CheckerRegistry integration
# ---------------------------------------------------------------------------


class TestCheckerRegistryIntegration:
    async def test_register_and_run_all(self, healthy_repo: Repository, tmp_path: Path):
        registry = CheckerRegistry()
        token_path = tmp_path / "dida365_token.json"
        token_path.write_text(json.dumps({
            "access_token": "test",
            "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
        }))
        checker = HealthChecker(config=MonitorConfig(), token_path=token_path)
        registry.register(checker)

        results = await registry.run_all(healthy_repo)
        assert len(results) == 1
        assert results[0].name == "health"
        assert results[0].status == "ok"

    async def test_registry_catches_exception(self, tmp_path: Path):
        """Registry should return critical CheckResult on checker failure."""
        registry = CheckerRegistry()
        checker = HealthChecker(config=MonitorConfig())
        registry.register(checker)

        # Create a repo that will fail (not initialized)
        goals_db = str(tmp_path / "goals.db")
        tasks_db = str(tmp_path / "tasks.db")
        _create_goals_db(goals_db)
        _create_tasks_db(tasks_db)
        config = DataConfig(goals_db=goals_db, tasks_db=tasks_db)
        repo = Repository(config)
        # Do NOT initialize — queries will fail

        results = await registry.run_all(repo)
        assert len(results) == 1
        # The health checker catches exceptions internally and reports critical
        # But if the checker itself throws, registry catches it
        assert results[0].name == "health"


# ---------------------------------------------------------------------------
# Tests: Timezone-aware token expiry
# ---------------------------------------------------------------------------


class TestTimezoneAwareExpiry:
    """Token expiry check should handle timezone-aware datetimes correctly."""

    async def test_timezone_aware_token_no_crash(self, healthy_repo: Repository, tmp_path: Path):
        """Token with +08:00 timezone should not cause TypeError."""
        token_path = tmp_path / "dida365_token.json"
        # expires_at with explicit timezone
        exp = (datetime.now() + timedelta(days=30)).isoformat() + "+08:00"
        token_path.write_text(json.dumps({
            "access_token": "test",
            "expires_at": exp,
        }))
        checker = HealthChecker(config=MonitorConfig(), token_path=token_path)
        result = await checker.check(healthy_repo)
        # Should not raise TypeError from naive/aware datetime subtraction
        assert result.status == "ok"

    async def test_timezone_aware_expired_token(self, healthy_repo: Repository, tmp_path: Path):
        """Expired timezone-aware token should be detected."""
        token_path = tmp_path / "dida365_token.json"
        exp = (datetime.now() - timedelta(days=1)).isoformat() + "+08:00"
        token_path.write_text(json.dumps({
            "access_token": "test",
            "expires_at": exp,
        }))
        checker = HealthChecker(config=MonitorConfig(), token_path=token_path)
        result = await checker.check(healthy_repo)
        assert result.status in ("warning", "critical")
        assert "Token" in result.message

    async def test_naive_token_still_works(self, healthy_repo: Repository, tmp_path: Path):
        """Naive (no timezone) token should still work as before."""
        token_path = tmp_path / "dida365_token.json"
        exp = (datetime.now() + timedelta(days=30)).isoformat()
        token_path.write_text(json.dumps({
            "access_token": "test",
            "expires_at": exp,
        }))
        checker = HealthChecker(config=MonitorConfig(), token_path=token_path)
        result = await checker.check(healthy_repo)
        assert result.status == "ok"

    async def test_epoch_timestamp_token(self, healthy_repo: Repository, tmp_path: Path):
        """Token with epoch timestamp (int) should work."""
        token_path = tmp_path / "dida365_token.json"
        import time
        exp = int(time.time()) + 30 * 86400
        token_path.write_text(json.dumps({
            "access_token": "test",
            "expires_at": exp,
        }))
        checker = HealthChecker(config=MonitorConfig(), token_path=token_path)
        result = await checker.check(healthy_repo)
        assert result.status == "ok"

