"""QDII/Cross-border ETF analysis — compare underlying index vs A-share ETF."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TENCENT_QUOTE_URL = "http://qt.gtimg.cn/q={codes}"

# Mapping: A-share ETF/fund code → underlying index
QDII_MAPPING: dict[str, dict[str, str]] = {
    "f_021778": {"index": "us.NDX", "name": "纳斯达克100"},
    "f_270042": {"index": "us.NDX", "name": "纳斯达克100"},
    "sz159941": {"index": "us.NDX", "name": "纳斯达克100"},
    "sh513500": {"index": "us.SPX", "name": "标普500"},
    "sh513100": {"index": "us.NDX", "name": "纳斯达克100"},
}


@dataclass
class QDIISnapshot:
    """Analysis snapshot for a single QDII holding."""

    etf_code: str
    etf_name: str
    etf_price: float  # A-share ETF price or NAV
    index_code: str
    index_name: str
    index_value: float
    # Computed
    tracking_diff: float = 0.0  # % difference attributed to FX + premium
    interpretation: str = ""


def is_qdii(code: str) -> bool:
    """Check if a code is in the QDII mapping."""
    return code in QDII_MAPPING


def parse_us_index_quote(raw_text: str, code: str) -> dict[str, Any]:
    """Parse Tencent US index quote response.

    Tencent US quote format (fields separated by ~):
      $2=name, $4=price, $5=prev_close, $33=change%, $47=52w_high, $48=52w_low
    """
    result: dict[str, Any] = {"code": code, "price": 0.0, "change_pct": 0.0, "high_52w": 0.0, "low_52w": 0.0}
    for line in raw_text.strip().split("\n"):
        line = line.strip().rstrip(";")
        if "=" not in line:
            continue
        _, _, value = line.partition("=")
        value = value.strip('"')
        parts = value.split("~")
        if len(parts) < 50:
            continue
        try:
            result["price"] = float(parts[3]) if parts[3] else 0.0
            result["change_pct"] = float(parts[32]) if len(parts) > 32 and parts[32] else 0.0
            result["high_52w"] = float(parts[47]) if len(parts) > 47 and parts[47] else 0.0
            result["low_52w"] = float(parts[48]) if len(parts) > 48 and parts[48] else 0.0
        except (ValueError, IndexError):
            continue
    return result


async def fetch_us_index(
    code: str,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Fetch US index price from Tencent API.

    Args:
        code: Tencent US index code like 'us.NDX', 'us.SPX'
        session: optional aiohttp session

    Returns:
        dict with keys: code, price, change_pct, high_52w, low_52w
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        url = TENCENT_QUOTE_URL.format(codes=code)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            raw = await resp.text(encoding="gbk")
        return parse_us_index_quote(raw, code)
    except Exception:
        logger.exception("Failed to fetch US index %s", code)
        return {"code": code, "price": 0.0, "change_pct": 0.0, "high_52w": 0.0, "low_52w": 0.0}
    finally:
        if own_session:
            await session.close()


async def fetch_etf_price(
    code: str,
    session: aiohttp.ClientSession | None = None,
) -> float:
    """Fetch A-share ETF/fund price from Tencent API."""
    from secretary.wealth.portfolio import fetch_prices

    prices = await fetch_prices([code], session)
    return prices.get(code, 0.0)


def compute_tracking_diff(etf_price: float, index_value: float) -> float:
    """Compute a simplified tracking difference percentage.

    In reality this also depends on FX rates and ETF premium/discount,
    but we approximate by comparing relative positions.
    """
    if index_value <= 0 or etf_price <= 0:
        return 0.0
    # This is a simplified metric — in practice you'd normalize both to a base date
    return 0.0  # Without base-date normalization, raw comparison isn't meaningful


def interpret_qdii(etf_code: str, etf_price: float, index_price: float, index_name: str) -> str:
    """Generate interpretation text for QDII holding."""
    if etf_price <= 0:
        return f"无法获取ETF {etf_code} 的价格"
    if index_price <= 0:
        return f"无法获取跟踪指数 {index_name} 的价格"
    return (
        f"跟踪{index_name}指数。"
        f"指数当前点位: {index_price:.2f}。"
        f"注意汇率波动和ETF溢价/折价对净值的影响。"
    )


async def analyze_qdii(
    etf_code: str,
    session: aiohttp.ClientSession | None = None,
) -> QDIISnapshot | None:
    """Analyze a QDII ETF by comparing with underlying index.

    Args:
        etf_code: A-share ETF/fund code
        session: optional aiohttp session

    Returns:
        QDIISnapshot or None if not a QDII holding
    """
    mapping = QDII_MAPPING.get(etf_code)
    if mapping is None:
        return None

    index_code = mapping["index"]
    index_name = mapping["name"]

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        # Fetch both in parallel would be ideal, but keep it simple
        index_data = await fetch_us_index(index_code, session)
        etf_price = await fetch_etf_price(etf_code, session)

        index_value = index_data.get("price", 0.0)
        tracking_diff = compute_tracking_diff(etf_price, index_value)
        interp = interpret_qdii(etf_code, etf_price, index_value, index_name)

        return QDIISnapshot(
            etf_code=etf_code,
            etf_name=mapping.get("name", etf_code),
            etf_price=etf_price,
            index_code=index_code,
            index_name=index_name,
            index_value=index_value,
            tracking_diff=tracking_diff,
            interpretation=interp,
        )
    finally:
        if own_session:
            await session.close()


async def analyze_all_qdii(
    portfolio_data: list[dict[str, Any]],
    session: aiohttp.ClientSession | None = None,
) -> list[QDIISnapshot]:
    """Analyze all QDII holdings in a portfolio.

    Args:
        portfolio_data: list of holding dicts with 'code' key
        session: optional aiohttp session

    Returns:
        list of QDIISnapshot for each QDII holding found
    """
    qdii_codes = [h["code"] for h in portfolio_data if h.get("code") in QDII_MAPPING]
    if not qdii_codes:
        return []

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        results: list[QDIISnapshot] = []
        for code in qdii_codes:
            snap = await analyze_qdii(code, session)
            if snap is not None:
                results.append(snap)
        return results
    finally:
        if own_session:
            await session.close()
