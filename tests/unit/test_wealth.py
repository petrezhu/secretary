"""Tests for wealth engine modules — mock HTTP, test scoring/P&L/keywords."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from secretary.wealth.decision import (
    assess_position,
    build_scenarios,
    compute_decision,
    format_decision_report,
)
from secretary.wealth.jobs import WealthJobs
from secretary.wealth.monitor import (
    Announcement,
    MonitorResult,
    check_all_holdings,
    check_runze,
    check_stock_announcements,
    format_announcement_alert,
    parse_cninfo_response,
    parse_eastmoney_response,
    scan_keywords,
)
from secretary.wealth.national_team import (
    NationalTeamResult,
    compute_score_from_flows,
    get_national_team_signal,
)
from secretary.wealth.portfolio import (
    Cash,
    ClosedPosition,
    Holding,
    PendingAction,
    PortfolioData,
    Transaction,
    compute_portfolio,
    format_portfolio_report,
    get_all_stock_codes,
    load_portfolio,
    load_portfolio_data,
    parse_tencent_prices,
)
from secretary.wealth.qdii import (
    QDII_MAPPING,
    QDIISnapshot,
    analyze_qdii,
    is_qdii,
    parse_us_index_quote,
)
from secretary.wealth.risk import (
    DEFAULT_RULES,
    RiskCheckResult,
    check_discipline,
    check_position_concentration,
    check_stop_loss,
    format_risk_report,
    run_risk_check,
)

# ── National Team Tests ─────────────────────────────────────────────────────


class TestNationalTeamScoring:
    """Test national team signal scoring logic."""

    def test_strong_bullish(self):
        """Large inflows across many ETFs → high positive score."""
        flows = {
            "510050": 15.0,
            "510300": 20.0,
            "510500": 10.0,
            "159919": 12.0,
            "159915": 8.0,
            "588000": 5.0,
        }
        score, interp = compute_score_from_flows(flows)
        assert score >= 3.0
        assert "买入" in interp
        assert "6只" in interp  # all positive

    def test_strong_bearish(self):
        """Large outflows across all ETFs → low negative score."""
        flows = {
            "510050": -15.0,
            "510300": -20.0,
            "510500": -10.0,
            "159919": -12.0,
            "159915": -8.0,
            "588000": -5.0,
        }
        score, interp = compute_score_from_flows(flows)
        assert score <= -3.0
        assert "卖出" in interp

    def test_neutral(self):
        """Small mixed flows → score near zero."""
        flows = {
            "510050": 0.5,
            "510300": -0.3,
            "510500": 0.2,
        }
        score, interp = compute_score_from_flows(flows)
        assert -1.0 <= score <= 1.0
        assert "中性" in interp or "不明显" in interp

    def test_mixed_signals(self):
        """Half inflow, half outflow → moderate score."""
        flows = {
            "510050": 5.0,
            "510300": 8.0,
            "510500": -5.0,
            "159919": -8.0,
        }
        score, interp = compute_score_from_flows(flows)
        assert -2.0 <= score <= 2.0

    def test_empty_flows(self):
        """No data → score 0."""
        score, interp = compute_score_from_flows({})
        assert score == 0.0
        assert "无" in interp

    def test_score_clamped_to_range(self):
        """Score should be clamped to [-5, +5]."""
        huge = {f"etf{i}": 100.0 for i in range(10)}
        score, _ = compute_score_from_flows(huge)
        assert -5.0 <= score <= 5.0

    def test_single_etf(self):
        """Single ETF inflow should still work."""
        flows = {"510300": 3.0}
        score, interp = compute_score_from_flows(flows)
        assert score > 0
        assert "流入1只" in interp


class TestNationalTeamFetch:
    """Test national team HTTP fetching with mocks."""

    @pytest.mark.asyncio
    async def test_get_signal_with_mock(self):
        """Mock eastmoney API and test full pipeline."""
        mock_data = {
            "data": {
                "diff": [
                    {"f12": "510050", "f14": "上证50ETF", "f62": 500000000},
                    {"f12": "510300", "f14": "沪深300ETF", "f62": -200000000},
                    {"f12": "510500", "f14": "中证500ETF", "f62": 100000000},
                ]
            }
        }
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value=mock_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        result = await get_national_team_signal(session=mock_session)
        assert isinstance(result, NationalTeamResult)
        assert result.score != 0  # Should have some signal


# ── Portfolio Tests ──────────────────────────────────────────────────────────


class TestTencentPriceParser:
    """Test Tencent quote API response parsing."""

    def test_parse_single_quote(self):
        raw = 'v_sh600519="1~贵州茅台~600519~1800.50~10.5~1805.00~100000~50000~50000~1800.00~9999~";'
        prices = parse_tencent_prices(raw)
        assert prices["sh600519"] == 1800.50

    def test_parse_multiple_quotes(self):
        raw = (
            'v_sh600519="1~贵州茅台~600519~1800.00~10~1800~100~50~50~1800~100~";\n'
            'v_sz300001="2~测试股票A~300001~35.50~0.5~35.80~200~100~100~35.50~50~";\n'
        )
        prices = parse_tencent_prices(raw)
        assert prices["sh600519"] == 1800.00
        assert prices["sz300001"] == 35.50

    def test_parse_empty_string(self):
        assert parse_tencent_prices("") == {}

    def test_parse_malformed_line(self):
        raw = 'v_sh600519="invalid";'
        prices = parse_tencent_prices(raw)
        # Should not crash, just skip
        assert "sh600519" not in prices


class TestPortfolioCompute:
    """Test portfolio P&L calculation."""

    def test_basic_pnl(self):
        holdings = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 1800.0},
        ]
        prices = {"sh600519": 1900.0}
        snap = compute_portfolio(holdings, prices)
        assert snap.total_cost == 180000.0
        assert snap.total_market_value == 190000.0
        assert snap.total_pnl == 10000.0
        assert len(snap.holdings) == 1
        assert snap.holdings[0].pnl == 10000.0
        assert snap.holdings[0].pnl_pct == pytest.approx(5.56, abs=0.01)

    def test_loss(self):
        holdings = [
            {"code": "sz300001", "name": "测试股票A", "shares": 200, "cost_price": 40.0},
        ]
        prices = {"sz300001": 35.0}
        snap = compute_portfolio(holdings, prices)
        assert snap.total_pnl < 0
        assert snap.holdings[0].pnl == -1000.0

    def test_multiple_holdings(self):
        holdings = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 1800.0},
            {"code": "sz300001", "name": "测试股票A", "shares": 200, "cost_price": 40.0},
        ]
        prices = {"sh600519": 1900.0, "sz300001": 35.0}
        snap = compute_portfolio(holdings, prices)
        assert len(snap.holdings) == 2
        assert snap.total_cost == 188000.0

    def test_significant_moves_detection(self):
        holdings = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0},
        ]
        prices = {"sh600519": 105.0}  # +5%
        snap = compute_portfolio(holdings, prices, move_threshold=3.0)
        assert len(snap.significant_moves) == 1
        assert "涨" in snap.significant_moves[0]

    def test_no_significant_moves(self):
        holdings = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0},
        ]
        prices = {"sh600519": 101.0}  # +1%
        snap = compute_portfolio(holdings, prices, move_threshold=3.0)
        assert len(snap.significant_moves) == 0

    def test_fallback_to_cost_price(self):
        """When price not available, fallback to cost price (zero P&L)."""
        holdings = [
            {"code": "sh999999", "name": "Test", "shares": 100, "cost_price": 50.0},
        ]
        snap = compute_portfolio(holdings, prices={})
        assert snap.total_pnl == 0.0

    def test_format_report(self):
        holdings = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0},
        ]
        prices = {"sh600519": 110.0}
        snap = compute_portfolio(holdings, prices)
        report = format_portfolio_report(snap)
        assert "持仓报告" in report
        assert "贵州茅台" in report

    def test_fund_otc_uses_nav(self):
        """OTC fund should use NAV instead of real-time price."""
        holdings = [
            {"code": "f_999999", "name": "测试基金A", "shares": 1000, "type": "fund_otc", "nav": 2.5, "cost_price": 2.0},
        ]
        snap = compute_portfolio(holdings, prices={})
        assert snap.holdings[0].market_value == pytest.approx(1000 * 2.5, abs=0.01)
        assert snap.holdings[0].current_price == 2.5

    def test_gold_accumulate_uses_grams(self):
        """Gold accumulate should use grams * cost_per_gram."""
        holdings = [
            {"code": "test_gold", "name": "测试黄金", "type": "gold_accumulate", "grams": 10, "cost_total": 5000.0, "cost_per_gram": 500.0},
        ]
        snap = compute_portfolio(holdings, prices={})
        assert snap.holdings[0].market_value == pytest.approx(10 * 500.0, abs=0.01)
        assert snap.holdings[0].cost_per_gram == 500.0

    def test_skip_zero_shares(self):
        """Holdings with 0 shares should be skipped."""
        holdings = [
            {"code": "sz000002", "name": "测试股票B", "shares": 0, "cost_price": 80.0, "type": "stock"},
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0, "type": "stock"},
        ]
        prices = {"sh600519": 110.0}
        snap = compute_portfolio(holdings, prices)
        assert len(snap.holdings) == 1
        assert snap.holdings[0].name == "贵州茅台"

    def test_mixed_asset_types(self):
        """Portfolio with stock + fund + gold should compute correctly."""
        holdings = [
            {"code": "sh601398", "name": "工商银行", "shares": 1000, "cost_price": 5.0, "type": "stock"},
            {"code": "f_999999", "name": "测试基金A", "shares": 1000, "type": "fund_otc", "nav": 2.5, "cost_price": 2.0},
            {"code": "test_gold", "name": "测试黄金", "type": "gold_accumulate", "grams": 10, "cost_total": 5000.0, "cost_per_gram": 500.0},
            {"code": "sz159999", "name": "测试ETF", "shares": 500, "cost_price": 1.0, "type": "etf"},
        ]
        prices = {"sh601398": 5.50, "sz159999": 1.10}
        snap = compute_portfolio(holdings, prices)
        assert len(snap.holdings) == 4
        # Stock cost: 1000 * 5.0 = 5000
        # Fund cost: 1000 * 2.0 = 2000
        # Gold cost: 5000.0
        # ETF cost: 500 * 1.0 = 500
        expected_cost = 5000 + 2000 + 5000.0 + 500
        assert snap.total_cost == pytest.approx(expected_cost, abs=0.1)


class TestPortfolioIO:
    """Test portfolio file loading."""

    def test_load_portfolio_json(self, tmp_path):
        data = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 1800.0}
        ]
        p = tmp_path / "portfolio.json"
        p.write_text(json.dumps(data, ensure_ascii=False))
        result = load_portfolio(p)
        assert len(result) == 1
        assert result[0]["code"] == "sh600519"

    def test_load_missing_file(self):
        result = load_portfolio("/nonexistent/path.json")
        assert result == []


class TestPortfolioDataLoading:
    """Test load_portfolio_data with full format."""

    def _full_portfolio(self):
        """Return a full-format portfolio dict."""
        return {
            "holdings": [
                {"name": "工商银行", "code": "sh601398", "shares": 1000, "cost_price": 5.0, "type": "stock"},
                {"name": "测试基金A", "code": "f_999999", "shares": 1000, "type": "fund_otc", "nav": 2.5},
                {"name": "测试黄金", "code": "test_gold", "type": "gold_accumulate", "grams": 10, "cost_total": 5000.0, "cost_per_gram": 500.0},
            ],
            "cash": {"total": 10000.0, "note": "test"},
            "closed_positions": [
                {"name": "测试股票C", "code": "sz000003", "close_date": "2026-03-05", "total_cost": 10000, "total_revenue": 9500, "profit_loss": -500, "return_rate": -5.0, "holding_days": 30}
            ],
            "pending_actions": [],
        }

    def test_load_full_format(self, tmp_path):
        """New format with holdings + cash + closed_positions."""
        p = tmp_path / "portfolio.json"
        p.write_text(json.dumps(self._full_portfolio(), ensure_ascii=False))
        data = load_portfolio_data(p)
        assert isinstance(data, PortfolioData)
        assert len(data.holdings) == 3
        assert data.cash.total == 10000.0
        assert len(data.closed_positions) == 1
        assert data.closed_positions[0].name == "测试股票C"
        assert data.closed_positions[0].profit_loss == -500

    def test_load_legacy_format(self, tmp_path):
        """Legacy list format wraps into PortfolioData."""
        data = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 1800.0}
        ]
        p = tmp_path / "portfolio.json"
        p.write_text(json.dumps(data, ensure_ascii=False))
        pd = load_portfolio_data(p)
        assert isinstance(pd, PortfolioData)
        assert len(pd.holdings) == 1
        assert pd.cash.total == 0.0
        assert len(pd.closed_positions) == 0

    def test_load_missing_returns_empty(self):
        pd = load_portfolio_data("/nonexistent/path.json")
        assert isinstance(pd, PortfolioData)
        assert len(pd.holdings) == 0

    def test_load_full_format_parses_holding_fields(self, tmp_path):
        """Holding should parse all extended fields."""
        p = tmp_path / "portfolio.json"
        p.write_text(json.dumps(self._full_portfolio(), ensure_ascii=False))
        pd = load_portfolio_data(p)
        h = pd.holdings[0]
        assert h.code == "sh601398"
        assert h.name == "工商银行"
        assert h.shares == 1000
        assert h.cost_price == 5.0
        assert h.type == "stock"

        fund = pd.holdings[1]
        assert fund.type == "fund_otc"
        assert fund.nav == 2.5

        gold = pd.holdings[2]
        assert gold.type == "gold_accumulate"
        assert gold.grams == 10
        assert gold.cost_total == 5000.0
        assert gold.cost_per_gram == 500.0


class TestGetAllStockCodes:
    """Test get_all_stock_codes extraction."""

    def test_filters_stock_and_etf(self):
        pd = PortfolioData(holdings=[
            Holding(code="sh601398", name="工商银行", shares=1000, type="stock"),
            Holding(code="f_999999", name="测试基金A", shares=1000, type="fund_otc"),
            Holding(code="test_gold", name="测试黄金", type="gold_accumulate"),
            Holding(code="sz159999", name="测试ETF", shares=500, type="etf"),
        ])
        codes = get_all_stock_codes(pd)
        assert codes == ["sh601398", "sz159999"]

    def test_skips_zero_shares(self):
        pd = PortfolioData(holdings=[
            Holding(code="sh601398", name="工商", shares=0, type="stock"),
            Holding(code="sz159692", name="ETF", shares=1300, type="etf"),
        ])
        codes = get_all_stock_codes(pd)
        assert codes == ["sz159692"]

    def test_empty_portfolio(self):
        pd = PortfolioData()
        assert get_all_stock_codes(pd) == []


# ── Announcement Monitor Tests ──────────────────────────────────────────────


class TestKeywordDetection:
    """Test keyword scanning logic."""

    def test_single_keyword(self):
        assert scan_keywords("公司拟收购某公司") == ["收购"]
        assert scan_keywords("公司拟重组") == ["重组"]
        assert scan_keywords("公司发行股份购买资产") == ["发行股份"]
        assert scan_keywords("公司股票复牌") == ["复牌"]

    def test_extended_keywords(self):
        """New keywords: 减持/增持/分红/业绩预告/可转债."""
        assert scan_keywords("大股东减持公告") == ["减持"]
        assert scan_keywords("控股股东增持计划") == ["增持"]
        assert scan_keywords("年度分红预案") == ["分红"]
        assert scan_keywords("2026年半年度业绩预告") == ["业绩预告"]
        assert scan_keywords("可转债发行公告") == ["可转债"]

    def test_multiple_keywords(self):
        result = scan_keywords("公司发行股份购买资产暨复牌公告")
        assert "发行股份" in result
        assert "复牌" in result

    def test_no_keywords(self):
        assert scan_keywords("公司年度报告") == []
        assert scan_keywords("") == []

    def test_custom_keywords(self):
        result = scan_keywords("季度分红公告", keywords=["分红", "增持"])
        assert "分红" in result


class TestEastmoneyParser:
    """Test eastmoney response parsing."""

    def test_parse_normal_response(self):
        data = {
            "data": {
                "list": [
                    {
                        "title": "关于收购资产的公告",
                        "notice_date": "2024-11-15 00:00:00",
                        "art_code": "AN20241115001",
                        "columns": [{"column_code": "szse"}],
                    },
                    {
                        "title": "年度报告",
                        "notice_date": "2024-11-10 00:00:00",
                        "art_code": "AN20241110001",
                        "columns": [{"column_code": "szse"}],
                    },
                ]
            }
        }
        results = parse_eastmoney_response(data)
        assert len(results) == 2
        assert "收购" in results[0].title
        assert results[0].matched_keywords == ["收购"]
        assert results[0].date == "2024-11-15"
        assert results[1].matched_keywords == []

    def test_parse_empty_response(self):
        assert parse_eastmoney_response({}) == []
        assert parse_eastmoney_response({"data": None}) == []
        assert parse_eastmoney_response({"data": {"list": None}}) == []

    def test_url_construction(self):
        data = {
            "data": {
                "list": [
                    {
                        "title": "测试公告",
                        "notice_date": "2024-11-15 00:00:00",
                        "art_code": "AN123",
                        "columns": [{"column_code": "sse"}],
                    }
                ]
            }
        }
        results = parse_eastmoney_response(data)
        assert "AN123" in results[0].url
        assert "eastmoney.com" in results[0].url


class TestCninfoParser:
    """Test cninfo response parsing."""

    def test_parse_normal_response(self):
        data = {
            "announcements": [
                {
                    "announcementTitle": "关于<em>收购</em>资产的公告",
                    "announcementDate": 1700000000000,
                    "adjunctUrl": "2024/gonggao/test.pdf",
                },
                {
                    "announcementTitle": "年度报告",
                    "announcementDate": 1699000000000,
                    "adjunctUrl": "2024/gonggao/annual.pdf",
                },
            ]
        }
        results = parse_cninfo_response(data)
        assert len(results) == 2
        # HTML tags should be stripped
        assert "<em>" not in results[0].title
        assert "收购" in results[0].title
        assert results[0].matched_keywords == ["收购"]
        assert results[1].matched_keywords == []

    def test_parse_empty_response(self):
        assert parse_cninfo_response({}) == []
        assert parse_cninfo_response({"announcements": None}) == []

    def test_url_construction(self):
        data = {
            "announcements": [
                {
                    "announcementTitle": "Test",
                    "announcementDate": 1700000000000,
                    "adjunctUrl": "2024/test.pdf",
                }
            ]
        }
        results = parse_cninfo_response(data)
        assert results[0].url == "http://static.cninfo.com.cn/2024/test.pdf"


class TestCheckStockAnnouncements:
    """Test generic stock announcement checking with eastmoney."""

    @pytest.mark.asyncio
    async def test_check_with_keyword_match(self):
        mock_data = {
            "data": {
                "list": [
                    {
                        "title": "关于筹划重大资产重组的停牌公告",
                        "notice_date": "2024-11-15 00:00:00",
                        "art_code": "AN001",
                        "columns": [{"column_code": "sse"}],
                    },
                ]
            }
        }
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value=mock_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        result = await check_stock_announcements("sh600519", "贵州茅台", session=mock_session)
        assert len(result.new_keyword_matches) == 1
        assert "重组" in result.new_keyword_matches[0].matched_keywords

    @pytest.mark.asyncio
    async def test_check_filters_seen_urls(self):
        """Seen URLs should be filtered out."""
        mock_data = {
            "data": {
                "list": [
                    {
                        "title": "收购公告",
                        "notice_date": "2024-11-15 00:00:00",
                        "art_code": "AN_SEEN",
                        "columns": [{"column_code": "sse"}],
                    },
                ]
            }
        }
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value=mock_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        seen = {"https://data.eastmoney.com/notices/detail/sse/AN_SEEN.html"}
        result = await check_stock_announcements("sh600519", seen_urls=seen, session=mock_session)
        assert len(result.new_keyword_matches) == 0


class TestCheckAllHoldings:
    """Test check_all_holdings for multiple stocks."""

    @pytest.mark.asyncio
    async def test_checks_multiple_stocks(self):
        """Should return results for each stock code."""
        mock_data = {
            "data": {
                "list": [
                    {
                        "title": "年度报告",
                        "notice_date": "2024-11-15 00:00:00",
                        "art_code": "AN001",
                        "columns": [{"column_code": "sse"}],
                    },
                ]
            }
        }
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value=mock_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        results = await check_all_holdings(
            stock_codes=["sh601398", "sz300001"],
            stock_names={"sh601398": "工商银行", "sz300001": "测试股票A"},
            session=mock_session,
        )
        assert len(results) == 2
        assert "sh601398" in results
        assert "sz300001" in results


class TestFormatAnnouncementAlert:
    """Test announcement alert formatting."""

    def test_format_with_keyword_matches(self):
        result = MonitorResult(
            new_keyword_matches=[
                Announcement(title="收购公告", date="2024-11-15", url="http://example.com", matched_keywords=["收购"]),
            ]
        )
        alert = format_announcement_alert("sh600519", "贵州茅台", result)
        assert "贵州茅台" in alert
        assert "收购" in alert
        assert "收购公告" in alert

    def test_format_empty(self):
        result = MonitorResult(new_keyword_matches=[])
        assert format_announcement_alert("sh600519", "贵州茅台", result) == ""


class TestRunzeMonitorFetch:
    """Test runze monitor (legacy compat) with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_check_with_keyword_match(self):
        mock_data = {
            "data": {
                "list": [
                    {
                        "title": "关于筹划重大资产重组的停牌公告",
                        "notice_date": "2024-11-15 00:00:00",
                        "art_code": "AN001",
                        "columns": [{"column_code": "szse"}],
                    },
                ]
            }
        }
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value=mock_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        result = await check_runze(session=mock_session)
        assert len(result.new_keyword_matches) == 1
        assert "重组" in result.new_keyword_matches[0].matched_keywords

    @pytest.mark.asyncio
    async def test_check_filters_seen_urls(self):
        mock_data = {
            "data": {
                "list": [
                    {
                        "title": "收购公告",
                        "notice_date": "2024-11-15 00:00:00",
                        "art_code": "AN_SEEN",
                        "columns": [{"column_code": "szse"}],
                    },
                ]
            }
        }
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value=mock_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        seen = {"https://data.eastmoney.com/notices/detail/szse/AN_SEEN.html"}
        result = await check_runze(seen_urls=seen, session=mock_session)
        assert len(result.new_keyword_matches) == 0  # filtered out


