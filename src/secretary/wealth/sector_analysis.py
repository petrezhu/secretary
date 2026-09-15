"""
持仓板块分类分析 — 按牛市/熊市相关性强弱归纳，给出板块级仓位建议。

分类体系:
  银行证券类   — 高股息防守，牛市滞涨熊市抗跌
  中证宽基ETF  — 跟踪大盘，β≈1
  港股宽基ETF  — 跟踪恒生，受外资/南向影响
  消费红利股   — 内需消费，中等β，分红防御
  科技股       — 高β进攻，牛市弹性大熊市回撤深
  纳斯达克ETF  — 跟踪美股科技，受美联储/汇率影响
  其他大盘股   — 周期/资源，受大宗商品驱动
  黄金债券     — 避险防守，负相关于风险资产
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"

# ── 板块分类规则 ─────────────────────────────────────────────────────────────

SECTOR_RULES: dict[str, dict[str, Any]] = {
    "银行证券": {
        "codes": ["sh601398", "sh601288", "sh601988", "sh601328", "sh600036",
                  "sh601688", "sh601211", "sh600030", "sz000776"],
        "keywords": ["银行", "工商", "农业", "建设", "中国银行", "招商",
                     "证券", "国泰君安", "中信证券", "华泰"],
        "index": "sh000134",       # 中证银行 (显示用)
        "index_name": "中证银行",
        "etf_52w": "sh512800",     # 银行ETF (52周数据源)
        "bull_beta": 0.5,
        "bear_beta": 0.3,
        "description": "高股息防守板块，牛市滞涨但熊市抗跌",
    },
    "中证宽基": {
        "codes": ["sh510300", "sh510500", "sh510050", "sz159919"],
        "keywords": ["沪深300", "中证500", "上证50", "创业板"],
        "index": "sh000300",
        "index_name": "沪深300",
        "etf_52w": "sh510300",     # 沪深300ETF
        "bull_beta": 1.0,
        "bear_beta": 1.0,
        "description": "大盘宽基，β≈1，市场风向标",
    },
    "港股宽基": {
        "codes": ["sz159185", "sh513050", "sh513180", "sh513060"],
        "keywords": ["港股", "恒生", "HK", "中概"],
        "index": "sh513050",       # 中概互联网ETF (代替恒生指数)
        "index_name": "港股宽基",
        "etf_52w": "sh513050",
        "bull_beta": 1.2,
        "bear_beta": 1.3,
        "description": "港股宽基，受外资/南向资金驱动，波动大",
    },
    "消费红利": {
        "codes": ["sh600993", "sh603882", "sh600519", "sz000858", "sz000568"],
        "keywords": ["茅台", "五粮液", "泸州", "医药", "消费"],
        "index": "sh000932",
        "index_name": "中证消费",
        "etf_52w": "sh510150",     # 消费ETF
        "bull_beta": 0.8,
        "bear_beta": 0.6,
        "description": "内需消费+医药，中等β，分红防御属性",
    },
    "科技成长": {
        "codes": ["sz002273", "sz300793", "sz300442", "sz300058",
                  "sh588000", "sh588050", "sh589850"],
        "keywords": ["蓝色光标", "科技", "AI", "科创"],
        "index": "sz399006",
        "index_name": "创业板指",
        "etf_52w": "sz159915",     # 创业板ETF
        "bull_beta": 1.8,
        "bear_beta": 1.5,
        "description": "高β进攻板块，牛市弹性大但熊市回撤深",
    },
    "纳斯达克": {
        "codes": ["sz159659", "f_021778", "f_270042", "sz159941", "sh513100"],
        "keywords": ["纳指", "纳斯达克", "NDX", "QQQ"],
        "index": "us.NDX",
        "index_name": "纳斯达克100",
        "etf_52w": "us.NDX",       # 直接用指数
        "bull_beta": 1.5,
        "bear_beta": 1.2,
        "description": "美股科技，受美联储政策/汇率影响",
    },
    "其他大盘": {
        "codes": ["sh601666", "sh601088", "sh600028", "sh601857"],
        "keywords": ["神华", "中石化", "中石油", "煤炭", "能源"],
        "index": "sh000001",
        "index_name": "上证指数",
        "etf_52w": "sh510300",     # 用沪深300ETF近似
        "bull_beta": 0.9,
        "bear_beta": 0.8,
        "description": "周期/资源股，受大宗商品和经济周期驱动",
    },
    "黄金": {
        "codes": ["sz159934", "sh518880"],
        "keywords": ["黄金", "积存金", "gold"],
        "index": "sh518880",
        "index_name": "黄金ETF",
        "etf_52w": "sh518880",
        "bull_beta": -0.3,
        "bear_beta": -0.5,
        "description": "避险资产，与风险资产负相关，牛市减配熊市超配",
    },
    "债券": {
        "codes": ["sh511010", "sh511260", "f_011062"],
        "keywords": ["债券", "国债", "中债"],
        "index": "sh511010",
        "index_name": "国债ETF",
        "etf_52w": "sh511010",
        "bull_beta": -0.2,
        "bear_beta": -0.3,
        "description": "固收防守，低波动低收益，熊市中超配",
    },
}


def _load_sector_overrides(local_file: Path | None = None) -> None:
    """Merge extra keywords from config/local_stocks.json into SECTOR_RULES.

    ``local_file`` override exists for tests; defaults to the project config.
    """
    path = local_file if local_file is not None else _CONFIG_DIR / "local_stocks.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        overrides = data.get("sector_overrides", {})
        for sector_name, override in overrides.items():
            if sector_name in SECTOR_RULES:
                extra = override.get("extra_keywords", [])
                existing = SECTOR_RULES[sector_name]["keywords"]
                for kw in extra:
                    if kw not in existing:
                        existing.append(kw)
    except Exception:
        pass


_load_sector_overrides()


def classify_holding(code: str, name: str) -> str:
    """将单只持仓归类到板块。"""
    for sector, rule in SECTOR_RULES.items():
        # 精确代码匹配
        if code in rule["codes"]:
            return sector
        # 关键词匹配
        for kw in rule["keywords"]:
            if kw in name:
                return sector
    return "其他大盘"  # 默认归类


def classify_all(holdings: list[dict]) -> dict[str, list[dict]]:
    """将所有持仓按板块分组。

    处理特殊资产类型:
    - gold_accumulate: 用 grams > 0 判断有效持仓
    - fund_otc: 用 shares > 0 或 principal > 0 判断有效持仓
    """
    groups: dict[str, list[dict]] = {}
    for h in holdings:
        # 判断持仓是否有效
        shares = h.get("shares") or 0
        grams = h.get("grams") or 0
        principal = h.get("principal") or 0
        htype = h.get("type", "")

        has_position = False
        if htype == "gold_accumulate":
            has_position = grams > 0
        elif htype == "fund_otc":
            # fund_otc with direct market_value doesn't need shares
            has_position = shares > 0 or principal > 0 or h.get("market_value") is not None
        else:
            has_position = shares > 0

        if not has_position:
            continue

        code = h.get("code", "")
        name = h.get("name", "")
        sector = classify_holding(code, name)
        groups.setdefault(sector, []).append(h)
    return groups


# ── 指数行情获取 ─────────────────────────────────────────────────────────────

def fetch_index_quotes(index_codes: list[str]) -> dict[str, dict]:
    """批量获取指数行情（腾讯接口）。

    返回: {code: {price, change_pct, high_52w, low_52w}}
    """
    if not index_codes:
        return {}

    query = ",".join(index_codes)
    try:
        resp = requests.get(f"http://qt.gtimg.cn/q={query}", timeout=10)
        raw = resp.text
    except Exception:
        return {}

    results = {}
    for line in raw.strip().split("\n"):
        line = line.strip().rstrip(";")
        if "=" not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        val = line.split("=")[1].strip('"')
        parts = val.split("~")
        if len(parts) < 5:
            continue
        try:
            price = float(parts[3])
            if price <= 0:
                continue
            results[key] = {
                "name": parts[1],
                "price": price,
                "change_pct": float(parts[32]) if len(parts) > 32 and parts[32] else 0.0,
                "high_52w": float(parts[47]) if len(parts) > 47 and parts[47] else 0.0,
                "low_52w": float(parts[48]) if len(parts) > 48 and parts[48] else 0.0,
            }
        except (ValueError, IndexError):
            continue

    return results


# ── 板块位置计算 ─────────────────────────────────────────────────────────────

@dataclass
class SectorPosition:
    """板块在牛市/熊市坐标系中的位置。"""
    sector: str
    description: str
    index_name: str
    index_price: float = 0.0
    index_change_pct: float = 0.0
    position_in_52w: float = 0.0    # 0=52w低, 1=52w高
    bull_beta: float = 1.0
    bear_beta: float = 1.0
    # 持仓汇总
    total_cost: float = 0.0
    total_value: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    holding_count: int = 0
    # 建议
    market_phase: str = ""      # "bull" / "bear" / "neutral"
    recommendation: str = ""    # "overweight" / "equal" / "underweight"
    reasoning: str = ""


def assess_market_phase(position_in_52w: float) -> str:
    """判断市场阶段。"""
    if position_in_52w >= 0.75:
        return "bull"    # 牛市高位
    elif position_in_52w <= 0.25:
        return "bear"    # 熊市低位
    else:
        return "neutral"  # 震荡中性


def compute_sector_recommendation(
    sector: str,
    position_in_52w: float,
    bull_beta: float,
    bear_beta: float,
    pnl_pct: float,
) -> tuple[str, str]:
    """基于板块位置和β给出仓位建议。

    逻辑:
    - 牛市高位(pos>0.75): 低β防守板块overweight, 高β进攻板块underweight
    - 熊市低位(pos<0.25): 高β进攻板块overweight(抄底), 低β防守板块equal
    - 震荡中性: 按当前盈亏调整 — 盈利多减仓, 亏损多观望
    """
    phase = assess_market_phase(position_in_52w)

    if phase == "bull":
        # 牛市高位：防守为王
        if bull_beta <= 0.5:
            return "overweight", f"牛市高位(pos={position_in_52w:.0%})，{sector}防守属性强，可超配"
        elif bull_beta >= 1.3:
            if pnl_pct > 20:
                return "underweight", f"牛市高位+高β板块+已盈利{pnl_pct:.0f}%，建议减仓锁利"
            else:
                return "equal", "牛市高位+高β板块，仓位持平，设止盈纪律"
        else:
            return "equal", "牛市高位，中性β板块保持标配"

    elif phase == "bear":
        # 熊市低位：进攻抄底
        if bear_beta >= 1.3:
            if pnl_pct < -15:
                return "equal", f"熊市低位但已亏损{pnl_pct:.0f}%，不加仓，等待企稳信号"
            else:
                return "overweight", (
                    f"熊市低位(pos={position_in_52w:.0%})，"
                    f"{sector}弹性大，可逢低布局"
                )
        elif bear_beta <= 0.3 or bear_beta < 0:
            return "underweight", f"熊市低位，{sector}防守属性强但弹性不足，减配转攻"
        else:
            return "equal", "熊市低位，中性板块保持标配"

    else:
        # 震荡中性
        if pnl_pct > 15:
            return "underweight", f"震荡市+已盈利{pnl_pct:.0f}%，可适当减仓兑现"
        elif pnl_pct < -10:
            return "equal", f"震荡市+亏损{pnl_pct:.0f}%，观望不动"
        else:
            return "equal", "震荡市，保持当前仓位"


# ── 主入口 ───────────────────────────────────────────────────────────────────

def analyze_sector_positions(portfolio_path: str) -> list[SectorPosition]:
    """分析所有持仓的板块位置和建议。"""
    with open(portfolio_path, 'r', encoding='utf-8') as f:
        portfolio = json.load(f)

    holdings = portfolio.get("holdings", [])
    groups = classify_all(holdings)

    # 获取所有相关指数行情（显示用）+ 52周数据（从跟踪ETF获取）
    index_codes = []
    etf_codes = []
    for sector, rule in SECTOR_RULES.items():
        if sector in groups:
            index_codes.append(rule["index"])
            etf_code = rule.get("etf_52w", rule["index"])
            if etf_code not in etf_codes:
                etf_codes.append(etf_code)
    index_quotes = fetch_index_quotes(index_codes)
    etf_quotes = fetch_index_quotes(etf_codes)

    results = []
    for sector, rule in SECTOR_RULES.items():
        if sector not in groups:
            continue

        sector_holdings = groups[sector]
        index_code = rule["index"]
        etf_code = rule.get("etf_52w", index_code)
        quote = index_quotes.get(index_code, {})
        etf_quote = etf_quotes.get(etf_code, {})

        # 持仓汇总（适配不同资产类型）
        total_cost = 0.0
        total_value = 0.0
        for h in sector_holdings:
            htype = h.get("type", "")
            if htype == "gold_accumulate":
                # 积存金: 用 cost_total 和 grams * cost_per_gram
                gold_value = h.get("cost_total") or (
                    h.get("grams", 0) * (h.get("cost_per_gram") or 0)
                )
                total_cost += gold_value
                total_value += gold_value
            elif htype == "fund_otc" and (h.get("shares") or 0) == 0:
                # 场外基金(债券类): 用 principal
                total_cost += h.get("principal") or 0
                total_value += h.get("principal") or 0
            else:
                # 股票/ETF/其他
                shares = h.get("shares") or 0
                total_cost += shares * (h.get("cost_price") or 0)
                total_value += shares * (h.get("current_price") or h.get("cost_price") or 0)
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0

        # 52周位置（从跟踪ETF获取）
        high = etf_quote.get("high_52w", 0)
        low = etf_quote.get("low_52w", 0)
        price_etf = etf_quote.get("price", 0)
        if high > low > 0 and price_etf > 0:
            position = max(0.0, min(1.0, (price_etf - low) / (high - low)))
        else:
            position = 0.5  # 无数据时默认中间

        # 生成建议
        rec, reasoning = compute_sector_recommendation(
            sector, position, rule["bull_beta"], rule["bear_beta"], total_pnl_pct
        )

        results.append(SectorPosition(
            sector=sector,
            description=rule["description"],
            index_name=rule["index_name"],
            index_price=quote.get("price", 0),
            index_change_pct=quote.get("change_pct", 0.0),
            position_in_52w=position,
            bull_beta=rule["bull_beta"],
            bear_beta=rule["bear_beta"],
            total_cost=round(total_cost, 2),
            total_value=round(total_value, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 2),
            holding_count=len(sector_holdings),
            market_phase=assess_market_phase(position),
            recommendation=rec,
            reasoning=reasoning,
        ))

    return results


def format_sector_report(positions: list[SectorPosition]) -> str:
    """格式化板块分析报告（纯文本，供日报使用）。"""
    if not positions:
        return "无持仓数据"

    lines = []

    # 牛熊判断
    phases = [p.market_phase for p in positions]
    bull_count = phases.count("bull")
    bear_count = phases.count("bear")

    if bull_count > bear_count + 1:
        market_view = "多数板块处于牛市高位，防守为主"
    elif bear_count > bull_count + 1:
        market_view = "多数板块处于熊市低位，可逢低布局进攻板块"
    else:
        market_view = "板块分化，均衡配置"

    lines.append(f"市场综合: {market_view}")
    lines.append("")

    # 板块明细
    emoji_map = {"overweight": "⬆️", "equal": "➡️", "underweight": "⬇️"}
    phase_emoji = {"bull": "🐂", "bear": "🐻", "neutral": "↔️"}

    for p in positions:
        rec_emoji = emoji_map.get(p.recommendation, "")
        ph_emoji = phase_emoji.get(p.market_phase, "")

        lines.append(
            f"{rec_emoji} {p.sector}({p.index_name}): "
            f"指数¥{p.index_price:.2f} {p.index_change_pct:+.2f}% "
            f"52周位置{p.position_in_52w:.0%} {ph_emoji}"
        )
        lines.append(
            f"   持仓{p.holding_count}只 市值¥{p.total_value:,.0f} "
            f"盈亏¥{p.total_pnl:+,.0f}({p.total_pnl_pct:+.1f}%) "
            f"→ {p.recommendation}"
        )
        lines.append(f"   {p.reasoning}")
        lines.append("")

    return "\n".join(lines)


def format_sector_report_html(positions: list[SectorPosition]) -> str:
    """格式化板块分析报告（HTML，供日报邮件）。"""
    if not positions:
        return "无持仓数据"

    lines = []

    phases = [p.market_phase for p in positions]
    bull_count = phases.count("bull")
    bear_count = phases.count("bear")

    if bull_count > bear_count + 1:
        market_view = "多数板块处于牛市高位，防守为主"
    elif bear_count > bull_count + 1:
        market_view = "多数板块处于熊市低位，可逢低布局进攻板块"
    else:
        market_view = "板块分化，均衡配置"

    lines.append(f"• 市场综合: {market_view}<br/>")

    emoji_map = {"overweight": "⬆️超配", "equal": "➡️标配", "underweight": "⬇️减配"}
    phase_emoji = {"bull": "🐂", "bear": "🐻", "neutral": "↔️"}

    for p in positions:
        rec_text = emoji_map.get(p.recommendation, p.recommendation)
        ph_emoji = phase_emoji.get(p.market_phase, "")

        lines.append(
            f"• {p.sector}({p.index_name}): "
            f"指数¥{p.index_price:.2f} {p.index_change_pct:+.2f}% "
            f"52周{p.position_in_52w:.0%} {ph_emoji} "
            f"持仓{p.holding_count}只 ¥{p.total_value:,.0f} "
            f"盈亏{p.total_pnl_pct:+.1f}% → {rec_text}"
        )
        lines.append(f"  {p.reasoning}<br/>")

    return "<br/>".join(lines)
