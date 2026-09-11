"""Risk control checklist — stop-loss, position limits, discipline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RiskCheckResult:
    """Result of a full risk check on the portfolio."""

    holding_name: str = ""
    checks: list[dict[str, Any]] = field(default_factory=list)
    overall_risk: str = "low"  # 'low'/'medium'/'high'/'critical'
    alerts: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# Default risk rules (can be overridden per stock in stock_*.md)
DEFAULT_RULES: dict[str, float] = {
    "stop_loss_pct": -10.0,  # if loss > 10%, trigger stop-loss alert
    "take_profit_pct": 30.0,  # if profit > 30%, consider taking profit
    "max_single_position_pct": 25.0,  # max 25% of total portfolio in one stock
    "max_sector_pct": 40.0,  # max 40% in one sector
}


def check_stop_loss(
    holding: dict[str, Any],
    current_price: float,
    rules: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Check if a holding triggers stop-loss.

    Args:
        holding: dict with keys: code, name, shares, cost_price
        current_price: current market price
        rules: risk rules override (uses DEFAULT_RULES if None)

    Returns:
        dict with keys: name, status ('ok'/'warning'/'alert'), detail
    """
    rules = rules or DEFAULT_RULES
    cost_price = float(holding.get("cost_price", 0))
    name = holding.get("name", holding.get("code", "unknown"))

    if cost_price <= 0:
        return {"name": name, "status": "ok", "detail": "成本价无效，无法判断"}

    pnl_pct = (current_price - cost_price) / cost_price * 100

    stop_loss = rules.get("stop_loss_pct", -10.0)
    take_profit = rules.get("take_profit_pct", 30.0)

    if pnl_pct <= stop_loss:
        return {
            "name": name,
            "status": "alert",
            "detail": f"亏损{abs(pnl_pct):.1f}%，已触发止损线({stop_loss}%)",
        }
    if pnl_pct <= stop_loss / 2:
        return {
            "name": name,
            "status": "warning",
            "detail": f"亏损{abs(pnl_pct):.1f}%，接近止损线({stop_loss}%)",
        }
    if pnl_pct >= take_profit:
        return {
            "name": name,
            "status": "warning",
            "detail": f"盈利{pnl_pct:.1f}%，已超过止盈线({take_profit}%)，考虑部分止盈",
        }

    return {"name": name, "status": "ok", "detail": f"盈亏{pnl_pct:+.1f}%，正常范围"}