# ── WealthJobs Tests ─────────────────────────────────────────────────────────


class TestWealthJobs:
    """Test WealthJobs scheduler integration."""

    def test_register_jobs(self):
        """Should register 3 jobs with the scheduler."""
        mock_scheduler = MagicMock()
        mock_dispatcher = MagicMock()
        jobs = WealthJobs(dispatcher=mock_dispatcher)
        jobs.register_jobs(mock_scheduler)
        assert mock_scheduler.add_job.call_count == 3
        job_names = {call.args[0].name for call in mock_scheduler.add_job.call_args_list}
        assert job_names == {"portfolio_check", "national_team", "announcement_monitor"}


# ── Dataclass Tests ─────────────────────────────────────────────────────────


class TestDataclasses:
    """Test new portfolio dataclasses."""

    def test_cash_defaults(self):
        c = Cash()
        assert c.total == 0.0
        assert c.note == ""

    def test_closed_position(self):
        cp = ClosedPosition(
            name="测试股票C", code="sz000003",
            close_date="2026-03-05", total_cost=10000,
            total_revenue=9500, profit_loss=-500,
            return_rate=-5.0, holding_days=30,
        )
        assert cp.name == "测试股票C"
        assert cp.profit_loss == -500

    def test_pending_action_defaults(self):
        pa = PendingAction()
        assert pa.action == ""
        assert pa.target == ""

    def test_transaction_defaults(self):
        t = Transaction()
        assert t.date == ""
        assert t.shares == 0.0

    def test_portfolio_data_defaults(self):
        pd = PortfolioData()
        assert pd.holdings == []
        assert pd.cash.total == 0.0
        assert pd.closed_positions == []
        assert pd.pending_actions == []


