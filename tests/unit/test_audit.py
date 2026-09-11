"""Tests for the audit log module."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from secretary.engine.audit import AuditEntry, AuditLog

# ------------------------------------------------------------------
# AuditEntry unit tests
# ------------------------------------------------------------------


class TestAuditEntry:
    def test_to_dict_round_trip(self):
        """to_dict → from_dict should be lossless."""
        entry = AuditEntry(
            id=42,
            timestamp=datetime(2026, 8, 31, 14, 30, 0),
            action="deploy",
            target="webapp",
            details={"version": "1.2.3", "env": "prod"},
            result="success",
            reversible=True,
        )
        d = entry.to_dict()
        restored = AuditEntry.from_dict(d)
        assert restored.id == entry.id
        assert restored.timestamp == entry.timestamp
        assert restored.action == entry.action
        assert restored.target == entry.target
        assert restored.details == entry.details
        assert restored.result == entry.result
        assert restored.reversible is True

    def test_from_dict_defaults_reversible(self):
        """from_dict should default reversible to False if missing."""
        d = {
            "id": 1,
            "timestamp": "2026-01-01T00:00:00",
            "action": "test",
            "target": "x",
            "details": {},
            "result": "success",
        }
        entry = AuditEntry.from_dict(d)
        assert entry.reversible is False


# ------------------------------------------------------------------
# AuditLog — writing
# ------------------------------------------------------------------


class TestAuditLogWrite:
    @pytest.mark.asyncio
    async def test_log_appends_jsonl_line(self, tmp_path: Path):
        """Each log() call should append one JSON line."""
        path = tmp_path / "audit.jsonl"
        audit = AuditLog(path=path)

        entry = await audit.log(
            action="notify",
            target="user@telegram",
            details={"message": "hello"},
            result="success",
        )

        assert entry.id == 1
        assert entry.action == "notify"
        assert entry.target == "user@telegram"
        assert entry.result == "success"
        assert entry.details == {"message": "hello"}

        raw = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(raw) == 1
        data = json.loads(raw[0])
        assert data["action"] == "notify"

    @pytest.mark.asyncio
    async def test_ids_increment(self, tmp_path: Path):
        path = tmp_path / "audit.jsonl"
        audit = AuditLog(path=path)

        e1 = await audit.log(action="a", target="t", result="success")
        e2 = await audit.log(action="b", target="t", result="failed")
        e3 = await audit.log(action="c", target="t", result="skipped")

        assert e1.id == 1
        assert e2.id == 2
        assert e3.id == 3

    @pytest.mark.asyncio
    async def test_default_details(self, tmp_path: Path):
        """details should default to {} when not provided."""
        path = tmp_path / "audit.jsonl"
        audit = AuditLog(path=path)
        entry = await audit.log(action="ping", target="server")
        assert entry.details == {}

    @pytest.mark.asyncio
    async def test_id_survives_restart(self, tmp_path: Path):
        """A fresh AuditLog reading an existing file should continue IDs."""
        path = tmp_path / "audit.jsonl"
        audit1 = AuditLog(path=path)
        await audit1.log(action="a", target="t")
        await audit1.log(action="b", target="t")

        # "restart" — new instance reading same file
        audit2 = AuditLog(path=path)
        entry = await audit2.log(action="c", target="t")
        assert entry.id == 3


# ------------------------------------------------------------------
# AuditLog — querying
# ------------------------------------------------------------------


class TestAuditLogQuery:
    @pytest.fixture
    async def populated_log(self, tmp_path: Path):
        """Write several entries for query tests."""
        path = tmp_path / "audit.jsonl"
        audit = AuditLog(path=path)

        datetime(2026, 8, 31, 12, 0, 0)
        entries_info = [
            ("deploy", "webapp", "success"),
            ("deploy", "api", "success"),
            ("notify", "user@telegram", "success"),
            ("deploy", "webapp", "failed"),
            ("backup", "db", "success"),
        ]
        for action, target, result in entries_info:
            await audit.log(action=action, target=target, result=result)

        return audit

    @pytest.mark.asyncio
    async def test_query_all(self, populated_log: AuditLog):
        entries = populated_log.query()
        assert len(entries) == 5

    @pytest.mark.asyncio
    async def test_query_by_action(self, populated_log: AuditLog):
        deploys = populated_log.query(action="deploy")
        assert len(deploys) == 3
        assert all(e.action == "deploy" for e in deploys)

        notifies = populated_log.query(action="notify")
        assert len(notifies) == 1
        assert notifies[0].target == "user@telegram"

    @pytest.mark.asyncio
    async def test_query_no_match(self, populated_log: AuditLog):
        assert populated_log.query(action="nonexistent") == []

    @pytest.mark.asyncio
    async def test_read_all(self, populated_log: AuditLog):
        all_entries = populated_log.read_all()
        assert len(all_entries) == 5


# ------------------------------------------------------------------
# Edge cases / file persistence
# ------------------------------------------------------------------


class TestAuditLogEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_file_query(self, tmp_path: Path):
        """Querying an empty file should return []."""
        path = tmp_path / "audit.jsonl"
        path.write_text("")
        audit = AuditLog(path=path)
        assert audit.query() == []

    @pytest.mark.asyncio
    async def test_nonexistent_file_query(self, tmp_path: Path):
        """Querying before any writes should return []."""
        audit = AuditLog(path=tmp_path / "nope.jsonl")
        assert audit.query() == []

    @pytest.mark.asyncio
    async def test_unicode_in_details(self, tmp_path: Path):
        """Chinese characters in details should round-trip correctly."""
        path = tmp_path / "audit.jsonl"
        audit = AuditLog(path=path)
        await audit.log(
            action="提醒",
            target="用户",
            details={"消息": "你好世界"},
            result="success",
        )
        entries = audit.read_all()
        assert len(entries) == 1
        assert entries[0].action == "提醒"
        assert entries[0].details["消息"] == "你好世界"

    @pytest.mark.asyncio
    async def test_jsonl_format_valid(self, tmp_path: Path):
        """Every line in the file should be valid JSON."""
        path = tmp_path / "audit.jsonl"
        audit = AuditLog(path=path)
        for i in range(10):
            await audit.log(action=f"act_{i}", target=f"t_{i}")

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 10
        for line in lines:
            json.loads(line)  # should not raise
