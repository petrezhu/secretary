"""Tests for candidate mining — rule aggregation of queries.jsonl."""

from __future__ import annotations

import json
import time
from pathlib import Path

from secretary.coldskill.mining import (
    aggregate_queries,
    candidates_path,
    queries_path,
    write_candidates,
)

NOW = 1_789_000_000.0  # fixed epoch, deterministic windows
DAY = 86400.0


def write_queries(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def row(ts: float, text: str, outcome: str = "allow") -> dict:
    return {"ts": ts, "text": text, "outcome": outcome}


# ── aggregate_queries ───────────────────────────────────────────────────────


def test_three_allows_becomes_candidate(tmp_path: Path):
    q = tmp_path / "queries.jsonl"
    write_queries(q, [
        row(NOW - 1 * DAY, "xx怎么用"),
        row(NOW - 2 * DAY, "xx怎么用"),
        row(NOW - 3 * DAY, "xx怎么用"),
    ])
    cands = aggregate_queries(q, now=NOW)
    assert len(cands) == 1
    assert cands[0]["text"] == "xx怎么用"
    assert cands[0]["count"] == 3
    assert cands[0]["suggested_action"] == "literal"
    assert "T" in cands[0]["last_seen"]  # ISO timestamp


def test_one_handled_row_denoises_candidate(tmp_path: Path):
    q = tmp_path / "queries.jsonl"
    write_queries(q, [
        row(NOW - 1 * DAY, "xx怎么用"),
        row(NOW - 2 * DAY, "xx怎么用"),
        row(NOW - 3 * DAY, "xx怎么用", outcome="greeting"),  # was handled
    ])
    cands = aggregate_queries(q, now=NOW)
    assert len(cands) == 0


def test_two_allows_below_threshold(tmp_path: Path):
    q = tmp_path / "queries.jsonl"
    write_queries(q, [
        row(NOW - 1 * DAY, "xx怎么用"),
        row(NOW - 2 * DAY, "xx怎么用"),
    ])
    assert aggregate_queries(q, now=NOW) == []


def test_expired_rows_ignored(tmp_path: Path):
    q = tmp_path / "queries.jsonl"
    # 3 occurrences but all older than the 30-day data span
    write_queries(q, [
        row(NOW - 40 * DAY, "老问题"),
        row(NOW - 41 * DAY, "老问题"),
        row(NOW - 42 * DAY, "老问题"),
    ])
    assert aggregate_queries(q, now=NOW) == []


def test_window_boundary_last_seen(tmp_path: Path):
    q = tmp_path / "queries.jsonl"
    # 3 hits but most recent is older than the 7-day window
    write_queries(q, [
        row(NOW - 8 * DAY, "过时热点"),
        row(NOW - 9 * DAY, "过时热点"),
        row(NOW - 10 * DAY, "过时热点"),
    ])
    assert aggregate_queries(q, now=NOW) == []
    # same counts, but last_seen inside the window → candidate
    write_queries(q, [
        row(NOW - 3 * DAY, "热点"),
        row(NOW - 6 * DAY, "热点"),
        row(NOW - 6.5 * DAY, "热点"),
    ])
    cands = aggregate_queries(q, now=NOW)
    assert [c["text"] for c in cands] == ["热点"]


def test_window_edge_exactly_7_days(tmp_path: Path):
    """Boundary semantics: last_seen exactly 7*86400s ago is inside the window."""
    q = tmp_path / "queries.jsonl"
    write_queries(q, [
        row(NOW - 7 * DAY, "边缘"),
        row(NOW - 5 * DAY, "边缘"),
        row(NOW - 2 * DAY, "边缘"),
    ])
    cands = aggregate_queries(q, now=NOW)
    assert [c["text"] for c in cands] == ["边缘"]


def test_garbage_rows_skipped(tmp_path: Path):
    q = tmp_path / "queries.jsonl"
    with open(q, "w", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write(json.dumps({"ts": "not-a-number", "text": "x", "outcome": "allow"}) + "\n")
        f.write(json.dumps(row(NOW - 1 * DAY, "ok问题")) + "\n")
        f.write(json.dumps(row(NOW - 2 * DAY, "ok问题")) + "\n")
        f.write(json.dumps(row(NOW - 3 * DAY, "ok问题")) + "\n")
        f.write('{"ts": 1, "text": 42, "outcome": "allow"}\n')  # text wrong type
    cands = aggregate_queries(q, now=NOW)
    assert [c["text"] for c in cands] == ["ok问题"]


def test_missing_and_empty_files(tmp_path: Path):
    assert aggregate_queries(tmp_path / "nope.jsonl", now=NOW) == []
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert aggregate_queries(empty, now=NOW) == []


def test_future_timestamps_ignored(tmp_path: Path):
    q = tmp_path / "queries.jsonl"
    write_queries(q, [
        row(NOW + 999 * DAY, "未来人"),
        row(NOW + 998 * DAY, "未来人"),
        row(NOW + 997 * DAY, "未来人"),
    ])
    assert aggregate_queries(q, now=NOW) == []


def test_deterministic_sorting(tmp_path: Path):
    q = tmp_path / "queries.jsonl"
    write_queries(q, [
        row(NOW - 1 * DAY, "b问题"), row(NOW - 2 * DAY, "b问题"), row(NOW - 3 * DAY, "b问题"),
        row(NOW - 1 * DAY, "a问题"), row(NOW - 2 * DAY, "a问题"), row(NOW - 3 * DAY, "a问题"),
        row(NOW - 1 * DAY, "a问题"),  # a问题 has 4 → sorts first by count
    ])
    cands = aggregate_queries(q, now=NOW)
    assert [c["text"] for c in cands] == ["a问题", "b问题"]
    assert [c["count"] for c in cands] == [4, 3]


# ── write_candidates ─────────────────────────────────────────────────────────


def test_write_candidates_structure_and_determinism(tmp_path: Path):
    out = tmp_path / "rule_candidates.json"
    payload = write_candidates(
        [{"text": "x", "count": 3, "last_seen": "2026-09-10T02:00:00+00:00", "suggested_action": "literal"}],
        out,
        generated_at="2026-09-10T02:30:00+00:00",
    )
    assert payload["generated_at"] == "2026-09-10T02:30:00+00:00"
    assert payload["candidates"][0]["text"] == "x"
    read_back = json.loads(out.read_text(encoding="utf-8"))
    assert read_back == payload


def test_write_candidates_empty_list_still_writes(tmp_path: Path):
    out = tmp_path / "rule_candidates.json"
    payload = write_candidates([], out, generated_at="2026-09-10T02:30:00+00:00")
    assert payload["candidates"] == []
    assert json.loads(out.read_text(encoding="utf-8")) == payload


# ── env-driven paths (default conventions) ───────────────────────────────────


def test_default_paths_fall_back_to_tmp(monkeypatch):
    monkeypatch.delenv("SECRETARY_DATA_DIR", raising=False)
    assert queries_path() == Path("/tmp/queries.jsonl")
    assert candidates_path() == Path("/tmp/rule_candidates.json")


def test_paths_respect_data_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SECRETARY_DATA_DIR", str(tmp_path))
    assert queries_path() == tmp_path / "queries.jsonl"
    assert candidates_path() == tmp_path / "rule_candidates.json"