# ── QDII Tests ─────────────────────────────────────────────────────────────


class TestQDII:
    """Test QDII/cross-border ETF analysis."""

    def test_qdii_mapping_exists(self):
        """QDII_MAPPING should contain known ETF codes."""
        assert len(QDII_MAPPING) >= 3
        assert "f_021778" in QDII_MAPPING
        assert QDII_MAPPING["f_021778"]["index"] == "us.NDX"
        assert QDII_MAPPING["f_021778"]["name"] == "纳斯达克100"

    def test_is_qdii_true(self):
        """Known QDII codes should return True."""
        assert is_qdii("f_021778") is True
        assert is_qdii("sh513500") is True

    def test_is_qdii_false(self):
        """Non-QDII codes should return False."""
        assert is_qdii("sh600519") is False
        assert is_qdii("unknown_code") is False

    def test_parse_us_index_quote(self):
        """Parse a mock Tencent US index response."""
        fields = [""] * 50
        fields[1] = "纳斯达克100"
        fields[3] = "18500.50"  # price
        fields[32] = "1.25"  # change%
        fields[47] = "19000.00"  # 52w high
        fields[48] = "15000.00"  # 52w low
        raw = 'v_us.NDX="' + "~".join(fields) + '";'
        result = parse_us_index_quote(raw, "us.NDX")
        assert result["code"] == "us.NDX"
        assert result["price"] == 18500.50
        assert result["change_pct"] == 1.25
        assert result["high_52w"] == 19000.00
        assert result["low_52w"] == 15000.00

    def test_parse_us_index_quote_empty(self):
        """Empty string should return defaults."""
        result = parse_us_index_quote("", "us.NDX")
        assert result["price"] == 0.0

    def test_parse_us_index_quote_short(self):
        """Short response with too few fields should return defaults."""
        raw = 'v_us.NDX="1~test~NDX~100~1";'
        result = parse_us_index_quote(raw, "us.NDX")
        assert result["price"] == 0.0

    @pytest.mark.asyncio
    async def test_analyze_qdii_with_mock(self):
        """Analyze a QDII ETF with mocked HTTP."""
        fields = [""] * 50
        fields[3] = "18500.50"
        fields[32] = "1.25"
        fields[47] = "19000.00"
        fields[48] = "15000.00"
        us_raw = 'v_us.NDX="' + "~".join(fields) + '";'
        etf_raw = 'v_f_021778="1~广发纳斯达克100联接C~021778~1.8500~0.01~1.8600~10000~5000~5000~1.8500~9999~";'

        class MockResponse:
            def __init__(self, raw_text):
                self._text = raw_text
            async def text(self, encoding=None):
                return self._text
            async def json(self, content_type=None):
                return {}
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        def mock_get(url, **kwargs):
            if "us.NDX" in str(url):
                return MockResponse(us_raw)
            return MockResponse(etf_raw)

        mock_session = MagicMock()
        mock_session.get = mock_get

        result = await analyze_qdii("f_021778", session=mock_session)
        assert result is not None
        assert isinstance(result, QDIISnapshot)
        assert result.index_code == "us.NDX"
        assert result.index_name == "纳斯达克100"
        assert result.index_value == 18500.50
        assert "纳斯达克100" in result.interpretation

    @pytest.mark.asyncio
    async def test_non_qdii_etf_returns_none(self):
        """Non-QDII code should return None."""
        mock_session = AsyncMock()
        result = await analyze_qdii("sh600519", session=mock_session)
        assert result is None


