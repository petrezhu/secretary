"""东方财富账户自动同步 — 通过 Playwright 登录交易网页版，抓取持仓和成交记录。

工作流程:
1. Playwright 打开 jywg.18.cn 登录页
2. 用户扫码/输入密码完成登录
3. 保存 cookies 和 localStorage 到本地文件
4. 后续用 cookies 直接调 REST API（不需再登录，直到 session 过期）
5. 定时同步持仓 + 成交记录到 portfolio.json

依赖: playwright (pip install playwright && playwright install chromium)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 东方财富交易网关 URL ─────────────────────────────────────────────────────

JYWG_BASE = "https://jywg.18.cn"
LOGIN_URL = f"{JYWG_BASE}/Login/Authentication"
HOLDINGS_URL = f"{JYWG_BASE}/Stock/QueryStock"
ASSETS_URL = f"{JYWG_BASE}/Asset/QueryAsset"
TRADE_HISTORY_URL = f"{JYWG_BASE}/Stock/QueryDeal"
PENDING_ORDERS_URL = f"{JYWG_BASE}/Stock/QueryOrder"

# Cookie 存储路径
DEFAULT_COOKIE_PATH = Path.home() / ".secretary" / "eastmoney_cookies.json"


@dataclass
class AccountAssets:
    """账户资产总览。"""
    total_assets: float = 0.0        # 总资产
    market_value: float = 0.0        # 证券市值
    available_cash: float = 0.0      # 可用资金
    frozen_cash: float = 0.0         # 冻结资金
    profit_today: float = 0.0        # 当日盈亏
    profit_total: float = 0.0        # 持仓盈亏
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AccountHolding:
    """单只持仓。"""
    code: str                  # 股票代码 (如 "601398")
    name: str                  # 股票名称
    shares: int                # 持仓数量
    available_shares: int      # 可卖数量
    cost_price: float          # 成本价
    current_price: float       # 现价
    market_value: float        # 市值
    profit: float              # 盈亏金额
    profit_pct: float          # 盈亏比例 %
    buy_date: str = ""         # 买入日期 (如能获取)


@dataclass
class TradeRecord:
    """成交记录。"""
    date: str                  # 成交日期 YYYY-MM-DD
    time: str                  # 成交时间 HH:MM:SS
    code: str                  # 股票代码
    name: str                  # 股票名称
    action: str                # "buy" / "sell"
    price: float               # 成交价
    shares: int                # 成交数量
    amount: float              # 成交金额
    fee: float = 0.0           # 手续费


@dataclass
class SyncResult:
    """同步结果。"""
    assets: AccountAssets | None = None
    holdings: list[AccountHolding] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    error: str | None = None
    sync_time: datetime = field(default_factory=datetime.now)
    cookie_valid: bool = True


# ── Cookie 管理 ──────────────────────────────────────────────────────────────

def save_cookies(
    cookies: list[dict],
    local_storage: dict | None = None,
    path: Path | None = None,
) -> None:
    """保存 cookies 和 localStorage 到文件。"""
    path = path or DEFAULT_COOKIE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cookies": cookies,
        "localStorage": local_storage or {},
        "saved_at": datetime.now().isoformat(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.info("Cookies saved to %s", path)


def load_cookies(path: Path | None = None) -> dict | None:
    """加载已保存的 cookies。返回 None 如果文件不存在。"""
    path = path or DEFAULT_COOKIE_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data
    except (json.JSONDecodeError, KeyError):
        return None


def is_cookie_fresh(path: Path | None = None, max_age_hours: int = 4) -> bool:
    """检查 cookies 是否在有效期内（默认4小时）。"""
    data = load_cookies(path)
    if not data:
        return False
    saved_at = datetime.fromisoformat(data.get("saved_at", "2000-01-01"))
    age_hours = (datetime.now() - saved_at).total_seconds() / 3600
    return age_hours < max_age_hours


# ── Playwright 登录 ──────────────────────────────────────────────────────────

async def login_with_playwright(
    headless: bool = False,
    cookie_path: Path | None = None,
    timeout_seconds: int = 120,
) -> dict:
    """启动 Playwright 浏览器，打开东方财富登录页，等待用户完成登录。

    Args:
        headless: 是否无头模式。首次登录建议 False（需要扫码/输密码）
        cookie_path: cookie 保存路径
        timeout_seconds: 等待登录完成的超时时间

    Returns:
        {"success": bool, "cookies": list, "error": str|None}
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "cookies": [], "error": "playwright not installed. Run: pip install playwright && playwright install chromium"}

    cookie_path = cookie_path or DEFAULT_COOKIE_PATH

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        try:
            # 打开登录页
            logger.info("Opening %s ...", JYWG_BASE)
            await page.goto(JYWG_BASE, wait_until="networkidle", timeout=30000)

            # 等待用户登录完成（检测跳转到交易主页面）
            # 登录成功的标志：URL 变化 或 页面出现"资产"等关键词
            logger.info("Waiting for login (timeout: %ds)...", timeout_seconds)
            logger.info("Please complete login in the browser window.")

            # 轮询检测登录状态
            start = time.time()
            logged_in = False
            while time.time() - start < timeout_seconds:
                current_url = page.url
                page_text = await page.content()

                # 检测登录成功标志
                if any(kw in page_text for kw in ["总资产", "持仓", "可用资金", "资金余额"]):
                    logged_in = True
                    break

                # 也检查 URL 变化（登录后通常跳转）
                if "/Home" in current_url or "/Main" in current_url or "/Asset" in current_url:
                    logged_in = True
                    break

                await asyncio.sleep(2)

            if not logged_in:
                return {"success": False, "cookies": [], "error": "Login timeout"}

            logger.info("Login detected! Saving cookies...")

            # 保存 cookies
            raw_cookies = await context.cookies()
            cookies = [dict(c) for c in raw_cookies]
            # 保存 localStorage
            local_storage = await page.evaluate("""() => {
                let data = {};
                for (let i = 0; i < localStorage.length; i++) {
                    let key = localStorage.key(i);
                    data[key] = localStorage.getItem(key);
                }
                return data;
            }""")

            save_cookies(cookies, local_storage, cookie_path)

            return {"success": True, "cookies": cookies, "error": None}

        finally:
            await browser.close()


