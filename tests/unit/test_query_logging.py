"""Unit tests for inbound-query tracing (queries.jsonl).

Every inbound message — no matter the outcome — must be traced to
``queries.jsonl``: over-long early return ("truncated"), high/medium
intent hits (handler name), and agent allow ("allow").

Tests override ``inbound._QUERIES.path`` to a tmp path, isolate the
repository wiring, and stub out the async supplement so no real HTTP
is ever attempted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import secretary.gateway.inbound as inbound
from secretary.gateway.api import app


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def queries_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the query log at a tmp file and expose its path."""
    path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(inbound._QUERIES, "path", path)
    return path


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with inbound wiring isolated for deterministic dispatch."""
    # No repository/config → data-dependent handlers answer "medium".
    monkeypatch.setattr(inbound, "_repo", None)
    monkeypatch.setattr(inbound, "_config", None)

    # Block the real Harness HTTP call scheduled on the medium path.
    async def _noop_supplement(text: str, intent_name: str, chat_id: str) -> None:
        return None

    monkeypatch.setattr(inbound, "_async_supplement", _noop_supplement)

    return TestClient(app)


def _read_entries(path: Path) -> list[dict]:
    """Parse the JSONL query log into a list of dicts."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ── Query-trace tests ────────────────────────────────────────────────────────


class TestQueryLogging:
    """The queries.jsonl trace must cover all four exit paths."""

    def test_truncated_long_message_is_logged(
        self, client: TestClient, queries_path: Path
    ):
        """Over-long messages early-return, but are still traced."""
        long_text = "长" * 250
        resp = client.post("/api/inbound", json={"text": long_text})
        assert resp.status_code == 200
        assert resp.json()["action"] == "allow"

        entries = _read_entries(queries_path)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "truncated"
        assert entries[0]["text"] == long_text[:80]
        assert isinstance(entries[0]["ts"], (int, float))

    def test_high_match_logged_with_handler_name(
        self, client: TestClient, queries_path: Path
    ):
        """HIGH hits are traced with the intent handler name."""
        resp = client.post("/api/inbound", json={"text": "你好"})
        assert resp.status_code == 200
        assert resp.json()["action"] == "handle"

        entries = _read_entries(queries_path)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "greeting"
        assert entries[0]["text"] == "你好"

    def test_medium_match_logged_with_handler_name(
        self, client: TestClient, queries_path: Path
    ):
        """MEDIUM hits (data missing) are traced with the handler name."""
        resp = client.post("/api/inbound", json={"text": "待办"})
        assert resp.status_code == 200
        assert resp.json()["action"] == "handle"
        assert resp.json()["reply"] == "收到，我查一下稍后回复你。"

        entries = _read_entries(queries_path)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "task_status"
        assert entries[0]["text"] == "待办"

    def test_low_allow_is_logged(self, client: TestClient, queries_path: Path):
        """Messages deferred to the agent are traced with outcome 'allow'."""
        resp = client.post("/api/inbound", json={"text": "今天天气怎么样"})
        assert resp.status_code == 200
        assert resp.json()["action"] == "allow"

        entries = _read_entries(queries_path)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "allow"
        assert entries[0]["text"] == "今天天气怎么样"

    def test_entries_are_appended_as_jsonl(
        self, client: TestClient, queries_path: Path
    ):
        """Each request appends one JSON line; earlier entries survive."""
        client.post("/api/inbound", json={"text": "你好"})
        client.post("/api/inbound", json={"text": "今天天气怎么样"})
        client.post("/api/inbound", json={"text": "长" * 250})

        entries = _read_entries(queries_path)
        assert len(entries) == 3
        assert [e["outcome"] for e in entries] == ["greeting", "allow", "truncated"]
        for entry in entries:
            assert set(entry.keys()) == {"ts", "text", "outcome"}
            assert isinstance(entry["ts"], (int, float))
            assert len(entry["text"]) <= 80

    def test_rotation_when_file_exceeds_256kb(self, queries_path: Path):
        """A log beyond 256KB is dropped and re-created on next record."""
        queries_path.write_text("x" * (256 * 1024 + 1), encoding="utf-8")
        assert queries_path.stat().st_size > 256 * 1024

        inbound._record_query("你好", "greeting")

        entries = _read_entries(queries_path)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "greeting"
        assert queries_path.stat().st_size <= 256 * 1024

    def test_record_query_never_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Best-effort contract: unwritable targets are silently swallowed."""
        monkeypatch.setattr(inbound._QUERIES, "path", tmp_path / "missing" / "queries.jsonl")
        inbound._record_query("你好", "greeting")  # must not raise