# ── Decision Framework Tests ───────────────────────────────────────────────


class TestDecision:
    """Test position sizing decision framework."""

    def test_assess_position_near_high(self):
        """Price near 52w high should show high position_in_range."""
        pos = assess_position(190.0, 200.0, 100.0)
        assert pos.position_in_range == pytest.approx(0.9, abs=0.01)
        assert pos.pct_from_high < 0  # below high
        assert "高位" in pos.interpretation

    def test_assess_position_near_low(self):
        """Price near 52w low should show low position_in_range."""
        pos = assess_position(110.0, 200.0, 100.0)
        assert pos.position_in_range == pytest.approx(0.1, abs=0.01)
        assert "低位" in pos.interpretation

    def test_assess_position_mid(self):
        """Price in the middle."""
        pos = assess_position(150.0, 200.0, 100.0)
        assert pos.position_in_range == pytest.approx(0.5, abs=0.01)

    def test_assess_position_with_pe(self):
        """High PE should be noted in interpretation."""
        pos = assess_position(150.0, 200.0, 100.0, pe=60.0)
        assert "PE" in pos.interpretation
        assert "偏高" in pos.interpretation

    def test_assess_position_low_pe(self):
        """Low PE should be noted."""
        pos = assess_position(150.0, 200.0, 100.0, pe=10.0)
        assert "偏低" in pos.interpretation

    def test_assess_position_invalid_range(self):
        """High <= Low should handle gracefully."""
        pos = assess_position(100.0, 100.0, 100.0)
        assert pos.position_in_range == 0.5

    def test_scenarios_sum_to_100(self):
        """All scenario probabilities must sum to 1.0."""
        for pos_val in [0.1, 0.3, 0.5, 0.7, 0.9]:
            pos = assess_position(pos_val * 200, 200.0, 0.0)
            scenarios = build_scenarios(pos)
            total = sum(s.probability for s in scenarios)
            assert abs(total - 1.0) < 0.01, f"At pos={pos_val}, probabilities sum to {total}"

    def test_scenarios_near_high_bearish(self):
        """Near high should have more weight on correction scenarios."""
        pos = assess_position(190.0, 200.0, 100.0)
        scenarios = build_scenarios(pos)
        bearish = sum(s.probability for s in scenarios if s.expected_return_pct < 0)
        bullish = sum(s.probability for s in scenarios if s.expected_return_pct > 0)
        assert bearish > bullish

    def test_scenarios_near_low_bullish(self):
        """Near low should have more weight on recovery scenarios."""
        pos = assess_position(105.0, 200.0, 100.0)
        scenarios = build_scenarios(pos)
        bullish = sum(s.probability for s in scenarios if s.expected_return_pct > 0)
        bearish = sum(s.probability for s in scenarios if s.expected_return_pct < 0)
        assert bullish > bearish

    def test_expected_return_calculation(self):
        """Expected return should be probability-weighted."""
        analysis = compute_decision(
            name="测试股票",
            price=150.0,
            high_52w=200.0,
            low_52w=100.0,
            cost_price=140.0,
            shares=100,
        )
        manual_er = sum(s.probability * s.expected_return_pct for s in analysis.scenarios)
        assert analysis.expected_return == pytest.approx(manual_er, abs=0.01)

    def test_decision_reduce_at_high(self):
        """High position with losing expected return → reduce."""
        analysis = compute_decision(
            name="高位股",
            price=195.0,
            high_52w=200.0,
            low_52w=100.0,
            cost_price=150.0,
            shares=100,
        )
        assert analysis.recommendation in ("reduce", "hold")

    def test_decision_stop_loss_at_low(self):
        """Deep loss near low → stop_loss."""
        analysis = compute_decision(
            name="深套股",
            price=85.0,
            high_52w=200.0,
            low_52w=80.0,
            cost_price=150.0,
            shares=100,
        )
        assert analysis.recommendation == "stop_loss"
        assert "止损" in analysis.reasoning

    def test_decision_add_at_low(self):
        """Low position with good expected return → add."""
        analysis = compute_decision(
            name="低位股",
            price=105.0,
            high_52w=200.0,
            low_52w=100.0,
            cost_price=110.0,
            shares=100,
        )
        assert analysis.recommendation in ("add", "hold")

    def test_format_decision_report(self):
        """Report should contain key information."""
        analysis = compute_decision(
            name="贵州茅台",
            price=150.0,
            high_52w=200.0,
            low_52w=100.0,
            cost_price=140.0,
            shares=100,
        )
        report = format_decision_report(analysis)
        assert "贵州茅台" in report
        assert "加减仓决策" in report
        assert "期望收益" in report
        assert "风险收益比" in report
        assert any(kw in report for kw in ["持有", "减仓", "加仓", "止损"])