async def refresh_session_with_playwright(
    cookie_path: Path | None = None,
    headless: bool = True,
) -> bool:
    """用已保存的 cookies 恢复会话，验证是否仍然有效。

    Returns:
        True if session is still valid
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False

    data = load_cookies(cookie_path)
    if not data:
        return False

    cookie_path = cookie_path or DEFAULT_COOKIE_PATH

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context()

        # 注入 cookies
        cookies = data.get("cookies", [])
        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()

        try:
            await page.goto(JYWG_BASE, wait_until="networkidle", timeout=30000)
            page_text = await page.content()

            if any(kw in page_text for kw in ["总资产", "持仓", "可用资金"]):
                # 刷新 cookies
                new_cookies = await context.cookies()
                save_cookies([dict(c) for c in new_cookies], data.get("localStorage"), cookie_path)
                return True
            return False
        finally:
            await browser.close()


# ── REST API 抓取（用 cookies）──────────────────────────────────────────────

async def _api_request(
    url: str,
    cookies: list[dict],
    data: dict | None = None,
    method: str = "POST",
) -> dict | None:
    """用保存的 cookies 调用东方财富交易 API。"""
    import aiohttp

    # 构建 cookie jar
    cookie_dict = {c["name"]: c["value"] for c in cookies if "name" in c}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": JYWG_BASE,
        "Accept": "application/json",
    }

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            if method == "POST":
                async with session.post(url, data=data, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
            else:
                async with session.get(url, headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
    except Exception as e:
        logger.error("API request failed: %s", e)
    return None


def _parse_holding(item: dict) -> AccountHolding | None:
    """解析东方财富 API 返回的持仓数据。

    东方财富 API 返回格式（字段名可能有变化，需要实际抓包确认）：
    {
        "Gddm": "股东代码", "Zqdm": "证券代码", "Zqmc": "证券名称",
        "Zqsl": "持仓数量", "Kysl": "可用数量", "Cbjg": "成本价",
        "Zxjg": "最新价", "Zxsz": "最新市值", "Ykje": "盈亏金额",
        "Ykbl": "盈亏比例"
    }
    """
    try:
        code = str(item.get("Zqdm", item.get("zqdm", item.get("stockCode", ""))))
        name = str(item.get("Zqmc", item.get("zqmc", item.get("stockName", ""))))
        if not code:
            return None

        return AccountHolding(
            code=code,
            name=name,
            shares=int(float(item.get("Zqsl", item.get("zqsl", item.get("stockAmount", 0))))),
            available_shares=int(float(item.get("Kysl", item.get("kysl", item.get("availableAmount", 0))))),
            cost_price=float(item.get("Cbjg", item.get("cbjg", item.get("costPrice", 0)))),
            current_price=float(item.get("Zxjg", item.get("zxjg", item.get("currentPrice", 0)))),
            market_value=float(item.get("Zxsz", item.get("zxsz", item.get("marketValue", 0)))),
            profit=float(item.get("Ykje", item.get("ykje", item.get("profit", 0)))),
            profit_pct=float(item.get("Ykbl", item.get("ykbl", item.get("profitRate", 0)))),
        )
    except (ValueError, TypeError) as e:
        logger.warning("Failed to parse holding: %s — %s", item, e)
        return None


def _parse_assets(data: dict) -> AccountAssets:
    """解析资产数据。"""
    return AccountAssets(
        total_assets=float(data.get("Zzc", data.get("zzc", data.get("totalAssets", 0)))),
        market_value=float(data.get("Zsz", data.get("zsz", data.get("marketValue", 0)))),
        available_cash=float(data.get("Kyzj", data.get("kyzj", data.get("availableCash", 0)))),
        frozen_cash=float(data.get("Djzj", data.get("djzj", data.get("frozenCash", 0)))),
        profit_today=float(data.get("Yk", data.get("yk", data.get("profitToday", 0)))),
        profit_total=float(data.get("Ccyk", data.get("ccyk", data.get("profitTotal", 0)))),
    )


def _parse_trade(item: dict) -> TradeRecord | None:
    """解析成交记录。"""
    try:
        code = str(item.get("Zqdm", item.get("zqdm", item.get("stockCode", ""))))
        name = str(item.get("Zqmc", item.get("zqmc", item.get("stockName", ""))))
        if not code:
            return None

        # 成交方向：买入/卖出
        direction = str(item.get("Mmlb", item.get("mmlb", item.get("tradeDirection", ""))))
        if direction in ("买入", "B", "1"):
            action = "buy"
        elif direction in ("卖出", "S", "2"):
            action = "sell"
        else:
            action = direction.lower()

        return TradeRecord(
            date=str(item.get("Cjrq", item.get("cjrq", item.get("tradeDate", "")))),
            time=str(item.get("Cjsj", item.get("cjsj", item.get("tradeTime", "")))),
            code=code,
            name=name,
            action=action,
            price=float(item.get("Cjjg", item.get("cjjg", item.get("tradePrice", 0)))),
            shares=int(float(item.get("Cjsl", item.get("cjsl", item.get("tradeAmount", 0))))),
            amount=float(item.get("Cjje", item.get("cjje", item.get("tradeMoney", 0)))),
            fee=float(item.get("Sxf", item.get("sxf", item.get("fee", 0)))),
        )
    except (ValueError, TypeError) as e:
        logger.warning("Failed to parse trade: %s — %s", item, e)
        return None


async def fetch_holdings(cookies: list[dict]) -> tuple[AccountAssets | None, list[AccountHolding]]:
    """从东方财富 API 获取持仓数据。"""
    # 先查资产
    assets_data = await _api_request(ASSETS_URL, cookies)
    assets = _parse_assets(assets_data) if assets_data else None

    # 再查持仓
    holdings_data = await _api_request(HOLDINGS_URL, cookies)
    holdings = []
    if holdings_data and isinstance(holdings_data, list):
        for item in holdings_data:
            h = _parse_holding(item)
            if h and h.shares > 0:
                holdings.append(h)
    elif holdings_data and isinstance(holdings_data, dict):
        # 有些接口把列表包在 data 字段里
        items = holdings_data.get("data", holdings_data.get("Data", []))
        if isinstance(items, list):
            for item in items:
                h = _parse_holding(item)
                if h and h.shares > 0:
                    holdings.append(h)

    return assets, holdings


async def fetch_trade_history(
    cookies: list[dict],
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[TradeRecord]:
    """获取成交记录。

    Args:
        cookies: 已保存的 cookies
        start_date: 开始日期 YYYY-MM-DD（默认近30天）
        end_date: 结束日期 YYYY-MM-DD（默认今天）
    """
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        from datetime import timedelta
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    data = {
        "startDate": start_date.replace("-", ""),
        "endDate": end_date.replace("-", ""),
    }

    trades_data = await _api_request(TRADE_HISTORY_URL, cookies, data=data)
    trades = []
    if trades_data and isinstance(trades_data, list):
        for item in trades_data:
            t = _parse_trade(item)
            if t:
                trades.append(t)
    elif trades_data and isinstance(trades_data, dict):
        items = trades_data.get("data", trades_data.get("Data", []))
        if isinstance(items, list):
            for item in items:
                t = _parse_trade(item)
                if t:
                    trades.append(t)

    return trades


# ── 同步到 portfolio.json ───────────────────────────────────────────────────

def _market_code(code: str) -> str:
    """将纯数字代码转换为带市场前缀的代码 (sh/sz)。"""
    code = code.strip()
    if code.startswith(("sh", "sz")):
        return code
    if code.startswith(("6", "5", "9")):
        return f"sh{code}"
    elif code.startswith(("0", "1", "2", "3")):
        return f"sz{code}"
    return code


def sync_to_portfolio(
    holdings: list[AccountHolding],
    assets: AccountAssets | None,
    portfolio_path: str | Path,
    trades: list[TradeRecord] | None = None,
) -> dict:
    """将东方财富持仓同步到 portfolio.json。

    策略：
    - stock/etf 类型：直接用东方财富数据覆盖 shares 和 cost_price
    - fund_otc/gold_accumulate：保留原有数据（东方财富不管理这些）
    - cash：用东方财富的 available_cash 更新
    - trades：追加到对应 holding 的 transactions（去重）
    """
    portfolio_path = Path(portfolio_path)

    # 读取现有 portfolio
    if portfolio_path.exists():
        portfolio = json.loads(portfolio_path.read_text())
    else:
        portfolio = {"holdings": [], "cash": {"total": 0}, "closed_positions": [], "pending_actions": []}

    existing_holdings = {h.get("code", ""): h for h in portfolio.get("holdings", [])}
    em_holdings = {_market_code(h.code): h for h in holdings}

    changes = {"updated": [], "added": [], "removed": [], "cash_updated": False, "trades_added": 0}

    # 更新/添加 stock/etf 持仓
    for em_code, em_h in em_holdings.items():
        if em_code in existing_holdings:
            old = existing_holdings[em_code]
            # 只更新 stock/etf 类型
            if old.get("type") in ("stock", "etf", None):
                old_shares = old.get("shares", 0)
                old["shares"] = em_h.shares
                old["cost_price"] = round(em_h.cost_price, 4)
                old["current_price"] = em_h.current_price
                old["market_value"] = em_h.market_value
                old["name"] = em_h.name  # 用东方财富的名称
                changes["updated"].append(f"{em_h.name}({em_code}): {old_shares}→{em_h.shares}股")
        else:
            # 新增持仓
            # 判断类型：ETF代码通常以 5/1 开头
            code_num = em_code.replace("sh", "").replace("sz", "")
            holding_type = "etf" if code_num.startswith(("5", "1")) else "stock"

            new_holding = {
                "name": em_h.name,
                "code": em_code,
                "shares": em_h.shares,
                "cost_price": round(em_h.cost_price, 4),
                "current_price": em_h.current_price,
                "market_value": em_h.market_value,
                "type": holding_type,
                "note": f"东方财富自动同步 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            }
            portfolio.setdefault("holdings", []).append(new_holding)
            changes["added"].append(f"{em_h.name}({em_code}): {em_h.shares}股")

    # 标记已清仓（东方财富有但 portfolio 有而东方财富没有的 stock/etf）
    for code, old in existing_holdings.items():
        if code not in em_holdings and old.get("type") in ("stock", "etf"):
            # 可能是手动卖出或不在东方财富账户里
            if old.get("shares", 0) > 0:
                changes["removed"].append(f"{old.get('name', code)}: {old.get('shares', 0)}股 → 东方财富已无持仓")
                old["shares"] = 0
                old["note"] = old.get("note", "") + f" | 东方财富同步: 已清仓 {datetime.now().strftime('%Y-%m-%d')}"

    # 更新现金
    if assets and assets.available_cash > 0:
        portfolio.setdefault("cash", {})["total"] = round(assets.available_cash, 2)
        portfolio["cash"]["note"] = f"东方财富同步 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        changes["cash_updated"] = True

    # 追加成交记录
    if trades:
        trades_by_code: dict[str, list[TradeRecord]] = {}
        for t in trades:
            trades_by_code.setdefault(_market_code(t.code), []).append(t)

        for holding in portfolio.get("holdings", []):
            code = holding.get("code", "")
            if code in trades_by_code:
                existing_txns = holding.get("transactions", [])
                existing_keys = {
                    (t.get("date", ""), t.get("action", ""), str(t.get("shares", "")), str(t.get("price", "")))
                    for t in existing_txns
                }

                for trade in trades_by_code[code]:
                    key = (trade.date, trade.action, str(trade.shares), str(trade.price))
                    if key not in existing_keys:
                        txn = {
                            "date": trade.date,
                            "action": trade.action,
                            "shares": trade.shares,
                            "price": trade.price,
                            "amount": trade.amount,
                            "note": "东方财富自动同步",
                        }
                        if trade.fee > 0:
                            txn["fee"] = trade.fee
                        existing_txns.append(txn)
                        changes["trades_added"] += 1

                holding["transactions"] = existing_txns

    # 写回
    portfolio_path.write_text(json.dumps(portfolio, ensure_ascii=False, indent=2))
    logger.info("Portfolio synced: %s", changes)

    return changes


# ── 完整同步流程 ─────────────────────────────────────────────────────────────

async def full_sync(
    portfolio_path: str | Path,
    cookie_path: Path | None = None,
    fetch_trades: bool = True,
    trade_days: int = 30,
) -> SyncResult:
    """完整同步流程：加载 cookies → 拉取持仓 → 同步到 portfolio.json。

    Args:
        portfolio_path: portfolio.json 路径
        cookie_path: cookie 文件路径
        fetch_trades: 是否也拉取成交记录
        trade_days: 拉取最近几天的成交记录
    """
    cookie_path = cookie_path or DEFAULT_COOKIE_PATH
    data = load_cookies(cookie_path)
    if not data:
        return SyncResult(error="No saved cookies. Please run login first.")

    if not is_cookie_fresh(cookie_path):
        return SyncResult(error="Cookies expired. Please re-login.", cookie_valid=False)

    cookies = data.get("cookies", [])

    try:
        # 拉取持仓
        assets, holdings = await fetch_holdings(cookies)

        if not holdings and not assets:
            return SyncResult(error="Failed to fetch holdings. Cookies may be invalid.", cookie_valid=False)

        # 拉取成交记录
        trades = []
        if fetch_trades:
            from datetime import timedelta
            start = (datetime.now() - timedelta(days=trade_days)).strftime("%Y-%m-%d")
            trades = await fetch_trade_history(cookies, start_date=start)

        # 同步到文件
        sync_to_portfolio(holdings, assets, portfolio_path, trades)

        return SyncResult(
            assets=assets,
            holdings=holdings,
            trades=trades,
        )

    except Exception as e:
        logger.exception("Full sync failed")
        return SyncResult(error=str(e))


# ── 格式化输出 ──────────────────────────────────────────────────────────────

def format_sync_report(result: SyncResult, changes: dict | None = None) -> str:
    """格式化同步报告。"""
    if result.error:
        return f"❌ 同步失败: {result.error}"

    lines = ["📊 东方财富账户同步完成", ""]

    if result.assets:
        a = result.assets
        lines.append(f"总资产: ¥{a.total_assets:,.2f}")
        lines.append(f"证券市值: ¥{a.market_value:,.2f}")
        lines.append(f"可用资金: ¥{a.available_cash:,.2f}")
        lines.append("")

    if result.holdings:
        lines.append(f"持仓 ({len(result.holdings)} 只):")
        for h in result.holdings:
            emoji = "🟢" if h.profit >= 0 else "🔴"
            lines.append(
                f"  {emoji} {h.name}({h.code}): {h.shares}股 "
                f"¥{h.current_price:.2f} 盈亏¥{h.profit:+,.2f}({h.profit_pct:+.2f}%)"
            )
        lines.append("")

    if result.trades:
        lines.append(f"成交记录 (近30天 {len(result.trades)} 笔):")
        for t in result.trades[-10:]:  # 只显示最近10笔
            emoji = "🟢" if t.action == "sell" else "🔵"
            lines.append(
                f"  {emoji} {t.date} {t.name} {t.action} {t.shares}股 @¥{t.price:.2f}"
            )
        lines.append("")

    if changes:
        if changes.get("updated"):
            lines.append("更新: " + ", ".join(changes["updated"]))
        if changes.get("added"):
            lines.append("新增: " + ", ".join(changes["added"]))
        if changes.get("removed"):
            lines.append("清仓: " + ", ".join(changes["removed"]))
        if changes.get("cash_updated"):
            lines.append("现金已同步")
        if changes.get("trades_added", 0) > 0:
            lines.append(f"新增 {changes['trades_added']} 笔成交记录")

    return "\n".join(lines)


# ── 异步辅助 ─────────────────────────────────────────────────────────────────

import asyncio  # noqa: E402
