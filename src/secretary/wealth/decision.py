"""Position sizing decision framework — probability-weighted expected returns."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PricePosition:
    """Where current price sits relative to 52w range and valuation."""

    current_price: float
    high_52w: float
    low_52w: float
    pe: float | None = None
    # Computed
    pct_from_high: float = 0.0  # negative = below high
    pct_from_low: float = 0.0  # positive = above low
    position_in_range: float = 0.0  # 0.0 = at low, 1.0 = at high
    interpretation: str = ""


@dataclass
class Scenario:
    """A single market scenario with probability and expected return."""

    name: str  # e.g. '继续上涨', '高位震荡', '回调', '大跌'
    probability: float  # 0-1
    expected_return_pct: float
    reasoning: str = ""


@dataclass
class DecisionAnalysis:
    """Full decision analysis result."""

    holding_name: str
    current_price: float
    position: PricePosition
    scenarios: list[Scenario]
    expected_return: float = 0.0  # probability-weighted
    risk_reward_ratio: float = 0.0
    recommendation: str = "hold"  # 'hold'/'reduce'/'add'/'stop_loss'
    reasoning: str = ""


def assess_position(
    current_price: float,
    high_52w: float,
    low_52w: float,
    pe: float | None = None,
) -> PricePosition:
    """Assess where price sits in 52-week range.

    Args:
        current_price: current market price
        high_52w: 52-week high
        low_52w: 52-week low
        pe: optional P/E ratio

    Returns:
        PricePosition with computed metrics
    """
    if high_52w <= low_52w or high_52w <= 0:
        return PricePosition(
            current_price=current_price,
            high_52w=high_52w,
            low_52w=low_52w,
            pe=pe,
            pct_from_high=0.0,
            pct_from_low=0.0,
            position_in_range=0.5,
            interpretation="52周高低数据异常，无法判断位置",
        )

    pct_from_high = (current_price - high_52w) / high_52w * 100
    pct_from_low = (current_price - low_52w) / low_52w * 100 if low_52w > 0 else 0.0
    position_in_range = (current_price - low_52w) / (high_52w - low_52w)
    position_in_range = max(0.0, min(1.0, position_in_range))

    # Generate interpretation
    if position_in_range >= 0.9:
        interp = "处于52周高位区间，接近历史高点，回调风险较大"
    elif position_in_range >= 0.7:
        interp = "处于52周偏高位置，上涨空间有限"
    elif position_in_range >= 0.3:
        interp = "处于52周中间位置，方向不明确"
    elif position_in_range >= 0.1:
        interp = "处于52周偏低位置，具备一定安全边际"
    else:
        interp = "处于52周低位区间，接近历史低点，可能有反弹机会"

    if pe is not None:
        if pe > 50:
            interp += f"；PE={pe:.1f}偏高，估值偏贵"
        elif pe < 15:
            interp += f"；PE={pe:.1f}偏低，估值有吸引力"

    return PricePosition(
        current_price=current_price,
        high_52w=high_52w,
        low_52w=low_52w,
        pe=pe,
        pct_from_high=round(pct_from_high, 2),
        pct_from_low=round(pct_from_low, 2),
        position_in_range=round(position_in_range, 4),
        interpretation=interp,
    )


def build_scenarios(
    position: PricePosition,
    holding_type: str = "stock",
) -> list[Scenario]:
    """Build bull/bear scenarios with probabilities that sum to 1.0.

    Adjusts scenario probabilities based on where price sits in range.

    Args:
        position: current price position assessment
        holding_type: 'stock' or 'etf' (ETFs have lower volatility)

    Returns:
        list of 4 scenarios with probabilities summing to 1.0
    """
    pos = position.position_in_range
    vol = 0.8 if holding_type == "etf" else 1.0  # ETFs are less volatile

    # Near high → more weight on correction
    # Near low → more weight on recovery
    if pos >= 0.8:
        # High position: bearish tilt
        scenarios = [
            Scenario("继续上涨", 0.20, 5.0 * vol, "动能可能延续，但空间有限"),
            Scenario("高位震荡", 0.35, 0.0, "高位横盘消化估值"),
            Scenario("回调", 0.30, -8.0 * vol, "获利盘出逃，估值修复"),
            Scenario("大跌", 0.15, -20.0 * vol, "系统性风险或业绩不及预期"),
        ]
    elif pos >= 0.5:
        # Mid-high: slightly bearish
        scenarios = [
            Scenario("继续上涨", 0.30, 8.0 * vol, "基本面支撑，趋势向好"),
            Scenario("高位震荡", 0.30, 0.0, "等待新的催化剂"),
            Scenario("回调", 0.25, -6.0 * vol, "短期调整消化涨幅"),
            Scenario("大跌", 0.15, -15.0 * vol, "黑天鹅事件冲击"),
        ]
    elif pos >= 0.2:
        # Mid-low: slightly bullish
        scenarios = [
            Scenario("继续上涨", 0.35, 10.0 * vol, "估值修复+业绩改善"),
            Scenario("高位震荡", 0.30, 0.0, "底部反复确认"),
            Scenario("回调", 0.20, -5.0 * vol, "进一步探底但空间有限"),
            Scenario("大跌", 0.15, -12.0 * vol, "基本面恶化"),
        ]
    else:
        # Low position: bullish tilt
        scenarios = [
            Scenario("继续上涨", 0.40, 15.0 * vol, "超跌反弹，估值回归"),
            Scenario("高位震荡", 0.25, 0.0, "底部震荡筑底"),
            Scenario("回调", 0.20, -5.0 * vol, "最后一跌确认底部"),
            Scenario("大跌", 0.15, -15.0 * vol, "基本面进一步恶化"),
        ]

    # Verify probabilities sum to 1.0
    total = sum(s.probability for s in scenarios)
    assert abs(total - 1.0) < 0.01, f"Probabilities must sum to 1.0, got {total}"

    return scenarios


def compute_decision(
    name: str,
    price: float,
    high_52w: float,
    low_52w: float,
    cost_price: float,
    shares: float,
    pe: float | None = None,
    holding_type: str = "stock",
) -> DecisionAnalysis:
    """Full decision analysis pipeline.

    Args:
        name: holding name
        price: current price
        high_52w: 52-week high
        low_52w: 52-week low
        cost_price: average cost price
        shares: number of shares held
        pe: optional P/E ratio
        holding_type: 'stock' or 'etf'

    Returns:
        DecisionAnalysis with recommendation
    """
    position = assess_position(price, high_52w, low_52w, pe)
    scenarios = build_scenarios(position, holding_type)

    # Probability-weighted expected return
    expected_return = sum(s.probability * s.expected_return_pct for s in scenarios)

    # Risk/reward ratio: avg upside / avg downside
    upside = sum(
        s.probability * s.expected_return_pct for s in scenarios if s.expected_return_pct > 0
    )
    downside = abs(
        sum(s.probability * s.expected_return_pct for s in scenarios if s.expected_return_pct < 0)
    )
    risk_reward = upside / downside if downside > 0 else float("inf")

    # Current P&L
    pnl_pct = ((price - cost_price) / cost_price * 100) if cost_price > 0 else 0.0

    # Decision logic
    recommendation, reasoning = _make_recommendation(
        position, expected_return, risk_reward, pnl_pct
    )

    return DecisionAnalysis(
        holding_name=name,
        current_price=price,
        position=position,
        scenarios=scenarios,
        expected_return=round(expected_return, 2),
        risk_reward_ratio=round(risk_reward, 2),
        recommendation=recommendation,
        reasoning=reasoning,
    )


def _make_recommendation(
    position: PricePosition,
    expected_return: float,
    risk_reward: float,
    pnl_pct: float,
) -> tuple[str, str]:
    """Core recommendation logic."""
    pos = position.position_in_range

    # Stop-loss: deep loss and near low
    if pnl_pct <= -15 and pos <= 0.2:
        return "stop_loss", f"已亏损{abs(pnl_pct):.1f}%且处于低位，建议止损控制风险"

    # Reduce: near high with negative expected return
    if pos >= 0.8 and expected_return < 0:
        return (
            "reduce",
            f"处于52周高位(pos={pos:.0%})，期望收益为负({expected_return:.1f}%)，建议减仓",
        )

    # Reduce: high profit, take some off the table
    if pnl_pct >= 25 and pos >= 0.7:
        return "reduce", f"已盈利{pnl_pct:.1f}%且处于高位，建议部分止盈"

    # Add: near low with positive expected return
    if pos <= 0.3 and expected_return > 2.0:
        return (
            "add",
            f"处于52周低位(pos={pos:.0%})，期望收益为正({expected_return:.1f}%)，可考虑加仓",
        )

    # Add: good risk/reward
    if risk_reward >= 2.0 and expected_return > 3.0:
        return "add", f"风险收益比良好({risk_reward:.1f})，期望收益{expected_return:.1f}%，可加仓"

    # Default: hold
    return (
        "hold",
        f"当前位置({pos:.0%})和期望收益({expected_return:.1f}%)不支持明确的加减仓信号，建议持有观望",
    )


def format_decision_report(analysis: DecisionAnalysis) -> str:
    """Format decision analysis as a readable Chinese report."""
    rec_map = {
        "hold": "🟢 继续持有",
        "reduce": "🟡 建议减仓",
        "add": "🔵 建议加仓",
        "stop_loss": "🔴 建议止损",
    }

    lines = [
        f"📊 {analysis.holding_name} 加减仓决策分析",
        f"当前价格: ¥{analysis.current_price:.2f}",
        "",
        "── 价格位置 ──",
        analysis.position.interpretation,
        f"52周区间位置: {analysis.position.position_in_range:.0%} (0%=最低, 100%=最高)",
        f"距高点: {analysis.position.pct_from_high:+.1f}%"
        f"  距低点: {analysis.position.pct_from_low:+.1f}%",
        "",
        "── 情景分析 ──",
    ]

    for s in analysis.scenarios:
        lines.append(
            f"  {s.name}: {s.probability:.0%}概率, 预期{s.expected_return_pct:+.1f}%"
            f"  ({s.reasoning})"
        )

    lines.extend(
        [
            "",
            f"期望收益: {analysis.expected_return:+.2f}%",
            f"风险收益比: {analysis.risk_reward_ratio:.2f}",
            "",
            "── 决策建议 ──",
            f"{rec_map.get(analysis.recommendation, analysis.recommendation)}",
            analysis.reasoning,
        ]
    )

    return "\n".join(lines)
