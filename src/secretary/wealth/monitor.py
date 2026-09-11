"""Monitor stock announcements via eastmoney API (all stocks) with cninfo fallback.

Checks for keywords: 收购/重组/发行股份/复牌/减持/增持/分红/业绩预告/可转债
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# eastmoney announcement search API (supports any stock)
EASTMONEY_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"

# cninfo announcement search API (fallback, needs org_id)
CNINFO_SEARCH_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"

KEYWORDS = [
    "收购",
    "重组",
    "发行股份",
    "复牌",
    "减持",
    "增持",
    "分红",
    "业绩预告",
    "可转债",
]


@dataclass
class Announcement:
    """Single announcement."""

    title: str
    date: str
    url: str
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class MonitorResult:
    """Result of a stock monitor check."""

    announcements: list[Announcement] = field(default_factory=list)
    new_keyword_matches: list[Announcement] = field(default_factory=list)
    check_time: datetime = field(default_factory=datetime.now)
    error: str | None = None


# ── Keyword Scanning ────────────────────────────────────────────────────────


def scan_keywords(text: str, keywords: list[str] | None = None) -> list[str]:
    """Scan text for keyword matches.

    Args:
        text: text to scan
        keywords: list of keywords to look for (default: KEYWORDS)

    Returns:
        list of matched keywords
    """
    if keywords is None:
        keywords = KEYWORDS
    return [kw for kw in keywords if kw in text]


# ── eastmoney Parser ────────────────────────────────────────────────────────


def parse_eastmoney_response(data: dict[str, Any]) -> list[Announcement]:
    """Parse eastmoney announcement API response into Announcement objects.

    Expected response structure:
    {"data": {"list": [{"art_code": "...", "title": "...",
      "notice_date": "...", "url": "...", ...}]}}
    """
    announcements: list[Announcement] = []
    data_inner = data.get("data") or {}
    items = data_inner.get("list") or []
    if not isinstance(items, list):
        return announcements

    for item in items:
        title = item.get("title", "")
        # Remove HTML tags if any
        title = re.sub(r"<[^>]+>", "", title)

        notice_date = item.get("notice_date", "")
        # eastmoney returns "2024-01-01 00:00:00", extract date part
        if isinstance(notice_date, str) and len(notice_date) >= 10:
            date_str = notice_date[:10]
        else:
            date_str = str(notice_date)

        art_code = item.get("art_code", "")
        columns = item.get("columns", [])
        column_code = ""
        if isinstance(columns, list) and columns:
            column_code = columns[0].get("column_code", "") if isinstance(columns[0], dict) else ""

        # Build URL from art_code
        if art_code:
            url = f"https://data.eastmoney.com/notices/detail/{column_code}/{art_code}.html"
        else:
            url = item.get("url", "")

        matched = scan_keywords(title)
        announcements.append(
            Announcement(
                title=title,
                date=date_str,
                url=url,
                matched_keywords=matched,
            )
        )

    return announcements


# ── cninfo Parser (fallback) ────────────────────────────────────────────────


def parse_cninfo_response(data: dict[str, Any]) -> list[Announcement]:
    """Parse cninfo API response into Announcement objects.

    Expected response structure:
    {"announcements": [{"announcementTitle": "...", "announcementDate": "...", ...}]}
    """
    announcements = []
    items = data.get("announcements") or []
    if not isinstance(items, list):
        return announcements

    for item in items:
        title = item.get("announcementTitle", "")
        # Remove HTML tags from title
        title = re.sub(r"<[^>]+>", "", title)

        timestamp = item.get("announcementDate", 0)
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            date_str = datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d")
        else:
            date_str = str(timestamp)

        adj_url = item.get("adjunctUrl", "")
        url = f"http://static.cninfo.com.cn/{adj_url}" if adj_url else ""

        matched = scan_keywords(title)
        announcements.append(
            Announcement(
                title=title,
                date=date_str,
                url=url,
                matched_keywords=matched,
            )
        )

    return announcements


# ── Fetching ────────────────────────────────────────────────────────────────


async def fetch_eastmoney_announcements(
    stock_code: str,
    session: aiohttp.ClientSession | None = None,
    page_size: int = 20,
) -> list[Announcement]:
    """Fetch recent announcements from eastmoney API for any stock.

    Args:
        stock_code: stock code like "600519" or "sz300442" (market prefix stripped)
        session: optional aiohttp session to reuse
        page_size: number of announcements to fetch

    Returns:
        list of Announcement objects
    """
    # Strip market prefix (sh/sz) if present
    pure_code = stock_code
    if pure_code.startswith(("sh", "sz")):
        pure_code = pure_code[2:]

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        params = {
            "sr": "-1",
            "page_size": str(page_size),
            "page_index": "1",
            "ann_type": "A",
            "client_source": "web",
            "f_node": "0",
            "s_node": "0",
            "stock_list": pure_code,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        async with session.get(
            EASTMONEY_ANN_URL,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)

        return parse_eastmoney_response(data)
    except Exception:
        logger.exception("Failed to fetch announcements from eastmoney for %s", stock_code)
        return []
    finally:
        if own_session:
            await session.close()


async def fetch_cninfo_announcements(
    stock_code: str,
    org_id: str,
    session: aiohttp.ClientSession | None = None,
    page_size: int = 20,
) -> list[Announcement]:
    """Fetch recent announcements from cninfo (fallback, needs org_id).

    Args:
        stock_code: stock code like "300442"
        org_id: cninfo orgId for the stock
        session: optional aiohttp session to reuse
        page_size: number of announcements to fetch

    Returns:
        list of Announcement objects
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        form_data = aiohttp.FormData()
        form_data.add_field("stock", f"{stock_code},{org_id}")
        form_data.add_field("tabName", "fulltext")
        form_data.add_field("pageNum", "1")
        form_data.add_field("pageSize", str(page_size))
        form_data.add_field("column", "szcy")
        form_data.add_field("category", "")
        form_data.add_field("plate", "")
        form_data.add_field("seDate", "")
        form_data.add_field("searchkey", "")
        form_data.add_field("secid", "")
        form_data.add_field("sortName", "")
        form_data.add_field("sortType", "")
        form_data.add_field("isHLtitle", "true")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "http://www.cninfo.com.cn/new/disclosure/stock",
            "Accept": "application/json",
        }

        async with session.post(
            CNINFO_SEARCH_URL,
            data=form_data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)

        return parse_cninfo_response(data)
    except Exception:
        logger.exception("Failed to fetch announcements from cninfo")
        return []
    finally:
        if own_session:
            await session.close()


