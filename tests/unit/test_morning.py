"""Unit tests for morning briefing generation (mocked repo data)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from secretary.coach.morning import (
    BriefingContext,
    collect_briefing_data,
    generate_morning_briefing,
    render_morning_briefing,
)

# ── Fake data models ─────────────────────────────────────────────────────


@dataclass
class FakeGoal:
    id: int = 1
    title: str = "Test Goal"
    status: str = "in_progress"
    goal_type: str = "weekly"
    created_at: str = "2026-08-01"
    priority: str = "P0"
    description: str | None = None
    progress: int = 30
    deadline: str | None = "2026-09-15"
    area: str | None = None
    owner: str | None = None
    parent_goal_id: int | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    year: int | None = None
    week: int | None = None
    month: int | None = None


@dataclass
class FakeTask:
    id: int = 1
    request_text: str = "实现数据层"
    status: str = "pending"
    priority: int = 5
    created_at: str = "2026-08-01"
    started_at: str | None = None
    completed_at: str | None = None
    last_updated: str | None = None
    notes: str | None = None
    session_id: str | None = None
    channel_id: str | None = None
    goal_id: str | None = None


# ── Mock repo factory ────────────────────────────────────────────────────


def make_mock_repo(
    weekly_goals=None,
    active_tasks=None,
    stuck_goals=None,
):
    """Create a mock Repository with configurable return values."""
    repo = AsyncMock()
    repo.get_weekly_goals = AsyncMock(return_value=weekly_goals or [])
    repo.get_active_tasks = AsyncMock(return_value=active_tasks or [])
    repo.get_stuck_goals = AsyncMock(return_value=stuck_goals or [])
    return repo


# ── collect_briefing_data tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_data_basic():
    """Basic data collection with one goal and two tasks."""
    repo = make_mock_repo(
        weekly_goals=[FakeGoal(title="完成Secretary项目", priority="P0", progress=30)],
        active_tasks=[FakeTask(status="pending"), FakeTask(status="pending")],
        stuck_goals=[],
    )
    ctx = await collect_briefing_data(repo)

    assert ctx.focus_title == "完成Secretary项目"
    assert ctx.pending_task_count == 2
    assert len(ctx.stuck_goals) == 0
    assert "P0" in ctx.focus_reason
    assert ctx.focus_progress == 30
    assert 0 <= ctx.energy_score <= 100


@pytest.mark.asyncio
async def test_collect_data_empty():
    """Empty repo should produce default values."""
    repo = make_mock_repo()
    ctx = await collect_briefing_data(repo)

    assert ctx.focus_title == "自由安排"
    assert ctx.pending_task_count == 0
    assert ctx.stuck_goals == []


@pytest.mark.asyncio
async def test_collect_data_with_stuck_goals():
    """Stuck goals should appear in context."""
    stuck = [FakeGoal(title="卡住的长期目标", priority="P1")]
    repo = make_mock_repo(
        weekly_goals=[FakeGoal()],
        active_tasks=[FakeTask()],
        stuck_goals=stuck,
    )
    ctx = await collect_briefing_data(repo)
    assert len(ctx.stuck_goals) == 1


# ── render_morning_briefing tests ────────────────────────────────────────


def test_render_contains_greeting():
    """Output should contain the greeting line."""
    ctx = BriefingContext(
        greeting="今天状态很棒！",
        focus_title="完成Secretary",
        focus_reason="P0，截止 2026-09-15",
        focus_progress=30,
        pending_task_count=5,
    )
    text = render_morning_briefing(ctx)
    assert "早安" in text
    assert "今天状态很棒" in text


def test_render_contains_focus():
    """Output should show today's focus goal."""
    ctx = BriefingContext(
        greeting="状态不错。",
        focus_title="学习Rust",
        focus_reason="P2",
        focus_progress=10,
        pending_task_count=3,
    )
    text = render_morning_briefing(ctx)
    assert "学习Rust" in text
    assert "P2" in text


def test_render_contains_task_count():
    """Output should show pending task count."""
    ctx = BriefingContext(
        greeting="状态不错。",
        focus_title="Task",
        focus_reason="P1",
        focus_progress=0,
        pending_task_count=7,
    )
    text = render_morning_briefing(ctx)
    assert "7 个" in text


def test_render_shows_stuck_goals():
    """Stuck goals should be listed."""
    stuck = [FakeGoal(title="卡住目标A"), FakeGoal(title="卡住目标B")]
    ctx = BriefingContext(
        greeting="状态不错。",
        focus_title="Task",
        focus_reason="P1",
        focus_progress=0,
        pending_task_count=0,
        stuck_goals=stuck,
    )
    text = render_morning_briefing(ctx)
    assert "卡住的目标" in text
    assert "卡住目标A" in text
    assert "卡住目标B" in text


def test_render_shows_yesterday_completions():
    """Yesterday's completions should be listed."""
    completed = [FakeTask(request_text="完成了任务X")]
    ctx = BriefingContext(
        greeting="状态不错。",
        focus_title="Task",
        focus_reason="P1",
        focus_progress=0,
        pending_task_count=0,
        yesterday_completions=completed,
    )
    text = render_morning_briefing(ctx)
    assert "昨日完成" in text
    assert "任务X" in text


def test_render_high_energy_closing():
    """High energy should show encouraging closing."""
    ctx = BriefingContext(
        greeting="今天状态很棒！",
        focus_title="Task",
        focus_reason="P1",
        focus_progress=0,
        pending_task_count=0,
        energy_score=80.0,
    )
    text = render_morning_briefing(ctx)
    assert "加油" in text


def test_render_medium_energy_closing():
    """Medium energy should show steady closing."""
    ctx = BriefingContext(
        greeting="状态不错。",
        focus_title="Task",
        focus_reason="P1",
        focus_progress=0,
        pending_task_count=0,
        energy_score=55.0,
    )
    text = render_morning_briefing(ctx)
    assert "稳步推进" in text


def test_render_low_energy_closing():
    """Low energy should show gentle closing."""
    ctx = BriefingContext(
        greeting="今天优先照顾好自己。",
        focus_title="Task",
        focus_reason="P1",
        focus_progress=0,
        pending_task_count=0,
        energy_score=20.0,
    )
    text = render_morning_briefing(ctx)
    assert "量力而行" in text


def test_render_no_stuck_no_completed():
    """Sections with no data should not appear."""
    ctx = BriefingContext(
        greeting="状态不错。",
        focus_title="Task",
        focus_reason="P1",
        focus_progress=0,
        pending_task_count=0,
    )
    text = render_morning_briefing(ctx)
    assert "卡住的目标" not in text
    assert "昨日完成" not in text


# ── generate_morning_briefing (integration-style) ────────────────────────


@pytest.mark.asyncio
async def test_generate_morning_briefing_integration():
    """Full pipeline: repo → context → text."""
    repo = make_mock_repo(
        weekly_goals=[FakeGoal(title="写周报", priority="P0", progress=50)],
        active_tasks=[FakeTask(), FakeTask()],
    )
    text = await generate_morning_briefing(repo)

    assert "早安" in text
    assert "写周报" in text
    assert "P0" in text
    assert "2 个" in text
