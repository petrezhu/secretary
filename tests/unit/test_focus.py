"""Unit tests for focus selection logic."""

from __future__ import annotations

from dataclasses import dataclass

from secretary.coach.focus import FocusResult, select_today_focus

# ── Helpers ───────────────────────────────────────────────────────────────


@dataclass
class FakeGoal:
    """Minimal goal stand-in for testing."""

    id: int
    title: str
    priority: str = ""
    deadline: str | None = None
    progress: int = 0
    status: str = "in_progress"


# ── Empty input ──────────────────────────────────────────────────────────


def test_empty_goals():
    result = select_today_focus([])
    assert result.goal is None
    assert "自由安排" in result.reason


# ── Priority ordering ────────────────────────────────────────────────────


def test_p0_beats_p1():
    goals = [
        FakeGoal(id=1, title="P1 task", priority="P1"),
        FakeGoal(id=2, title="P0 task", priority="P0"),
    ]
    result = select_today_focus(goals)
    assert result.goal.title == "P0 task"


def test_p0_beats_p2():
    goals = [
        FakeGoal(id=1, title="P2 task", priority="P2"),
        FakeGoal(id=2, title="P0 task", priority="P0"),
    ]
    result = select_today_focus(goals)
    assert result.goal.title == "P0 task"


def test_priority_ordering_all():
    goals = [
        FakeGoal(id=1, title="P3", priority="P3"),
        FakeGoal(id=2, title="P1", priority="P1"),
        FakeGoal(id=3, title="P2", priority="P2"),
        FakeGoal(id=4, title="P0", priority="P0"),
    ]
    result = select_today_focus(goals)
    assert result.goal.title == "P0"


# ── Deadline tiebreaker ─────────────────────────────────────────────────


def test_nearest_deadline_wins():
    goals = [
        FakeGoal(id=1, title="Far deadline", priority="P1", deadline="2026-12-31"),
        FakeGoal(id=2, title="Near deadline", priority="P1", deadline="2026-09-01"),
    ]
    result = select_today_focus(goals)
    assert result.goal.title == "Near deadline"


def test_no_deadline_loses_to_has_deadline():
    goals = [
        FakeGoal(id=1, title="No deadline", priority="P1", deadline=None),
        FakeGoal(id=2, title="Has deadline", priority="P1", deadline="2026-09-01"),
    ]
    result = select_today_focus(goals)
    assert result.goal.title == "Has deadline"


# ── Progress tiebreaker ─────────────────────────────────────────────────


def test_lowest_progress_wins_on_tie():
    goals = [
        FakeGoal(id=1, title="High progress", priority="P1", deadline="2026-09-01", progress=80),
        FakeGoal(id=2, title="Low progress", priority="P1", deadline="2026-09-01", progress=20),
    ]
    result = select_today_focus(goals)
    assert result.goal.title == "Low progress"


# ── Single goal ──────────────────────────────────────────────────────────


def test_single_goal():
    goal = FakeGoal(id=1, title="Only goal", priority="P1")
    result = select_today_focus([goal])
    assert result.goal is goal
    assert "P1" in result.reason


# ── Reason string ────────────────────────────────────────────────────────


def test_reason_includes_priority():
    goal = FakeGoal(id=1, title="Task", priority="P0")
    result = select_today_focus([goal])
    assert "P0" in result.reason


def test_reason_includes_deadline():
    goal = FakeGoal(id=1, title="Task", priority="P0", deadline="2026-09-15")
    result = select_today_focus([goal])
    assert "2026-09-15" in result.reason


def test_reason_includes_progress():
    goal = FakeGoal(id=1, title="Task", priority="P0", progress=45)
    result = select_today_focus([goal])
    assert "45%" in result.reason


# ── Return type ──────────────────────────────────────────────────────────


def test_returns_focus_result():
    result = select_today_focus([])
    assert isinstance(result, FocusResult)