# ── Risk Control Tests ─────────────────────────────────────────────────────


class TestRisk:
    """Test risk control checklist."""

    def test_stop_loss_triggered(self):
        """Loss exceeding threshold should trigger alert."""
        holding = {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0}
        result = check_stop_loss(holding, current_price=85.0)
        assert result["status"] == "alert"
        assert "止损" in result["detail"]

    def test_stop_loss_not_triggered(self):
        """Normal loss should be ok."""
        holding = {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0}
        result = check_stop_loss(holding, current_price=96.0)
        assert result["status"] == "ok"

    def test_stop_loss_warning_zone(self):
        """Loss approaching threshold should warn."""
        holding = {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0}
        result = check_stop_loss(holding, current_price=93.0)
        assert result["status"] == "warning"
        assert "接近" in result["detail"]

    def test_take_profit_warning(self):
        """Profit exceeding threshold should warn."""
        holding = {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0}
        result = check_stop_loss(holding, current_price=135.0)
        assert result["status"] == "warning"
        assert "止盈" in result["detail"]

    def test_position_over_limit(self):
        """Position exceeding 25% should alert."""
        holding = {"code": "sh600519", "name": "贵州茅台", "shares": 1000, "cost_price": 100.0}
        result = check_position_concentration(holding, current_price=100.0, total_portfolio_value=300000.0)
        assert result["status"] == "alert"
        assert "超过上限" in result["detail"]

    def test_position_under_limit(self):
        """Normal position should be ok."""
        holding = {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0}
        result = check_position_concentration(holding, current_price=100.0, total_portfolio_value=500000.0)
        assert result["status"] == "ok"

    def test_position_near_limit(self):
        """Position approaching limit should warn."""
        holding = {"code": "sh600519", "name": "贵州茅台", "shares": 2100, "cost_price": 100.0}
        result = check_position_concentration(holding, current_price=100.0, total_portfolio_value=1000000.0)
        assert result["status"] == "warning"
        assert "接近上限" in result["detail"]

    def test_discipline_chase_high(self):
        """Buying at 52w high should alert."""
        holding = {"name": "贵州茅台"}
        checks = check_discipline(holding, price_position=0.9, action="buy")
        assert any(c["status"] == "alert" for c in checks)
        assert any("追高" in c["detail"] for c in checks)

    def test_discipline_panic_sell(self):
        """Selling at 52w low should alert."""
        holding = {"name": "贵州茅台"}
        checks = check_discipline(holding, price_position=0.1, action="sell")
        assert any(c["status"] == "alert" for c in checks)
        assert any("恐慌" in c["detail"] for c in checks)

    def test_discipline_normal_hold(self):
        """Holding in mid-range is fine."""
        holding = {"name": "贵州茅台"}
        checks = check_discipline(holding, price_position=0.5, action="hold")
        assert all(c["status"] == "ok" for c in checks)

    def test_run_risk_check_full(self):
        """Full risk check should process all holdings."""
        portfolio = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0},
            {"code": "sz300001", "name": "测试股票A", "shares": 500, "cost_price": 40.0},
        ]
        prices = {"sh600519": 110.0, "sz300001": 30.0}
        result = run_risk_check(portfolio, prices, total_value=26000.0)
        assert isinstance(result, RiskCheckResult)
        assert len(result.checks) == 4  # 2 stop-loss + 2 concentration
        assert result.overall_risk in ("low", "medium", "high", "critical")

    def test_run_risk_check_critical(self):
        """Multiple alerts should be critical."""
        portfolio = [
            {"code": f"sh60000{i}", "name": f"Stock{i}", "shares": 10000, "cost_price": 100.0}
            for i in range(5)
        ]
        prices = {f"sh60000{i}": 80.0 for i in range(5)}  # all down 20%
        total = sum(10000 * 80.0 for _ in range(5))
        result = run_risk_check(portfolio, prices, total_value=total)
        assert result.overall_risk in ("high", "critical")
        assert len(result.alerts) > 0

    def test_run_risk_check_custom_rules(self):
        """Custom rules should override defaults."""
        portfolio = [
            {"code": "sh600519", "name": "贵州茅台", "shares": 100, "cost_price": 100.0},
        ]
        custom_rules = {**DEFAULT_RULES, "stop_loss_pct": -5.0}
        result = run_risk_check(portfolio, {"sh600519": 93.0}, total_value=10000.0, rules=custom_rules)
        assert any(c["status"] == "alert" for c in result.checks)

    def test_format_risk_report(self):
        """Report should contain key sections."""
        result = RiskCheckResult(
            checks=[{"name": "测试", "status": "ok", "detail": "正常"}],
            overall_risk="low",
            alerts=[],
            recommendations=["风控检查通过"],
        )
        report = format_risk_report(result)
        assert "风控检查报告" in report
        assert "LOW" in report
        assert "测试" in report
        assert "风控检查通过" in report

    def test_format_risk_report_with_alerts(self):
        """Report with alerts should show them."""
        result = RiskCheckResult(
            checks=[{"name": "止损检查", "status": "alert", "detail": "亏损15%"}],
            overall_risk="high",
            alerts=["🔴 亏损15%，已触发止损线"],
            recommendations=["建议立即处理"],
        )
        report = format_risk_report(result)
        assert "HIGH" in report
        assert "警报" in report
        assert "止损" in report