# ── Monitor Logic ───────────────────────────────────────────────────────────


async def check_stock_announcements(
    stock_code: str,
    stock_name: str = "",
    org_id: str | None = None,
    seen_urls: set[str] | None = None,
    session: aiohttp.ClientSession | None = None,
) -> MonitorResult:
    """Check a stock for keyword-matching announcements.

    Uses eastmoney API by default. Falls back to cninfo if org_id is provided.

    Args:
        stock_code: stock code like "sh600519" or "sz300442"
        stock_name: stock name for display (optional)
        org_id: cninfo orgId for fallback (optional)
        seen_urls: set of already-seen announcement URLs to filter out
        session: optional aiohttp session

    Returns:
        MonitorResult with any new keyword matches
    """
    if seen_urls is None:
        seen_urls = set()

    try:
        announcements = await fetch_eastmoney_announcements(stock_code, session)

        # Fallback to cninfo if eastmoney returned nothing and org_id is available
        if not announcements and org_id:
            pure_code = stock_code[2:] if stock_code.startswith(("sh", "sz")) else stock_code
            announcements = await fetch_cninfo_announcements(pure_code, org_id, session)
    except Exception as exc:
        return MonitorResult(error=str(exc))

    keyword_matches = []
    for ann in announcements:
        if ann.matched_keywords and ann.url not in seen_urls:
            keyword_matches.append(ann)

    return MonitorResult(
        announcements=announcements,
        new_keyword_matches=keyword_matches,
    )


async def check_all_holdings(
    stock_codes: list[str],
    stock_names: dict[str, str] | None = None,
    seen_urls_map: dict[str, set[str]] | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, MonitorResult]:
    """Check all stock/etf holdings for keyword-matching announcements.

    Args:
        stock_codes: list of stock codes to check
        stock_names: optional mapping of code -> name for display
        seen_urls_map: optional mapping of code -> set of seen URLs
        session: optional aiohttp session to reuse

    Returns:
        dict of code -> MonitorResult
    """
    if stock_names is None:
        stock_names = {}
    if seen_urls_map is None:
        seen_urls_map = {}

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    results: dict[str, MonitorResult] = {}
    try:
        for code in stock_codes:
            name = stock_names.get(code, "")
            seen = seen_urls_map.get(code, set())
            result = await check_stock_announcements(
                stock_code=code,
                stock_name=name,
                seen_urls=seen,
                session=session,
            )
            results[code] = result
    finally:
        if own_session:
            await session.close()

    return results


# ── Formatting ──────────────────────────────────────────────────────────────


def format_announcement_alert(
    stock_code: str,
    stock_name: str,
    result: MonitorResult,
) -> str:
    """Format keyword matches into an alert string."""
    if not result.new_keyword_matches:
        return ""

    display_name = stock_name or stock_code
    lines = [f"🚨 {display_name} 公告预警:"]
    for ann in result.new_keyword_matches:
        kws = "/".join(ann.matched_keywords)
        lines.append(f"  [{kws}] {ann.title} ({ann.date})")
    return "\n".join(lines)


# ── Legacy compat (for existing tests) ──────────────────────────────────────


async def fetch_runze_announcements(
    session: aiohttp.ClientSession | None = None,
    page_size: int = 30,
) -> list[Announcement]:
    """Fetch announcements for 润泽科技 — delegates to eastmoney."""
    return await fetch_eastmoney_announcements("300442", session, page_size)


async def check_runze(
    seen_urls: set[str] | None = None,
    session: aiohttp.ClientSession | None = None,
) -> MonitorResult:
    """Check 润泽科技 for keyword-matching announcements (legacy compat)."""
    return await check_stock_announcements(
        stock_code="sz300442",
        stock_name="润泽科技",
        seen_urls=seen_urls,
        session=session,
    )


def format_runze_alert(result: MonitorResult) -> str:
    """Format keyword matches for 润泽科技 (legacy compat)."""
    return format_announcement_alert("sz300442", "润泽科技", result)