def check_position_concentration(
    holding: dict[str, Any],
    current_price: float,
    total_portfolio_value: float,
    rules: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Check if single position exceeds concentration limit.

    Args:
        holding: holding dict
        current_price: current market price
        total_portfolio_value: total portfolio market value
        rules: risk rules override

    Returns:
        dict with keys: name, status, detail
    """
    rules = rules or DEFAULT_RULES
    name = holding.get("name", holding.get("code", "unknown"))
    shares = float(holding.get("shares", 0))

    if total_portfolio_value <= 0:
        return {"name": name, "status": "ok", "detail": "组合总值为零，无法计算集中度"}

    position_value = shares * current_price
    position_pct = position_value / total_portfolio_value * 100
    max_pct = rules.get("max_single_position_pct", 25.0)

    if position_pct > max_pct:
        return {
            "name": name,
            "status": "alert",
            "detail": f"仓位占比{position_pct:.1f}%，超过上限({max_pct}%)",
        }
    if position_pct > max_pct * 0.8:
        return {
            "name": name,
            "status": "warning",
            "detail": f"仓位占比{position_pct:.1f}%，接近上限({max_pct}%)",
        }

    return {"name": name, "status": "ok", "detail": f"仓位占比{position_pct:.1f}%，正常"}


def check_discipline(
    holding: dict[str, Any],
    price_position: float,
    action: str = "hold",
) -> list[dict[str, Any]]:
    """Check if user is violating trading discipline.

    Args:
        holding: holding dict
        price_position: position in 52w range (0.0-1.0)
        action: intended action ('buy'/'sell'/'hold')

    Returns:
        list of check dicts
    """
    name = holding.get("name", holding.get("code", "unknown"))
    checks: list[dict[str, Any]] = []

    if action == "buy" and price_position >= 0.85:
        checks.append(
            {
                "name": f"{name} 追高风险",
                "status": "alert",
                "detail": f"在52周高位({price_position:.0%})买入属于追高行为",
            }
        )

    if action == "sell" and price_position <= 0.15:
        checks.append(
            {
                "name": f"{name} 恐慌卖出风险",
                "status": "alert",
                "detail": f"在52周低位({price_position:.0%})卖出可能是恐慌性抛售",
            }
        )

    if action == "buy" and price_position >= 0.7:
        checks.append(
            {
                "name": f"{name} 高位加仓提醒",
                "status": "warning",
                "detail": f"在52周偏高位置({price_position:.0%})加仓需谨慎",
            }
        )

    if action == "sell" and price_position <= 0.3:
        checks.append(
            {
                "name": f"{name} 低位减仓提醒",
                "status": "warning",
                "detail": f"在52周偏低位置({price_position:.0%})减仓可能割在底部",
            }
        )

    if not checks:
        checks.append(
            {
                "name": f"{name} 纪律检查",
                "status": "ok",
                "detail": "交易行为符合纪律要求",
            }
        )

    return checks


def run_risk_check(
    portfolio_data: list[dict[str, Any]],
    prices: dict[str, float],
    total_value: float,
    rules: dict[str, float] | None = None,
) -> RiskCheckResult:
    """Run full risk check on portfolio.

    Args:
        portfolio_data: list of holding dicts
        prices: dict of code -> current price
        total_value: total portfolio market value
        rules: risk rules override

    Returns:
        RiskCheckResult with all checks, alerts, and recommendations
    """
    rules = rules or DEFAULT_RULES
    all_checks: list[dict[str, Any]] = []
    alerts: list[str] = []
    recommendations: list[str] = []

    for h in portfolio_data:
        code = h.get("code", "")
        current_price = prices.get(code, float(h.get("cost_price", 0)))

        # Stop-loss check
        sl_check = check_stop_loss(h, current_price, rules)
        all_checks.append(sl_check)
        if sl_check["status"] == "alert":
            alerts.append(f"🔴 {sl_check['detail']}")

        # Position concentration
        pc_check = check_position_concentration(h, current_price, total_value, rules)
        all_checks.append(pc_check)
        if pc_check["status"] == "alert":
            alerts.append(f"⚠️ {pc_check['detail']}")

    # Sector concentration would need sector data — skip if not available
    # Overall risk level
    alert_count = sum(1 for c in all_checks if c["status"] == "alert")
    warning_count = sum(1 for c in all_checks if c["status"] == "warning")

    if alert_count >= 3:
        overall = "critical"
    elif alert_count >= 1:
        overall = "high"
    elif warning_count >= 2:
        overall = "medium"
    else:
        overall = "low"

    # Generate recommendations
    if alert_count > 0:
        recommendations.append("存在风控警报，建议立即检查并处理")
    if warning_count > 0:
        recommendations.append("存在风险预警，建议关注相关持仓")
    if not alerts and not (warning_count >= 2):
        recommendations.append("风控检查通过，持仓风险可控")

    return RiskCheckResult(
        checks=all_checks,
        overall_risk=overall,
        alerts=alerts,
        recommendations=recommendations,
    )


def format_risk_report(result: RiskCheckResult) -> str:
    """Format risk report in Chinese."""
    risk_emoji = {
        "low": "🟢",
        "medium": "🟡",
        "high": "🟠",
        "critical": "🔴",
    }

    lines = [
        f"{risk_emoji.get(result.overall_risk, '⚪')} 风控检查报告",
        f"整体风险等级: {result.overall_risk.upper()}",
        "",
    ]

    if result.alerts:
        lines.append("── 警报 ──")
        for a in result.alerts:
            lines.append(f"  {a}")
        lines.append("")

    lines.append("── 检查详情 ──")
    for c in result.checks:
        status_icon = {"ok": "✅", "warning": "⚠️", "alert": "🔴"}.get(c["status"], "❓")
        lines.append(f"  {status_icon} {c['name']}: {c['detail']}")
    lines.append("")

    if result.recommendations:
        lines.append("── 建议 ──")
        for r in result.recommendations:
            lines.append(f"  • {r}")

    return "\n".join(lines)