# ── Eastmoney Sync Tests ────────────────────────────────────────────────────


class TestEastmoneyCookieManagement:
    """Test cookie save/load/freshness."""

    def test_save_and_load_cookies(self, tmp_path):
        from secretary.wealth.eastmoney_sync import load_cookies, save_cookies
        path = tmp_path / "cookies.json"
        cookies = [{"name": "token", "value": "abc123", "domain": ".18.cn"}]
        save_cookies(cookies, {"key": "value"}, path)
        loaded = load_cookies(path)
        assert loaded is not None
        assert loaded["cookies"][0]["name"] == "token"
        assert loaded["localStorage"]["key"] == "value"

    def test_load_missing_file(self, tmp_path):
        from secretary.wealth.eastmoney_sync import load_cookies
        result = load_cookies(tmp_path / "nonexistent.json")
        assert result is None

    def test_cookie_fresh(self, tmp_path):
        from secretary.wealth.eastmoney_sync import is_cookie_fresh, save_cookies
        path = tmp_path / "cookies.json"
        save_cookies([{"name": "t", "value": "v"}], path=path)
        assert is_cookie_fresh(path, max_age_hours=1) is True

    def test_cookie_stale(self, tmp_path):
        import json
        from datetime import datetime, timedelta

        from secretary.wealth.eastmoney_sync import is_cookie_fresh
        path = tmp_path / "cookies.json"
        data = {
            "cookies": [],
            "localStorage": {},
            "saved_at": (datetime.now() - timedelta(hours=10)).isoformat(),
        }
        path.write_text(json.dumps(data))
        assert is_cookie_fresh(path, max_age_hours=4) is False


