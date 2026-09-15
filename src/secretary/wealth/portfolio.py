"""Portfolio tracking — read holdings, fetch prices, compute P&L."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Tencent stock price API
TENCENT_QUOTE_URL = "http://qt.gtimg.cn/q={codes}"


# ── Data Classes ────────────────────────────────────────────────────────────


@dataclass
class Transaction:
    """A single buy/sell transaction."""

    date: str = ""
    action: str = ""  # buy/sell
    shares: float = 0.0
    price: float = 0.0
    amount: float = 0.0
    note: str = ""
    grams: float = 0.0
    cost: float = 0.0
    per_gram: float = 0.0
    realized_profit: float = 0.0
    cash_source: str = ""


@dataclass
class PendingRedemption:
    """Pending redemption for OTC fund."""

    date_submitted: str = ""
    shares: float = 0.0
    confirm_date: str = ""
    status: str = ""


@dataclass
class Holding:
    """Single stock/ETF/fund/gold holding."""

    code: str
    name: str
    shares: float = 0.0
    cost_price: float = 0.0
    current_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    market_value: float = 0.0
    # Extended fields
    type: str = "stock"  # stock/etf/fund_otc/gold_accumulate
    buy_date: str = ""
    note: str = ""
    transactions: list[Transaction] = field(default_factory=list)
    # Fund-specific
    nav: float = 0.0
    nav_date: str = ""
    pending_redemption: PendingRedemption | None = None
    # Gold-specific
    grams: float = 0.0
    cost_total: float = 0.0
    cost_per_gram: float = 0.0


@dataclass
class Cash:
    """Cash position."""

    total: float = 0.0
    note: str = ""


@dataclass
class ClosedPosition:
    """A closed (fully sold) position."""

    name: str = ""
    code: str = ""
    close_date: str = ""
    shares: float = 0.0
    cost_price: float = 0.0
    total_cost: float = 0.0
    total_revenue: float = 0.0
    profit_loss: float = 0.0
    return_rate: float = 0.0
    holding_days: int = 0
    note: str = ""


@dataclass
class PendingAction:
    """A pending action (e.g. pending redemption, pending buy)."""

    action: str = ""
    target: str = ""
    details: str = ""
    date: str = ""


@dataclass
class PortfolioData:
    """Full portfolio data: holdings + cash + closed positions + pending actions."""

    holdings: list[Holding] = field(default_factory=list)
    cash: Cash = field(default_factory=Cash)
    closed_positions: list[ClosedPosition] = field(default_factory=list)
    pending_actions: list[PendingAction] = field(default_factory=list)


@dataclass
class PortfolioSnapshot:
    """Portfolio summary at a point in time."""

    total_cost: float = 0.0
    total_market_value: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    holdings: list[Holding] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    significant_moves: list[str] = field(default_factory=list)
    cash: Cash = field(default_factory=Cash)
    closed_positions: list[ClosedPosition] = field(default_factory=list)
    pending_actions: list[PendingAction] = field(default_factory=list)
    total_wealth: float = 0.0  # market_value + cash


# ── Loading ─────────────────────────────────────────────────────────────────


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert value to float, handling None and non-numeric strings."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _parse_transaction(raw: dict[str, Any]) -> Transaction:
    """Parse a raw dict into a Transaction dataclass."""
    return Transaction(
        date=raw.get("date", ""),
        action=raw.get("action", ""),
        shares=float(raw.get("shares") or 0),
        price=_safe_float(raw.get("price", 0)),
        amount=_safe_float(raw.get("amount", 0)),
        note=raw.get("note", ""),
        grams=_safe_float(raw.get("grams", 0)),
        cost=_safe_float(raw.get("cost", 0)),
        per_gram=_safe_float(raw.get("per_gram", 0)),
        realized_profit=_safe_float(raw.get("realized_profit", 0)),
        cash_source=raw.get("cash_source", ""),
    )


def _parse_pending_redemption(raw: dict[str, Any] | None) -> PendingRedemption | None:
    """Parse a raw dict into a PendingRedemption dataclass."""
    if not raw:
        return None
    return PendingRedemption(
        date_submitted=raw.get("date_submitted", ""),
        shares=float(raw.get("shares") or 0),
        confirm_date=raw.get("confirm_date", ""),
        status=raw.get("status", ""),
    )


def _parse_holding(raw: dict[str, Any]) -> Holding:
    """Parse a raw dict into a Holding dataclass."""
    transactions = [_parse_transaction(t) for t in raw.get("transactions", [])]
    pending_redemption = _parse_pending_redemption(raw.get("pending_redemption"))

    cost_price = raw.get("cost_price")
    cost_total = raw.get("cost_total")
    grams = raw.get("grams")

    return Holding(
        code=raw.get("code", ""),
        name=raw.get("name", ""),
        shares=float(raw.get("shares") or 0),
        cost_price=float(cost_price) if cost_price is not None else 0.0,
        type=raw.get("type", "stock"),
        buy_date=raw.get("buy_date", ""),
        note=raw.get("note", ""),
        transactions=transactions,
        nav=_safe_float(raw.get("nav", 0)),
        nav_date=raw.get("nav_date", ""),
        pending_redemption=pending_redemption,
        grams=float(grams) if grams is not None else 0.0,
        cost_total=float(cost_total) if cost_total is not None else 0.0,
        cost_per_gram=_safe_float(raw.get("cost_per_gram", 0)),
    )


def _parse_closed_position(raw: dict[str, Any]) -> ClosedPosition:
    """Parse a raw dict into a ClosedPosition dataclass."""
    return ClosedPosition(
        name=raw.get("name", ""),
        code=raw.get("code", ""),
        close_date=raw.get("close_date", ""),
        shares=float(raw.get("shares") or 0),
        cost_price=_safe_float(raw.get("cost_price", 0)),
        total_cost=_safe_float(raw.get("total_cost", 0)),
        total_revenue=_safe_float(raw.get("total_revenue", 0)),
        profit_loss=_safe_float(raw.get("profit_loss")),
        return_rate=_safe_float(raw.get("return_rate", 0)),
        holding_days=int(raw.get("holding_days", 0)),
        note=raw.get("note", ""),
    )


def _parse_pending_action(raw: dict[str, Any]) -> PendingAction:
    """Parse a raw dict into a PendingAction dataclass."""
    return PendingAction(
        action=raw.get("action", ""),
        target=raw.get("target", ""),
        details=raw.get("details", ""),
        date=raw.get("date", ""),
    )


def load_portfolio(path: str | Path) -> list[dict[str, Any]]:
    """Load portfolio holdings from a JSON file (legacy format).

    Returns list of holding dicts for backward compatibility.
    Use load_portfolio_data() for the full format.
    """
    portfolio_path = Path(path)
    if not portfolio_path.exists():
        logger.warning("Portfolio file not found: %s", path)
        return []
    with open(portfolio_path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "holdings" in data:
        return data["holdings"]
    logger.error("Portfolio file has unexpected format: %s", path)
    return []


def load_portfolio_data(path: str | Path) -> PortfolioData:
    """Load full portfolio data from a JSON file.

    Supports two formats:
    1. New format: {"holdings": [...], "cash": {...}, "closed_positions": [...], "pending_actions": [...]}
    2. Legacy format: [{holding}, ...] → wraps into PortfolioData with empty cash/closed/pending
    """
    portfolio_path = Path(path)
    if not portfolio_path.exists():
        logger.warning("Portfolio file not found: %s", path)
        return PortfolioData()

    with open(portfolio_path, encoding="utf-8") as f:
        data = json.load(f)

    # Legacy format: plain list of holdings
    if isinstance(data, list):
        holdings = [_parse_holding(h) for h in data]
        return PortfolioData(holdings=holdings)

    # New format: dict with "holdings" key
    if isinstance(data, dict) and "holdings" in data:
        holdings = [_parse_holding(h) for h in data.get("holdings", [])]

        raw_cash = data.get("cash", {})
        cash = Cash(
            total=float(raw_cash.get("total", 0)),
            note=raw_cash.get("note", ""),
        ) if isinstance(raw_cash, dict) else Cash()

        closed_positions = [
            _parse_closed_position(cp) for cp in data.get("closed_positions", [])
        ]
        pending_actions = [
            _parse_pending_action(pa) for pa in data.get("pending_actions", [])
        ]

        return PortfolioData(
            holdings=holdings,
            cash=cash,
            closed_positions=closed_positions,
            pending_actions=pending_actions,
        )

    logger.error("Portfolio file has unexpected format: %s", path)
    return PortfolioData()


def get_all_stock_codes(portfolio_data: PortfolioData) -> list[str]:
    """Extract codes of holdings that need real-time price monitoring.

    Returns codes for type=stock and type=etf (excludes fund_otc, gold_accumulate).
    Also skips holdings with 0 shares (fully sold).
    """
    return [
        h.code for h in portfolio_data.holdings
        if h.type in ("stock", "etf") and h.shares > 0 and h.code
    ]


# ── Price Fetching ──────────────────────────────────────────────────────────


def parse_tencent_prices(raw_text: str) -> dict[str, float]:
    """Parse Tencent quote API response into code -> price mapping.

    The response format is one line per stock:
        v_sh600519="1~贵州茅台~600519~1800.00~...~..."
    """
    prices: dict[str, float] = {}
    for line in raw_text.strip().split("\n"):
        line = line.strip().rstrip(";")
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Extract code from v_shXXXXXX
        code_part = key.split("_", 1)[-1] if "_" in key else key
        # Parse the quote string
        value = value.strip('"')
        parts = value.split("~")
        if len(parts) > 3:
            try:
                price = float(parts[3])
                if price > 0:
                    prices[code_part] = price
            except (ValueError, IndexError):
                continue
    return prices


async def fetch_prices(
    codes: list[str],
    session: aiohttp.ClientSession | None = None,
) -> dict[str, float]:
    """Fetch current prices from Tencent API.

    Args:
        codes: list of stock codes like ["sh600519", "sz300442"]
        session: optional aiohttp session to reuse

    Returns:
        dict of code -> current price
    """
    if not codes:
        return {}

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        codes_str = ",".join(codes)
        url = TENCENT_QUOTE_URL.format(codes=codes_str)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            raw = await resp.text(encoding="gbk")
        return parse_tencent_prices(raw)
    except Exception:
        logger.exception("Failed to fetch prices from Tencent API")
        return {}
    finally:
        if own_session:
            await session.close()


# ── Computation ─────────────────────────────────────────────────────────────


def _compute_holding(
    raw: dict[str, Any],
    prices: dict[str, float],
) -> tuple[Holding, float, float, str | None]:
    """Compute a single holding's P&L.

    Returns (holding, cost, market_value, significant_move_or_none).
    """
    code = raw.get("code", "")
    name = raw.get("name", code)
    shares = _safe_float(raw.get("shares", 0))
    cost_price = _safe_float(raw.get("cost_price", 0)) if raw.get("cost_price") is not None else 0.0
    h_type = raw.get("type", "stock")

    if h_type == "fund_otc":
        # OTC fund: use NAV, no real-time price
        nav = _safe_float(raw.get("nav", 0))
        current_price = nav
        market_value = shares * nav if nav > 0 else 0.0
        cost = shares * cost_price if cost_price > 0 else 0.0
    elif h_type == "gold_accumulate":
        # Gold accumulate: use grams * cost_per_gram
        grams = _safe_float(raw.get("grams", 0))
        cost_total = _safe_float(raw.get("cost_total", 0))
        cost_per_gram = _safe_float(raw.get("cost_per_gram", 0))
        current_price = cost_per_gram  # no real-time gold price API
        market_value = grams * cost_per_gram
        cost = cost_total
        # Override shares for display
        shares = grams
    else:
        # stock/etf: use real-time price
        current_price = prices.get(code, cost_price)
        market_value = shares * current_price
        cost = shares * cost_price

    pnl = market_value - cost
    pnl_pct = (pnl / cost * 100) if cost != 0 else 0.0

    pending_redemption = _parse_pending_redemption(raw.get("pending_redemption"))
    transactions = [_parse_transaction(t) for t in raw.get("transactions", [])]

    holding = Holding(
        code=code,
        name=name,
        shares=float(raw.get("shares") or 0),
        cost_price=cost_price,
        current_price=current_price,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        market_value=round(market_value, 2),
        type=h_type,
        buy_date=raw.get("buy_date", ""),
        note=raw.get("note", ""),
        transactions=transactions,
        nav=_safe_float(raw.get("nav", 0)),
        nav_date=raw.get("nav_date", ""),
        pending_redemption=pending_redemption,
        grams=_safe_float(raw.get("grams", 0)),
        cost_total=_safe_float(raw.get("cost_total", 0)) if raw.get("cost_total") is not None else 0.0,
        cost_per_gram=_safe_float(raw.get("cost_per_gram", 0)),
    )

    significant_move = None
    if cost > 0 and abs(pnl_pct) >= 3.0:
        direction = "涨" if pnl_pct > 0 else "跌"
        significant_move = f"{name}{direction}{abs(pnl_pct):.1f}%"

    return holding, cost, market_value, significant_move


def compute_portfolio(
    holdings_data: list[dict[str, Any]],
    prices: dict[str, float],
    move_threshold: float = 3.0,
) -> PortfolioSnapshot:
    """Compute portfolio P&L from holdings data and current prices.

    Args:
        holdings_data: list of holding dicts (from JSON)
        prices: dict of code -> current price
        move_threshold: percentage threshold for significant moves (default 3%)

    Returns:
        PortfolioSnapshot with computed values
    """
    holdings: list[Holding] = []
    total_cost = 0.0
    total_market_value = 0.0
    significant_moves: list[str] = []

    for h in holdings_data:
        # Skip zero-share holdings (fully sold) for P&L computation
        h_type = h.get("type", "stock")
        shares = float(h.get("shares", 0))
        if h_type != "gold_accumulate" and shares <= 0:
            continue
        if h_type == "gold_accumulate" and float(h.get("grams", 0)) <= 0:
            continue

        holding, cost, mv, move = _compute_holding(h, prices)
        holdings.append(holding)
        total_cost += cost
        total_market_value += mv
        if move:
            significant_moves.append(move)

    total_pnl = total_market_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost != 0 else 0.0

    return PortfolioSnapshot(
        total_cost=round(total_cost, 2),
        total_market_value=round(total_market_value, 2),
        total_pnl=round(total_pnl, 2),
        total_pnl_pct=round(total_pnl_pct, 2),
        holdings=holdings,
        significant_moves=significant_moves,
    )


def compute_portfolio_from_data(
    portfolio_data: PortfolioData,
    prices: dict[str, float],
    move_threshold: float = 3.0,
) -> PortfolioSnapshot:
    """Compute portfolio snapshot from PortfolioData (full format).

    Args:
        portfolio_data: PortfolioData with holdings, cash, closed positions, pending actions
        prices: dict of code -> current price
        move_threshold: significant move threshold (default 3%)

    Returns:
        PortfolioSnapshot with computed values including cash and total wealth
    """
    holdings_raw = [
        {
            "code": h.code,
            "name": h.name,
            "shares": h.shares,
            "cost_price": h.cost_price,
            "type": h.type,
            "nav": h.nav,
            "nav_date": h.nav_date,
            "grams": h.grams,
            "cost_total": h.cost_total,
            "cost_per_gram": h.cost_per_gram,
            "note": h.note,
            "buy_date": h.buy_date,
            "transactions": [
                {
                    "date": t.date,
                    "action": t.action,
                    "shares": t.shares,
                    "price": t.price,
                    "amount": t.amount,
                    "note": t.note,
                    "grams": t.grams,
                    "cost": t.cost,
                    "per_gram": t.per_gram,
                    "realized_profit": t.realized_profit,
                    "cash_source": t.cash_source,
                }
                for t in h.transactions
            ],
            "pending_redemption": {
                "date_submitted": h.pending_redemption.date_submitted,
                "shares": h.pending_redemption.shares,
                "confirm_date": h.pending_redemption.confirm_date,
                "status": h.pending_redemption.status,
            } if h.pending_redemption else None,
        }
        for h in portfolio_data.holdings
    ]

    snapshot = compute_portfolio(holdings_raw, prices, move_threshold)
    snapshot.cash = portfolio_data.cash
    snapshot.closed_positions = portfolio_data.closed_positions
    snapshot.pending_actions = portfolio_data.pending_actions
    snapshot.total_wealth = round(snapshot.total_market_value + portfolio_data.cash.total, 2)
    return snapshot


# ── Pipeline ────────────────────────────────────────────────────────────────


async def get_portfolio_snapshot(
    portfolio_path: str | Path,
    session: aiohttp.ClientSession | None = None,
    move_threshold: float = 3.0,
) -> PortfolioSnapshot:
    """Full pipeline: load portfolio → fetch prices → compute P&L.

    Args:
        portfolio_path: path to portfolio.json
        session: optional aiohttp session
        move_threshold: significant move threshold (default 3%)

    Returns:
        PortfolioSnapshot
    """
    portfolio_data = load_portfolio_data(portfolio_path)
    if not portfolio_data.holdings:
        return PortfolioSnapshot()

    codes = get_all_stock_codes(portfolio_data)
    prices = await fetch_prices(codes, session)
    return compute_portfolio_from_data(portfolio_data, prices, move_threshold)


# ── Formatting ──────────────────────────────────────────────────────────────


def format_portfolio_report(snapshot: PortfolioSnapshot) -> str:
    """Format a portfolio snapshot into a readable Chinese report."""
    lines = []
    pnl_emoji = "📈" if snapshot.total_pnl >= 0 else "📉"
    lines.append(f"{pnl_emoji} 持仓报告")

    # Total wealth (market value + cash) if cash is present
    if snapshot.cash.total > 0:
        lines.append(f"总资产: ¥{snapshot.total_wealth:,.2f} (含现金 ¥{snapshot.cash.total:,.2f})")
    lines.append(f"持仓市值: ¥{snapshot.total_market_value:,.2f}")
    lines.append(f"总盈亏: ¥{snapshot.total_pnl:,.2f} ({snapshot.total_pnl_pct:+.2f}%)")
    lines.append("")

    # Group holdings by type
    stock_holdings = [h for h in snapshot.holdings if h.type in ("stock", "etf")]
    fund_holdings = [h for h in snapshot.holdings if h.type == "fund_otc"]
    gold_holdings = [h for h in snapshot.holdings if h.type == "gold_accumulate"]

    if stock_holdings:
        lines.append("📊 股票/ETF:")
        for h in stock_holdings:
            emoji = "🟢" if h.pnl >= 0 else "🔴"
            lines.append(
                f"  {emoji} {h.name}: ¥{h.current_price:.2f} "
                f"盈亏 ¥{h.pnl:,.2f} ({h.pnl_pct:+.2f}%)"
            )

    if fund_holdings:
        lines.append("📈 基金:")
        for h in fund_holdings:
            emoji = "🟢" if h.pnl >= 0 else "🔴"
            nav_str = f"净值 ¥{h.nav:.4f}" if h.nav > 0 else "净值待更新"
            lines.append(
                f"  {emoji} {h.name}: {nav_str} "
                f"市值 ¥{h.market_value:,.2f}"
            )
            if h.pending_redemption and h.pending_redemption.status == "pending":
                lines.append(
                    f"    ⏳ 赎回中: {h.pending_redemption.shares:.0f}份 "
                    f"(预计 {h.pending_redemption.confirm_date})"
                )

    if gold_holdings:
        lines.append("🥇 积存金:")
        for h in gold_holdings:
            lines.append(
                f"  💛 {h.name}: {h.grams:.0f}克 "
                f"成本 ¥{h.cost_total:,.2f} (¥{h.cost_per_gram:.2f}/克)"
            )

    if snapshot.significant_moves:
        lines.append("")
        lines.append("⚠️ 大幅波动: " + "、".join(snapshot.significant_moves))

    # Closed positions summary
    if snapshot.closed_positions:
        lines.append("")
        lines.append("📋 已清仓:")
        for cp in snapshot.closed_positions:
            emoji = "🟢" if cp.profit_loss >= 0 else "🔴"
            lines.append(
                f"  {emoji} {cp.name}: {cp.close_date} "
                f"盈亏 ¥{cp.profit_loss:,.2f} ({cp.return_rate:+.2f}%)"
            )

    return "\n".join(lines)


# ── Trading Record Validation ──────────────────────────────────────────────


def validate_trade_price(
    code: str,
    user_price: float,
    current_price: float,
    tolerance: float = 0.15,
) -> dict:
    """Validate that user-reported trade price is sane.

    Args:
        code: stock code
        user_price: price reported by user
        current_price: current market price
        tolerance: max allowed deviation (default 15%)

    Returns:
        {'valid': bool, 'warning': str|None, 'deviation_pct': float}
    """
    if current_price == 0:
        return {
            "valid": True,
            "warning": f"{code} 无实时行情，无法校验价格",
            "deviation_pct": 0.0,
        }

    deviation = abs(user_price - current_price) / current_price
    if deviation > tolerance:
        return {
            "valid": False,
            "warning": (
                f"{code} 报告价格 {user_price:.2f} 偏离实时价 "
                f"{current_price:.2f} 达 {deviation:.1%}，超过阈值 {tolerance:.0%}"
            ),
            "deviation_pct": round(deviation, 4),
        }
    return {
        "valid": True,
        "warning": None,
        "deviation_pct": round(deviation, 4),
    }


def compute_weighted_avg_cost(
    existing_shares: float,
    existing_cost: float,
    new_shares: float,
    new_price: float,
) -> tuple[float, float]:
    """Compute weighted average cost after a new buy.

    Returns:
        (new_total_shares, new_avg_cost)
    """
    total_shares = existing_shares + new_shares
    if total_shares == 0:
        return (0.0, 0.0)
    total_cost = existing_cost * existing_shares + new_price * new_shares
    avg_cost = total_cost / total_shares
    return (round(total_shares, 4), round(avg_cost, 4))


def compute_cash_impact(
    action: str, amount: float, current_cash: float
) -> float:
    """Compute new cash balance after a trade.

    Args:
        action: 'buy' or 'sell'
        amount: trade amount (shares * price)
        current_cash: current cash balance

    Returns:
        new cash balance
    """
    if action == "buy":
        return round(current_cash - amount, 2)
    elif action == "sell":
        return round(current_cash + amount, 2)
    else:
        raise ValueError(f"Unknown action: {action!r}, expected 'buy' or 'sell'")


def compute_realized_pnl(
    shares: float, sell_price: float, avg_cost: float
) -> float:
    """Compute realized P&L for a sell trade."""
    return round(shares * (sell_price - avg_cost), 2)


def validate_and_record_trade(
    portfolio_data: dict,
    code: str,
    action: str,
    shares: float,
    price: float,
) -> dict:
    """Full trade validation and recording pipeline.

    1. Validate price sanity
    2. Check cash impact (for buys)
    3. Compute weighted avg cost (for buys)
    4. Compute realized P&L (for sells)
    5. Return validation result with all warnings

    Returns:
        {'valid': bool, 'warnings': list[str], 'new_cash': float,
         'new_avg_cost': float, 'realized_pnl': float}
    """
    warnings: list[str] = []

    # Find holding
    holdings = portfolio_data.get("holdings", [])
    holding = None
    for h in holdings:
        if h.get("code") == code:
            holding = h
            break

    current_price = portfolio_data.get("current_prices", {}).get(code, 0.0)

    # 1. Price sanity check
    price_check = validate_trade_price(code, price, current_price)
    if price_check["warning"]:
        warnings.append(price_check["warning"])
    if not price_check["valid"]:
        return {
            "valid": False,
            "warnings": warnings,
            "new_cash": portfolio_data.get("cash", {}).get("total", 0.0),
            "new_avg_cost": holding.get("cost_price", 0.0) if holding else 0.0,
            "realized_pnl": 0.0,
        }

    current_cash = portfolio_data.get("cash", {}).get("total", 0.0)
    amount = shares * price
    new_avg_cost = 0.0
    realized_pnl = 0.0

    if action == "buy":
        # 2. Cash check
        if amount > current_cash:
            warnings.append(
                f"买入金额 ¥{amount:,.2f} 超过可用资金 ¥{current_cash:,.2f}"
            )
        # 3. Weighted avg cost
        existing_shares = holding.get("shares", 0) if holding else 0
        existing_cost = holding.get("cost_price", 0) if holding else 0
        _, new_avg_cost = compute_weighted_avg_cost(
            existing_shares, existing_cost, shares, price
        )
    elif action == "sell":
        if holding and shares > holding.get("shares", 0):
            warnings.append(
                f"卖出 {shares} 股超过持有 {holding['shares']} 股"
            )
        avg_cost = holding.get("cost_price", 0) if holding else price
        realized_pnl = compute_realized_pnl(shares, price, avg_cost)
    else:
        warnings.append(f"未知操作: {action}")
        return {
            "valid": False,
            "warnings": warnings,
            "new_cash": current_cash,
            "new_avg_cost": 0.0,
            "realized_pnl": 0.0,
        }

    new_cash = compute_cash_impact(action, amount, current_cash)

    return {
        "valid": len(warnings) == 0 or action == "buy",
        "warnings": warnings,
        "new_cash": new_cash,
        "new_avg_cost": new_avg_cost,
        "realized_pnl": realized_pnl,
    }
