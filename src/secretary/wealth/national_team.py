"""National team (国家队) signal scoring via ETF flow data.

Fetches ETF fund flow data from eastmoney/akshare and computes a
composite score from -5 (very bearish) to +5 (very bullish).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Eastmoney fund flow API for key broad-market ETFs
ETF_FLOW_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "fid=f62&po=1&pz=50&pn=1&np=1&fltt=2&invt=2&"
    "fs=b:MK0021&fields=f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
)

# Key broad-market ETF codes to focus on
KEY_ETFS = {
    "510050": "上证50ETF",
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "159919": "沪深300ETF",
    "159915": "创业板ETF",
    "588000": "科创50ETF",
}


@dataclass
class NationalTeamResult:
    """Result of national team signal analysis."""

    score: float  # -5 to +5
    interpretation: str
    etf_flows: dict[str, float]  # code -> net flow (亿元)
    timestamp: datetime
    raw_details: dict[str, Any]


def compute_score_from_flows(flows: dict[str, float]) -> tuple[float, str]:
    """Compute national team score from ETF net inflow data.

    Args:
        flows: dict of ETF code -> net inflow in 亿元 (positive = inflow)

    Returns:
        (score, interpretation) where score is -5 to +5
    """
    if not flows:
        return 0.0, "无ETF资金流向数据"

    total_flow = sum(flows.values())
    num_positive = sum(1 for v in flows.values() if v > 0)
    num_negative = sum(1 for v in flows.values() if v < 0)
    total_count = len(flows)

    # Score based on total flow magnitude and breadth
    # Flow magnitude: each 10亿 = 1 point
    magnitude_score = max(-3.0, min(3.0, total_flow / 10.0))

    # Breadth: ratio of positive vs negative ETFs
    if total_count > 0:
        breadth_ratio = (num_positive - num_negative) / total_count
        breadth_score = breadth_ratio * 2.0  # -2 to +2
    else:
        breadth_score = 0.0

    raw_score = magnitude_score + breadth_score
    score = max(-5.0, min(5.0, round(raw_score, 1)))

    # Generate interpretation
    parts = []
    if total_flow > 0:
        parts.append(f"ETF净流入{total_flow:.1f}亿")
    elif total_flow < 0:
        parts.append(f"ETF净流出{abs(total_flow):.1f}亿")
    else:
        parts.append("ETF资金流向持平")

    parts.append(f"流入{num_positive}只/流出{num_negative}只")

    if score >= 3:
        mood = "国家队大幅买入，强烈看多"
    elif score >= 1:
        mood = "国家队温和买入，偏多"
    elif score >= -1:
        mood = "国家队动作不明显，中性"
    elif score >= -3:
        mood = "国家队净卖出，偏空"
    else:
        mood = "国家队大幅卖出，强烈看空"

    interpretation = f"{mood}。{', '.join(parts)}。"
    return score, interpretation


async def fetch_etf_flows(session: aiohttp.ClientSession | None = None) -> dict[str, float]:
    """Fetch ETF fund flow data from eastmoney.

    Returns:
        dict of ETF code -> net flow in 亿元
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://data.eastmoney.com/",
        }
        async with session.get(
            ETF_FLOW_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            data = await resp.json(content_type=None)

        flows: dict[str, float] = {}
        items = data.get("data", {}).get("diff", [])
        if not items:
            logger.warning("No ETF flow data returned from eastmoney")
            return flows

        for item in items:
            code = str(item.get("f12", ""))
            if code in KEY_ETFS:
                # f62 = main net flow, unit is 元
                net_flow_raw = item.get("f62")
                if net_flow_raw is not None and net_flow_raw != "-":
                    net_flow = float(net_flow_raw) / 1e8  # Convert to 亿元
                    flows[code] = round(net_flow, 2)

        return flows
    except Exception:
        logger.exception("Failed to fetch ETF flows from eastmoney")
        return {}
    finally:
        if own_session:
            await session.close()


async def get_national_team_signal(
    session: aiohttp.ClientSession | None = None,
) -> NationalTeamResult:
    """Get the current national team signal.

    Args:
        session: optional aiohttp session to reuse

    Returns:
        NationalTeamResult with score, interpretation, and details
    """
    flows = await fetch_etf_flows(session)
    score, interpretation = compute_score_from_flows(flows)

    return NationalTeamResult(
        score=score,
        interpretation=interpretation,
        etf_flows=flows,
        timestamp=datetime.now(),
        raw_details={"etf_count": len(flows), "total_flow_yi": sum(flows.values())},
    )
