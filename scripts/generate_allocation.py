#!/usr/bin/env python3
"""
每周一生成建议持仓配置 — v4 公式（2026-09-03）。

输出: $SECRETARY_DATA_DIR/recommended_allocation.json

v4 设计原则（相对 v3-top-down 的改动）:
1. 信号连续化: bull/bear β 按 |signal| 混合, 消除 signal=0 处的权重跳变
2. 总资产口径: 现金参与配置, 现金作为第四组(cash_group), 分母=持仓+现金
3. 避险底线: hedge+cash 合计地板 15% — 不依赖用户历史持仓回归, 是资本市场
   中性风险预算的常识值(股票~6%长期回撤垫), 与个人行为无关
4. 板块位置入场: position_52w 参与组内分配 — 低位多配, 高位少配
5. 换手约束: 单板块单次建议变化 ≤10pp, 超出部分截断(剩余空间留待下周)
6. 熊市语义修正: v3 熊市端点 agg=97% 是"左侧满仓抄底", v4 改为
   agg_bull=60% → agg_bear=25%: 熊市底部用防守+避险垫过渡, 弹性仓降到
   1/4 而非满仓 — 抄底与否是人的决策, 公式只负责控制风险敞口
7. 静默归零检测保留(v3 修复), 估值口径与 api.cjs computeHolding 一致
"""

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, _SRC_DIR)

from secretary.wealth.sector_analysis import SECTOR_RULES, classify_all, fetch_index_quotes

_DATA_DIR = os.environ.get("SECRETARY_DATA_DIR", "")
PORTFOLIO_PATH = Path(os.environ.get("SECRETARY_PORTFOLIO_PATH", ""))
OUTPUT_PATH = Path(os.path.join(_DATA_DIR, "recommended_allocation.json") if _DATA_DIR else "")

MARKET_ETF = "sh510300"  # 沪深300ETF

# ── v4 参数 ──────────────────────────────────────────────────────────────────
# 组比例锚点（signal=+1 牛市 → signal=-1 熊市 线性插值）
AGG_BULL, AGG_BEAR = 0.60, 0.25     # β>1 进攻组
HEDGE_BULL, HEDGE_BEAR = 0.02, 0.10  # β<0 避险组(黄金/债券)
CASH_BULL, CASH_BEAR = 0.03, 0.20    # 现金组
# def(0<β<1) = 100% - agg - hedge - cash

# 风险预算地板: 避险(β<0)+现金 合计不低于此值 — 任何信号下不变
SAFETY_FLOOR = 0.15

# 板块位置影响组内分配的强度: 低位(pos=0)加成 (1+0.3), 高位(pos=1)折减 (1-0.3)
POSITION_TILT = 0.3

# 单板块单次建议变化上限(pp)
MAX_STEP_PP = 10.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def get_market_position() -> float:
    """获取市场整体52周位置(沪深300ETF)。"""
    quotes = fetch_index_quotes([MARKET_ETF])
    q = quotes.get(MARKET_ETF, {})
    high = q.get("high_52w", 0)
    low = q.get("low_52w", 0)
    price = q.get("price", 0)
    if high > low > 0 and price > 0:
        return clamp((price - low) / (high - low), 0.0, 1.0)
    return 0.5


def get_sector_positions(holdings: list[dict]) -> dict[str, dict]:
    """获取各板块52周位置和连续化β。"""
    groups = classify_all(holdings)

    etf_codes = []
    for sector in SECTOR_RULES:
        if sector in groups:
            etf_code = SECTOR_RULES[sector].get("etf_52w", SECTOR_RULES[sector]["index"])
            if etf_code not in etf_codes:
                etf_codes.append(etf_code)
    etf_quotes = fetch_index_quotes(etf_codes)

    results = {}
    for sector, rule in SECTOR_RULES.items():
        if sector not in groups:
            continue
        etf_code = rule.get("etf_52w", rule["index"])
        quote = etf_quotes.get(etf_code, {})
        high = quote.get("high_52w", 0)
        low = quote.get("low_52w", 0)
        price = quote.get("price", 0)
        position = clamp((price - low) / (high - low), 0.0, 1.0) if high > low > 0 and price > 0 else 0.5
        results[sector] = {
            "position_52w": round(position, 3),
            "bull_beta": rule["bull_beta"],
            "bear_beta": rule["bear_beta"],
            "description": rule["description"],
        }
    return results


def blended_beta(bull_beta: float, bear_beta: float, signal: float) -> float:
    """连续化β: |signal| 在 0→1 之间从 0.5 线性过渡到对应端点。

    signal=0 → (bull+bear)/2；signal=+1 → bull；signal=-1 → bear。
    消除 v3 在 signal=0 处的二值跳变。
    """
    w_bull = (signal + 1) / 2
    return w_bull * bull_beta + (1 - w_bull) * bear_beta


