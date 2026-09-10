"""Test fixtures."""

import pytest


@pytest.fixture
def sample_goals():
    return [
        {"id": 1, "title": "完成Secretary项目", "status": "in_progress", "priority": "P0"},
        {"id": 2, "title": "学习Rust", "status": "pending", "priority": "P2"},
    ]


@pytest.fixture
def sample_tasks():
    return [
        {"id": 1, "title": "实现数据层", "status": "in_progress", "goal_id": 1, "heartbeat": None},
        {"id": 2, "title": "写测试", "status": "pending", "goal_id": 1, "heartbeat": None},
    ]
