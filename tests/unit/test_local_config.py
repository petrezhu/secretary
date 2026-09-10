"""Unit tests for local (gitignored) config loading paths.

Covers config/local_stocks.json / local.json / systems.json merging —
the runtime bridges that keep personal data out of tracked files.
"""

from __future__ import annotations

import json

from secretary.gateway.intents.systems import _load_systems
from secretary.memory.extractor import _load_stock_patterns
from secretary.wealth.sector_analysis import _load_sector_overrides

# ── stock patterns ───────────────────────────────────────────────────────────


def test_stock_patterns_missing_file_returns_empty(tmp_path):
    assert _load_stock_patterns(tmp_path / "nope.json") == []


def test_stock_patterns_loads_valid_file(tmp_path):
    p = tmp_path / "local_stocks.json"
    p.write_text(
        json.dumps(
            {"stock_patterns": [{"pattern": "(甲股票|甲)"}, {"pattern": "(乙基金)"}]},
            ensure_ascii=False,
        )
    )
    assert _load_stock_patterns(p) == ["(甲股票|甲)", "(乙基金)"]


def test_stock_patterns_skips_entries_without_pattern(tmp_path):
    p = tmp_path / "local_stocks.json"
    p.write_text(
        json.dumps(
            {"stock_patterns": [{"pattern": "(甲)"}, {"note": "no pattern"}, {"pattern": ""}]},
            ensure_ascii=False,
        )
    )
    assert _load_stock_patterns(p) == ["(甲)"]


def test_stock_patterns_malformed_json_is_soft(tmp_path):
    p = tmp_path / "local_stocks.json"
    p.write_text("{not valid json!!")
    assert _load_stock_patterns(p) == []


# ── sector overrides ─────────────────────────────────────────────────────────


def test_sector_overrides_missing_file_is_noop(tmp_path):
    # Should not raise, and must not mutate SECTOR_RULES
    _load_sector_overrides(tmp_path / "nope.json")


def test_sector_overrides_merges_keywords(tmp_path):
    from secretary.wealth.sector_analysis import SECTOR_RULES

    original = list(SECTOR_RULES["科技成长"]["keywords"])
    p = tmp_path / "local_stocks.json"
    p.write_text(
        json.dumps(
            {"sector_overrides": {"科技成长": {"extra_keywords": ["测试股X"]}}},
            ensure_ascii=False,
        )
    )
    try:
        _load_sector_overrides(p)
        assert "测试股X" in SECTOR_RULES["科技成长"]["keywords"]
        assert "测试股X" not in original
    finally:
        SECTOR_RULES["科技成长"]["keywords"] = original


def test_sector_overrides_skips_unknown_sector(tmp_path):
    from secretary.wealth.sector_analysis import SECTOR_RULES

    p = tmp_path / "local_stocks.json"
    p.write_text(
        json.dumps(
            {"sector_overrides": {"不存在的板块": {"extra_keywords": ["X"]}}},
            ensure_ascii=False,
        )
    )
    _load_sector_overrides(p)  # must not raise


def test_sector_overrides_malformed_json_is_soft(tmp_path):
    p = tmp_path / "local_stocks.json"
    p.write_text("junk")
    _load_sector_overrides(p)  # must not raise


# ── systems registry merge ───────────────────────────────────────────────────


def _write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False))


def test_load_systems_both_missing_returns_empty(tmp_path):
    assert _load_systems(tmp_path / "main.json", tmp_path / "local.json") == []


def test_load_systems_main_only(tmp_path):
    main = tmp_path / "systems.json"
    _write_json(main, {"systems": [{"name": "A"}, {"name": "B"}]})
    result = _load_systems(main, tmp_path / "local.json")
    assert [s["name"] for s in result] == ["A", "B"]


def test_load_systems_local_merges_appends(tmp_path):
    main = tmp_path / "systems.json"
    local = tmp_path / "local.json"
    _write_json(main, {"systems": [{"name": "A"}]})
    _write_json(local, {"systems": [{"name": "B"}, {"name": "C"}]})
    result = _load_systems(main, local)
    assert [s["name"] for s in result] == ["A", "B", "C"]


def test_load_systems_local_duplicate_name_skipped(tmp_path):
    main = tmp_path / "systems.json"
    local = tmp_path / "local.json"
    _write_json(main, {"systems": [{"name": "A"}]})
    _write_json(local, {"systems": [{"name": "A"}, {"name": "B"}]})
    result = _load_systems(main, local)
    assert [s["name"] for s in result] == ["A", "B"]


def test_load_systems_malformed_local_keeps_main(tmp_path):
    main = tmp_path / "systems.json"
    local = tmp_path / "local.json"
    _write_json(main, {"systems": [{"name": "A"}]})
    local.write_text("{bad json")
    result = _load_systems(main, local)
    assert [s["name"] for s in result] == ["A"]


def test_load_systems_malformed_main_returns_empty(tmp_path):
    main = tmp_path / "systems.json"
    main.write_text("{bad json")
    assert _load_systems(main, tmp_path / "local.json") == []