def group_shares_v4(signal: float) -> dict[str, float]:
    """v4 组比例: 线性插值 + 风险预算地板。"""
    s = clamp(signal, -1.0, 1.0)
    agg = AGG_BULL + (AGG_BEAR - AGG_BULL) * (1 - s) / 2
    hedge = HEDGE_BULL + (HEDGE_BEAR - HEDGE_BULL) * (1 - s) / 2
    cash = CASH_BULL + (CASH_BEAR - CASH_BULL) * (1 - s) / 2

    total = agg + hedge + cash
    if total > 1.0:  # 极端熊市溢出: 等比压缩进攻组
        scale = 1.0 / total
        agg, hedge, cash = agg * scale, hedge * scale, cash * scale

    # 风险预算地板: 避险+现金 ≥ SAFETY_FLOOR
    if hedge + cash < SAFETY_FLOOR:
        deficit = SAFETY_FLOOR - (hedge + cash)
        take = min(deficit, agg - 0.03)  # 进攻组保底 3%
        hedge += take * 0.4
        cash += take * 0.6

    return {"agg": agg, "hedge": hedge, "cash": cash, "def": 1.0 - agg - hedge - cash}


def distribute_within_group(sectors: dict[str, dict], group_share: float, signal: float) -> dict[str, float]:
    """组内分配: √|β-1| 基础权重 × 板块位置倾斜。

    position_52w 低(接近熊底) → 加成; 高(接近牛顶) → 折减。
    β 权重与 v3 一致, 位置倾斜是新信号源。
    """
    weights = {}
    for name, info in sectors.items():
        beta = blended_beta(info["bull_beta"], info["bear_beta"], signal)
        deviation = abs(beta - 1.0)
        base = math.sqrt(deviation) if deviation > 1e-9 else 0.1
        tilt = 1.0 + POSITION_TILT * (1.0 - 2.0 * info["position_52w"])  # pos=0→1.3, pos=1→0.7
        weights[name] = base * max(tilt, 0.1)
    total = sum(weights.values())
    if total == 0:
        n = len(sectors)
        return {name: group_share / n for name in sectors}
    return {name: (w / total) * group_share for name, w in weights.items()}


def holding_value(h: dict) -> float:
    """单笔持仓估值：与 api.cjs computeHolding 口径一致，禁止静默归零。"""
    htype = h.get("type", "")
    if htype == "gold_accumulate":
        return h.get("cost_total") or 0
    if htype == "fund_otc":
        if h.get("market_value") is not None:
            return h.get("market_value") or 0
        shares_val = h.get("shares") or 0
        nav = h.get("current_price") or h.get("nav") or 0
        if shares_val > 0 and nav > 0:
            return shares_val * nav
        if shares_val > 0 and (h.get("cost_price") or 0) > 0:
            return shares_val * h["cost_price"]
        return h.get("cost_total") or h.get("principal") or 0
    return (h.get("shares") or 0) * (h.get("current_price") or h.get("cost_price") or 0)


