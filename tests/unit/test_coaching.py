"""Unit tests for M4 coaching — overdue detection, perception, adaptive planner."""

from __future__ import annotations

from datetime import datetime

from secretary.coach import (
    AdaptivePlanner,
    Emotion,
    EnergyState,
    GoalOverdueDetector,
    PerceptionEngine,
    Severity,
    Urgency,
)
from secretary.data.repository import Goal, Task

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_goal(
    gid: int = 1,
    title: str = "测试目标",
    status: str = "in_progress",
    deadline: str | None = None,
    goal_type: str = "weekly",
) -> Goal:
    return Goal(
        id=gid,
        title=title,
        status=status,
        goal_type=goal_type,
        created_at="2026-01-01",
        deadline=deadline,
    )


def _make_task(
    tid: int = 1,
    text: str = "测试任务",
    status: str = "pending",
    priority: int = 5,
) -> Task:
    return Task(
        id=tid,
        request_text=text,
        status=status,
        priority=priority,
    )


# ═══════════════════════════════════════════════════════════════════════════
# GoalOverdueDetector
# ═══════════════════════════════════════════════════════════════════════════


class TestGoalOverdueDetector:
    def setup_method(self):
        self.today = datetime(2026, 8, 31)
        self.detector = GoalOverdueDetector(today=self.today)

    def test_no_goals(self):
        assert self.detector.detect([]) == []

    def test_completed_goal_not_overdue(self):
        goal = _make_goal(deadline="2026-08-01", status="completed")
        assert self.detector.detect([goal]) == []

    def test_no_deadline_skipped(self):
        goal = _make_goal(deadline=None)
        assert self.detector.detect([goal]) == []

    def test_future_deadline_not_overdue(self):
        goal = _make_goal(deadline="2026-09-15")
        assert self.detector.detect([goal]) == []

    def test_today_is_overdue(self):
        goal = _make_goal(deadline="2026-08-31")
        results = self.detector.detect([goal])
        assert len(results) == 1
        assert results[0].severity == Severity.TODAY
        assert results[0].days_overdue == 0

    def test_this_week_overdue(self):
        goal = _make_goal(deadline="2026-08-25")
        results = self.detector.detect([goal])
        assert len(results) == 1
        assert results[0].severity == Severity.THIS_WEEK
        assert results[0].days_overdue == 6

    def test_long_overdue(self):
        goal = _make_goal(deadline="2026-08-01")
        results = self.detector.detect([goal])
        assert len(results) == 1
        assert results[0].severity == Severity.LONG_OVERDUE
        assert results[0].days_overdue == 30

    def test_coaching_message_contains_title(self):
        goal = _make_goal(title="学习Rust", deadline="2026-08-31")
        results = self.detector.detect([goal])
        assert "学习Rust" in results[0].coaching_message

    def test_multiple_goals_sorted_by_severity(self):
        goals = [
            _make_goal(gid=1, title="今日到期", deadline="2026-08-31"),
            _make_goal(gid=2, title="早已超期", deadline="2026-08-01"),
            _make_goal(gid=3, title="本周超期", deadline="2026-08-28"),
        ]
        results = self.detector.detect(goals)
        assert len(results) == 3
        assert results[0].severity == Severity.LONG_OVERDUE
        assert results[1].severity == Severity.THIS_WEEK
        assert results[2].severity == Severity.TODAY

    def test_generate_summary_no_overdue(self):
        assert "没有超期" in self.detector.generate_summary([])

    def test_generate_summary_with_overdue(self):
        goals = [_make_goal(gid=1, title="测试", deadline="2026-08-31")]
        overdue = self.detector.detect(goals)
        summary = self.detector.generate_summary(overdue)
        assert "1 个超期" in summary
        assert "测试" in summary

    def test_invalid_deadline_format(self):
        goal = _make_goal(deadline="not-a-date")
        assert self.detector.detect([goal]) == []


# ═══════════════════════════════════════════════════════════════════════════
# PerceptionEngine
# ═══════════════════════════════════════════════════════════════════════════


