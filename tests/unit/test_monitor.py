"""Unit tests for check result model."""

from secretary.monitor import CheckResult


def test_check_result_ok():
    r = CheckResult(name="test", status="ok", message="all good")
    assert r.status == "ok"
    d = r.to_dict()
    assert d["name"] == "test"
    assert "timestamp" in d


def test_check_result_critical():
    r = CheckResult(name="test", status="critical", message="bad", details={"x": 1})
    assert r.details["x"] == 1
