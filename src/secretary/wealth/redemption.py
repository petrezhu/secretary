"""OTC fund redemption workflow — T+1 price confirmation and tracking."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PendingRedemption:
    """A pending OTC fund redemption."""

    fund_code: str
    fund_name: str
    shares_submitted: float
    date_submitted: str  # YYYY-MM-DD
    confirm_date: str  # YYYY-MM-DD (T+1)
    status: str  # 'pending'/'confirmed'/'failed'
    confirm_nav: float | None = None  # filled after confirmation
    confirm_amount: float | None = None
    realized_pnl: float | None = None
    note: str = ""


class RedemptionTracker:
    """Track pending redemptions and confirm them when NAV is available."""

    def __init__(self, portfolio_path: str | Path):
        self.portfolio_path = Path(portfolio_path)

    def _load_portfolio(self) -> dict:
        """Load portfolio JSON as dict."""
        import json

        if not self.portfolio_path.exists():
            logger.warning("Portfolio file not found: %s", self.portfolio_path)
            return {}
        with open(self.portfolio_path, encoding="utf-8") as f:
            return json.load(f)

    def _save_portfolio(self, data: dict) -> None:
        """Save portfolio JSON."""
        import json

        with open(self.portfolio_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_pending_redemptions(self) -> list[PendingRedemption]:
        """Read all pending_redemption entries from portfolio.json."""
        data = self._load_portfolio()
        results: list[PendingRedemption] = []

        # Scan fund_otc holdings for pending_redemption with status='pending'
        holdings = data.get("holdings", [])
        for holding in holdings:
            pending = holding.get("pending_redemption")
            if not pending:
                continue
            if pending.get("status") != "pending":
                continue
            results.append(
                PendingRedemption(
                    fund_code=holding.get("code", ""),
                    fund_name=holding.get("name", ""),
                    shares_submitted=float(pending.get("shares", 0)),
                    date_submitted=pending.get("date", ""),
                    confirm_date=pending.get("confirm_date", ""),
                    status="pending",
                    note=pending.get("note", ""),
                )
            )
        return results

    def is_confirmation_due(self, redemption: PendingRedemption) -> bool:
        """Check if today >= confirm_date and market is closed (after 15:30)."""
        if redemption.status != "pending":
            return False
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        # Only confirm if today is on or after confirm_date
        if today_str < redemption.confirm_date:
            return False
        # After 15:30 CST (market close)
        return not (now.hour < 15 or now.hour == 15 and now.minute < 30)

    async def confirm_redemption(self, redemption: PendingRedemption, nav: float) -> dict:
        """Confirm a redemption with the given NAV.

        1. Compute confirm_amount = shares * nav
        2. Add to cash.total
        3. Update holding: remove shares, clear pending_redemption
        4. Record in closed_positions if fully redeemed
        5. Return confirmation details
        """

        data = self._load_portfolio()
        holdings = data.get("holdings", [])
        confirm_amount = round(redemption.shares_submitted * nav, 2)

        # Find the holding
        holding = None
        for h in holdings:
            if h.get("code") == redemption.fund_code:
                holding = h
                break

        if not holding:
            return {
                "success": False,
                "error": f"未找到基金 {redemption.fund_code}",
            }

        # Compute P&L
        avg_cost = float(holding.get("cost_price", 0))
        realized_pnl = round(redemption.shares_submitted * (nav - avg_cost), 2)

        # Update cash
        cash = data.get("cash", {})
        cash["total"] = round(float(cash.get("total", 0)) + confirm_amount, 2)
        data["cash"] = cash

        # Update holding
        remaining_shares = float(holding.get("shares", 0)) - redemption.shares_submitted
        if remaining_shares <= 0:
            # Fully redeemed — move to closed_positions
            holding["shares"] = 0
            closed = data.get("closed_positions", [])
            closed.append(
                {
                    "code": holding.get("code"),
                    "name": holding.get("name"),
                    "shares": redemption.shares_submitted,
                    "sell_nav": nav,
                    "sell_amount": confirm_amount,
                    "avg_cost": avg_cost,
                    "realized_pnl": realized_pnl,
                    "closed_date": datetime.now().strftime("%Y-%m-%d"),
                }
            )
            data["closed_positions"] = closed
        else:
            holding["shares"] = round(remaining_shares, 4)

        # Clear pending redemption
        holding.pop("pending_redemption", None)

        self._save_portfolio(data)

        return {
            "success": True,
            "fund": redemption.fund_name,
            "shares": redemption.shares_submitted,
            "nav": nav,
            "amount": confirm_amount,
            "realized_pnl": realized_pnl,
            "remaining_shares": max(remaining_shares, 0),
        }

    def format_confirmation(self, redemption: PendingRedemption) -> str:
        """Format redemption confirmation as readable text."""
        lines = [
            f"📋 赎回确认 — {redemption.fund_name}",
            f"份额: {redemption.shares_submitted:.2f} 份",
            f"提交日期: {redemption.date_submitted}",
            f"确认日期: {redemption.confirm_date}",
        ]
        if redemption.confirm_nav is not None:
            lines.append(f"确认净值: {redemption.confirm_nav:.4f}")
        if redemption.confirm_amount is not None:
            lines.append(f"到账金额: ¥{redemption.confirm_amount:,.2f}")
        if redemption.realized_pnl is not None:
            pnl_emoji = "📈" if redemption.realized_pnl >= 0 else "📉"
            lines.append(f"{pnl_emoji} 实现盈亏: ¥{redemption.realized_pnl:,.2f}")
        if redemption.note:
            lines.append(f"备注: {redemption.note}")
        return "\n".join(lines)


async def check_pending_redemptions(
    portfolio_path: str | Path,
) -> list[dict]:
    """Check all pending redemptions and confirm any that are due.

    Returns:
        list of confirmation results (empty if nothing to confirm)
    """
    tracker = RedemptionTracker(portfolio_path)
    pending = tracker.get_pending_redemptions()
    results: list[dict] = []
    for r in pending:
        if tracker.is_confirmation_due(r):
            # Mark as needs_manual_confirmation until NAV is provided
            results.append(
                {
                    "fund": r.fund_name,
                    "fund_code": r.fund_code,
                    "shares": r.shares_submitted,
                    "status": "needs_manual_confirmation",
                    "message": f"{r.fund_name} 赎回{r.shares_submitted:.2f}份待确认净值",
                }
            )
    return results