class TestEastmoneyParsing:
    """Test API response parsing."""

    def test_parse_holding(self):
        from secretary.wealth.eastmoney_sync import _parse_holding
        item = {
            "Zqdm": "601398", "Zqmc": "工商银行",
            "Zqsl": "4500", "Kysl": "4500", "Cbjg": "7.16",
            "Zxjg": "7.25", "Zxsz": "32625.0", "Ykje": "405.0", "Ykbl": "1.26",
        }
        h = _parse_holding(item)
        assert h is not None
        assert h.code == "601398"
        assert h.name == "工商银行"
        assert h.shares == 4500
        assert h.cost_price == 7.16

    def test_parse_holding_empty(self):
        from secretary.wealth.eastmoney_sync import _parse_holding
        assert _parse_holding({}) is None

    def test_parse_assets(self):
        from secretary.wealth.eastmoney_sync import _parse_assets
        data = {"Zzc": "100000", "Zsz": "55000", "Kyzj": "45000", "Djzj": "0", "Yk": "500", "Ccyk": "2000"}
        a = _parse_assets(data)
        assert a.total_assets == 100000.0
        assert a.available_cash == 45000.0

    def test_parse_trade(self):
        from secretary.wealth.eastmoney_sync import _parse_trade
        item = {
            "Cjrq": "20260713", "Cjsj": "10:30:00",
            "Zqdm": "601398", "Zqmc": "工商银行",
            "Mmlb": "买入", "Cjjg": "7.27", "Cjsl": "3500", "Cjje": "25445.0",
        }
        t = _parse_trade(item)
        assert t is not None
        assert t.code == "601398"
        assert t.action == "buy"
        assert t.shares == 3500

    def test_parse_trade_sell(self):
        from secretary.wealth.eastmoney_sync import _parse_trade
        item = {"Zqdm": "300001", "Zqmc": "测试股票A", "Mmlb": "卖出", "Cjjg": "80.79", "Cjsl": "200", "Cjje": "16158.0", "Cjrq": "20260713", "Cjsj": "14:00:00"}
        t = _parse_trade(item)
        assert t.action == "sell"


