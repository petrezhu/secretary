"""Test fixtures."""

import sys
from pathlib import Path

import pytest

# Make pytest import the ``secretary`` package that lives next to these
# tests (repo src/ layout) instead of any installed copy, so the suite
# always exercises the code in this checkout.
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


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
