"""Wealth intents — portfolio snapshot, market overview, QDII."""

from __future__ import annotations

import json
import os
from pathlib import Path

from secretary.gateway.intents.base import IntentContext

# ── Exact keywords (no regex, no substring match) ────────────────────────
_PORTFOLIO_KW = {
    "持仓",
    "查持仓",
    "看看持仓",
    "查一下持仓",
    "看一下持仓",
    "持仓情况",
    "持仓怎么样",
    "持仓如何",
    "市值",
    "市值多少",
    "市值怎么样",
    "市值如何",
    "盈亏",
    "盈亏多少",
    "盈亏怎么样",
    "盈亏如何",
    "收益",
    "收益多少",
    "收益怎么样",
    "账户",
    "账户怎么样",
    "账户情况",
    "账户多少",
    "我的股票",
    "我的基金",
    "今天赚了多少",
    "今天亏了多少",
    "今天赚了",
    "今天亏了",
}

_MARKET_KW = {
    "大盘",
    "行情",
    "沪深300",
    "沪深 300",
    "上证指数",
    "创业板指",
    "创业板",
    "指数怎么样",
    "指数如何",
    "指数",
}

_QDII_KW = {"qdii", "QDII", "溢价", "套利"}


def _load_portfolio(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class PortfolioHandler:
    name = "portfolio"
    keywords = _PORTFOLIO_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        path = getattr(getattr(ctx.config, "data", None), "portfolio_path", None)
        if not path or not Path(path).exists():
            return None
        data = _load_portfolio(path)
        holdings = data.get("holdings", [])
        cash = data.get("cash", {})
        cash_total = cash.get("total") or cash.get("available") or cash.get("amount") or 0

        lines = []
        for h in holdings:
            if h.get("status") == "closed":
                continue
            name = h.get("name", "?")
            htype = h.get("type", "")
            if htype == "fund_otc":
                principal = h.get("principal") or 0
                lines.append(f"  {name}: 本金{principal:.0f}")
            elif htype == "gold_accumulate":
                grams = h.get("grams") or 0
                cost = h.get("cost_per_gram") or 0
                lines.append(f"  {name}: {grams}g @{cost:.0f}/g")
            else:
                shares = h.get("shares") or 0
                cost = h.get("cost_price") or 0
                lines.append(f"  {name}: {shares}股 @{cost:.3f}")

        result = f"💼 持仓 {len([h for h in holdings if h.get('status') != 'closed'])} 只:\n"
        result += "\n".join(lines)
        result += f"\n💰 现金: {cash_total:.0f}"
        result += "\n（以上为成本价，实时盈亏需 Agent 查行情）"
        return result


class MarketHandler:
    name = "market"
    keywords = _MARKET_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        try:
            import asyncio

            from secretary.wealth.sector_analysis import fetch_index_quotes

            loop = asyncio.get_running_loop()
            quotes = await loop.run_in_executor(
                None, fetch_index_quotes, ["sh000300", "sh000001", "sz399006"]
            )
            if not quotes:
                return None
            lines = ["📊 大盘："]
            name_map = {"sh000300": "沪深300", "sh000001": "上证", "sz399006": "创业板"}
            for code, q in list(quotes.items())[:3]:
                name = name_map.get(code, code)
                chg = q.get("change_pct")
                price = q.get("price")
                if chg is not None and price is not None:
                    emoji = "🔴" if chg >= 0 else "🟢"
                    lines.append(f"  {emoji} {name} {price} ({chg:+.2f}%)")
            return "\n".join(lines) if len(lines) > 1 else None
        except Exception:
            return None


class QdiiHandler:
    name = "qdii"
    keywords = _QDII_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str | None:
        """Fetch QDII premium/discount analysis for user's holdings."""
        import logging

        logger = logging.getLogger(__name__)

        try:
            import json
            from pathlib import Path

            from secretary.wealth.qdii import analyze_all_qdii

            _portfolio_env = os.environ.get("SECRETARY_PORTFOLIO_PATH", "").strip()
            if not _portfolio_env:
                return None
            portfolio_path = Path(_portfolio_env)
            if not portfolio_path.is_file():
                return None

            # Load raw portfolio data (list of holding dicts)
            with open(portfolio_path, encoding="utf-8") as f:
                data = json.load(f)
            holdings = data.get("holdings", [])

            if not holdings:
                return None

            # Analyze QDII holdings
            results = await analyze_all_qdii(holdings)
            if not results:
                return "你当前没有QDII持仓。"

            lines = ["📊 QDII 溢价分析："]
            for r in results:
                if r.tracking_diff > 5:
                    emoji = "🔴"  # High premium
                elif r.tracking_diff > 2:
                    emoji = "🟡"  # Moderate premium
                elif r.tracking_diff < -2:
                    emoji = "🟢"  # Discount
                else:
                    emoji = "⚪"  # Normal

                lines.append(
                    f"{emoji} {r.etf_name}: {r.tracking_diff:+.1f}% "
                    f"(ETF {r.etf_price:.3f} vs {r.index_name} {r.index_value:.0f})"
                )
                if r.interpretation:
                    lines.append(f"   {r.interpretation}")

            lines.append("\n💡 高溢价(>5%)谨慎买入，折价(<-2%)可关注。")
            return "\n".join(lines)

        except Exception as e:
            logger.warning("QDII analysis failed: %s", e)
            return None


HANDLERS = [PortfolioHandler(), MarketHandler(), QdiiHandler()]