class TestSyncToPortfolio:
    """Test portfolio sync logic."""

    def test_sync_updates_existing(self, tmp_path):
        import json

        from secretary.wealth.eastmoney_sync import AccountAssets, AccountHolding, sync_to_portfolio

        portfolio_path = tmp_path / "portfolio.json"
        portfolio_path.write_text(json.dumps({
            "holdings": [
                {"name": "工商银行", "code": "sh601398", "shares": 1000, "cost_price": 7.0, "type": "stock"},
            ],
            "cash": {"total": 10000},
            "closed_positions": [],
            "pending_actions": [],
        }))

        holdings = [AccountHolding(code="601398", name="工商银行", shares=4500, available_shares=4500,
                                    cost_price=7.16, current_price=7.25, market_value=32625, profit=405, profit_pct=1.26)]
        assets = AccountAssets(total_assets=50000, available_cash=17375)

        changes = sync_to_portfolio(holdings, assets, portfolio_path)
        assert "601398" in str(changes["updated"])

        data = json.loads(portfolio_path.read_text())
        h = next(h for h in data["holdings"] if h["code"] == "sh601398")
        assert h["shares"] == 4500
        assert data["cash"]["total"] == 17375

    def test_sync_adds_new(self, tmp_path):
        import json

        from secretary.wealth.eastmoney_sync import AccountHolding, sync_to_portfolio

        portfolio_path = tmp_path / "portfolio.json"
        portfolio_path.write_text(json.dumps({
            "holdings": [], "cash": {"total": 0}, "closed_positions": [], "pending_actions": [],
        }))

        holdings = [AccountHolding(code="601398", name="工商银行", shares=100, available_shares=100,
                                    cost_price=7.0, current_price=7.2, market_value=720, profit=20, profit_pct=2.86)]

        changes = sync_to_portfolio(holdings, None, portfolio_path)
        assert len(changes["added"]) == 1
        data = json.loads(portfolio_path.read_text())
        assert len(data["holdings"]) == 1

    def test_market_code_conversion(self):
        from secretary.wealth.eastmoney_sync import _market_code
        assert _market_code("601398") == "sh601398"
        assert _market_code("300442") == "sz300442"
        assert _market_code("sh601398") == "sh601398"


class TestFormatSyncReport:
    """Test report formatting."""

    def test_format_error(self):
        from secretary.wealth.eastmoney_sync import SyncResult, format_sync_report
        result = SyncResult(error="No cookies")
        report = format_sync_report(result)
        assert "失败" in report

    def test_format_success(self):
        from secretary.wealth.eastmoney_sync import (
            AccountAssets,
            AccountHolding,
            SyncResult,
            format_sync_report,
        )
        result = SyncResult(
            assets=AccountAssets(total_assets=100000, market_value=50000, available_cash=50000),
            holdings=[AccountHolding(code="601398", name="工商银行", shares=100, available_shares=100,
                                      cost_price=7.0, current_price=7.2, market_value=720, profit=20, profit_pct=2.86)],
        )
        report = format_sync_report(result)
        assert "工商银行" in report
        assert "总资产" in report