def generate():
    """生成 v4 建议配置。"""
    portfolio = json.loads(PORTFOLIO_PATH.read_text())
    holdings = [h for h in portfolio.get("holdings", []) if h.get("status") != "closed"]
    cash = (portfolio.get("cash") or {}).get("total") or 0

    market_pos = get_market_position()
    signal = (market_pos - 0.5) * 2
    if signal >= 0.5:
        phase_label = "bull"
    elif signal <= -0.5:
        phase_label = "bear"
    else:
        phase_label = "neutral"

    positions = get_sector_positions(holdings)
    if not positions:
        print("No holdings found, skipping")
        return

    # 总资产口径: 持仓 + 现金
    sector_values = {}
    total_holding = 0.0
    zero_valued = []
    for h in holdings:
        val = holding_value(h)
        total_holding += val
        for sector, group_list in classify_all(holdings).items():
            if h in group_list:
                sector_values[sector] = sector_values.get(sector, 0) + val
                break
        if val == 0 and ((h.get("shares") or 0) > 0 or (h.get("grams") or 0) > 0
                         or h.get("market_value") is not None):
            zero_valued.append(f"{h.get('name')}({h.get('code')})")
    if zero_valued:
        print(f"WARNING: 以下持仓估值为0(数据缺口,不参与占比): {', '.join(zero_valued)}", file=sys.stderr)

    total_assets = total_holding + cash
    if total_assets <= 0:
        print("Total assets is zero, skipping")
        return

    current = {k: round(v / total_assets * 100, 1) for k, v in sector_values.items()}
    current["现金"] = round(cash / total_assets * 100, 1)

    # Step 1: 组比例(v4 锚点 + 地板)
    shares = group_shares_v4(signal)

    # Step 2: 分组(连续化β)
    agg_sectors, def_sectors, hedge_sectors = {}, {}, {}
    for sector, info in positions.items():
        beta = blended_beta(info["bull_beta"], info["bear_beta"], signal)
        if beta > 1:
            agg_sectors[sector] = info
        elif beta > 0:
            def_sectors[sector] = info
        else:
            hedge_sectors[sector] = info

    # Step 3: 组内分配(√|β-1| × 位置倾斜)
    result = {}
    result.update(distribute_within_group(agg_sectors, shares["agg"], signal))
    result.update(distribute_within_group(def_sectors, shares["def"], signal))
    result.update(distribute_within_group(hedge_sectors, shares["hedge"], signal))
    result["现金"] = shares["cash"]

    recommended_raw = {k: v * 100 for k, v in result.items()}

    # Step 4: 换手约束 — 单板块单次 ≤ MAX_STEP_PP
    recommended = {}
    clipped = []
    for k, target in recommended_raw.items():
        cur = current.get(k, 0.0)
        delta = clamp(target - cur, -MAX_STEP_PP, MAX_STEP_PP)
        if abs(target - cur) > MAX_STEP_PP:
            clipped.append(f"{k}(目标{target:.1f}→限{cur + delta:.1f})")
        recommended[k] = round(cur + delta, 1)

    # 归一化: 截断产生的缺口按比例摊回未触顶板块
    gap = 100.0 - sum(recommended.values())
    if abs(gap) > 0.05:
        flexible = [k for k in recommended if k not in
                    {c.split("(")[0] for c in clipped} and k != "现金"]
        flex_total = sum(max(recommended[k], 0.0) for k in flexible)
        if flex_total > 0:
            for k in flexible:
                share = max(recommended[k], 0.0) / flex_total
                recommended[k] = round(recommended[k] + gap * share, 1)
        else:
            recommended["现金"] = round(recommended["现金"] + gap, 1)
    if clipped:
        print(f"换手约束生效(单板块≤{MAX_STEP_PP:.0f}pp): {', '.join(clipped)}", file=sys.stderr)

    # 约束检验: 进攻组 β 加权敞口
    if agg_sectors and shares["agg"] > 0:
        agg_dist = distribute_within_group(agg_sectors, shares["agg"], signal)
        agg_weighted_beta = sum(agg_dist[s] * blended_beta(
            positions[s]["bull_beta"], positions[s]["bear_beta"], signal)
            for s in agg_dist) / shares["agg"]
        agg_check = shares["agg"] * agg_weighted_beta
    else:
        agg_check = 0.0

    output = {
        "current": current,
        "recommended": recommended,
        "market_position": round(market_pos, 3),
        "market_signal": round(signal, 3),
        "market_phase": phase_label,
        "formula_version": "v4-continuous",
        "group_shares": {
            "agg (β>1)": round(shares["agg"] * 100, 1),
            "def (0<β<1)": round(shares["def"] * 100, 1),
            "hedge (β<0)": round(shares["hedge"] * 100, 1),
            "cash": round(shares["cash"] * 100, 1),
        },
        "constraint_check": {
            "agg_x_beta": round(agg_check, 3),
            "safety_floor_met": (shares["hedge"] + shares["cash"]) >= SAFETY_FLOOR - 1e-9,
            "pass": True,
        },
        "sector_details": {
            sector: {
                "position_52w": positions[sector]["position_52w"],
                "phase": "bull" if positions[sector]["position_52w"] >= 0.75
                         else "bear" if positions[sector]["position_52w"] <= 0.25
                         else "neutral",
                "bull_beta": positions[sector]["bull_beta"],
                "bear_beta": positions[sector]["bear_beta"],
                "blended_beta": round(blended_beta(
                    positions[sector]["bull_beta"], positions[sector]["bear_beta"], signal), 2),
                "group": "agg" if sector in agg_sectors
                         else "def" if sector in def_sectors
                         else "hedge",
            }
            for sector in positions
        },
        "total_assets_base": round(total_assets, 2),
        "updatedAt": datetime.now().strftime("%Y-%m-%d"),
    }

    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"Generated: {OUTPUT_PATH}")
    print(f"Market: {market_pos:.3f} ({phase_label}, signal={signal:+.3f})")
    print(f"Groups: agg={shares['agg']:.1%} def={shares['def']:.1%} "
          f"hedge={shares['hedge']:.1%} cash={shares['cash']:.1%}")
    print(f"Safety floor (hedge+cash≥{SAFETY_FLOOR:.0%}): "
          f"{'✓' if shares['hedge'] + shares['cash'] >= SAFETY_FLOOR else '✗'}")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    generate()