class TestPerceptionEngine:
    def setup_method(self):
        self.engine = PerceptionEngine()

    def test_emotion_happy(self):
        result = self.engine.perceive("太好了，今天很开心！")
        assert result.emotion == Emotion.HAPPY

    def test_emotion_tired(self):
        result = self.engine.perceive("好累啊，疲惫不堪")
        assert result.emotion == Emotion.TIRED

    def test_emotion_frustrated(self):
        result = self.engine.perceive("太烦了，卡住了")
        assert result.emotion == Emotion.FRUSTRATED

    def test_emotion_anxious(self):
        result = self.engine.perceive("焦虑，压力大")
        assert result.emotion == Emotion.ANXIOUS

    def test_emotion_neutral_default(self):
        result = self.engine.perceive("今天天气不错")
        # "不错" is in HAPPY keywords
        assert result.emotion in (Emotion.HAPPY, Emotion.NEUTRAL)

    def test_emotion_neutral_empty(self):
        result = self.engine.perceive("12345")
        assert result.emotion == Emotion.NEUTRAL

    def test_urgency_critical(self):
        result = self.engine.perceive("紧急！服务器挂了！")
        assert result.urgency == Urgency.CRITICAL

    def test_urgency_high(self):
        result = self.engine.perceive("尽快处理一下这个问题")
        assert result.urgency == Urgency.HIGH

    def test_urgency_medium(self):
        result = self.engine.perceive("最好今天做一下")
        assert result.urgency == Urgency.MEDIUM

    def test_urgency_low(self):
        result = self.engine.perceive("不急，以后有空再做")
        assert result.urgency == Urgency.LOW

    def test_urgency_default_medium(self):
        result = self.engine.perceive("你好")
        assert result.urgency == Urgency.MEDIUM

    def test_task_extraction(self):
        result = self.engine.perceive("需要修复登录bug")
        assert len(result.tasks) >= 1
        assert any("修复" in t or "登录" in t for t in result.tasks)

    def test_task_extraction_multiple(self):
        result = self.engine.perceive("需要写报告，还得改代码")
        assert len(result.tasks) >= 1

    def test_task_extraction_todo_prefix(self):
        result = self.engine.perceive("TODO: 优化数据库查询")
        assert len(result.tasks) >= 1

    def test_auto_create_high_urgency_not_tired(self):
        result = self.engine.perceive("紧急！需要修复服务器")
        assert result.should_auto_create is True

    def test_auto_create_high_urgency_tired(self):
        result = self.engine.perceive("紧急，但我累了")
        assert result.should_auto_create is False

    def test_auto_create_low_urgency(self):
        result = self.engine.perceive("不急，以后再说")
        assert result.should_auto_create is False


# ═══════════════════════════════════════════════════════════════════════════
# AdaptivePlanner
# ═══════════════════════════════════════════════════════════════════════════


class TestAdaptivePlanner:
    def test_empty_tasks(self):
        planner = AdaptivePlanner()
        energy = EnergyState(score=80)
        result = planner.plan([], energy)
        assert result.adjusted_count == 0
        assert "没有待办" in result.reason

    def test_low_energy_reduces_quantity(self):
        planner = AdaptivePlanner()
        energy = EnergyState(score=30)
        tasks = [_make_task(tid=i, priority=i) for i in range(1, 9)]
        result = planner.plan(tasks, energy)
        assert result.adjusted_count <= 3
        assert "精力不足" in result.reason

    def test_consecutive_failures_reduce_difficulty(self):
        planner = AdaptivePlanner(consecutive_failures=3)
        energy = EnergyState(score=60)
        tasks = [_make_task(tid=i, priority=i) for i in range(1, 9)]
        result = planner.plan(tasks, energy)
        assert result.adjusted_count <= 2
        assert "连续失败" in result.reason

    def test_high_completion_high_energy_adds_challenge(self):
        planner = AdaptivePlanner(
            max_tasks=5,
            consecutive_failures=0,
            recent_completion_rate=0.9,
        )
        energy = EnergyState(score=80)
        tasks = [_make_task(tid=i, priority=i) for i in range(1, 12)]
        result = planner.plan(tasks, energy)
        assert result.adjusted_count > 5
        assert "完成率" in result.reason

    def test_normal_planning(self):
        planner = AdaptivePlanner(max_tasks=5)
        energy = EnergyState(score=60)
        tasks = [_make_task(tid=i, priority=i) for i in range(1, 11)]
        result = planner.plan(tasks, energy)
        assert result.adjusted_count == 5
        assert "正常节奏" in result.reason

    def test_skips_completed_tasks(self):
        planner = AdaptivePlanner(max_tasks=10)
        energy = EnergyState(score=80)
        tasks = [
            _make_task(tid=1, status="pending"),
            _make_task(tid=2, status="completed"),
            _make_task(tid=3, status="in_progress"),
        ]
        result = planner.plan(tasks, energy)
        assert result.adjusted_count == 2

    def test_tasks_sorted_by_priority(self):
        planner = AdaptivePlanner(max_tasks=3)
        energy = EnergyState(score=60)
        tasks = [
            _make_task(tid=1, priority=10),
            _make_task(tid=2, priority=1),
            _make_task(tid=3, priority=5),
        ]
        result = planner.plan(tasks, energy)
        assert result.tasks[0].id == 2  # highest priority (lowest number)

    def test_low_energy_boundary(self):
        planner = AdaptivePlanner()
        energy = EnergyState(score=39)
        tasks = [_make_task(tid=i) for i in range(1, 9)]
        result = planner.plan(tasks, energy)
        assert "精力不足" in result.reason

    def test_low_energy_at_40_normal(self):
        planner = AdaptivePlanner(max_tasks=8)
        energy = EnergyState(score=40)
        tasks = [_make_task(tid=i) for i in range(1, 9)]
        result = planner.plan(tasks, energy)
        assert "正常节奏" in result.reason
