"""Wealth engine — portfolio tracking, national team signal, and market monitoring."""
from __future__ import annotations

from secretary.wealth.decision import (
    DecisionAnalysis,
    PricePosition,
    Scenario,
    assess_position,
    build_scenarios,
    compute_decision,
    format_decision_report,
)
from secretary.wealth.eastmoney_sync import (
    AccountAssets,
    AccountHolding,
    SyncResult,
    TradeRecord,
    fetch_holdings,
    fetch_trade_history,
    format_sync_report,
    full_sync,
    is_cookie_fresh,
    load_cookies,
    login_with_playwright,
    refresh_session_with_playwright,
    save_cookies,
    sync_to_portfolio,
)
from secretary.wealth.jobs import WealthJobs
from secretary.wealth.monitor import (
    MonitorResult,
    check_all_holdings,
    check_runze,
    check_stock_announcements,
    format_announcement_alert,
    parse_cninfo_response,
    parse_eastmoney_response,
    scan_keywords,
)
from secretary.wealth.national_team import (
    NationalTeamResult,
    compute_score_from_flows,
    get_national_team_signal,
)
from secretary.wealth.portfolio import (
    Cash,
    ClosedPosition,
    Holding,
    PendingAction,
    PortfolioData,
    PortfolioSnapshot,
    Transaction,
    compute_portfolio,
    get_all_stock_codes,
    get_portfolio_snapshot,
    load_portfolio_data,
    parse_tencent_prices,
)
from secretary.wealth.qdii import (
    QDII_MAPPING,
    QDIISnapshot,
    analyze_all_qdii,
    analyze_qdii,
    is_qdii,
)
from secretary.wealth.redemption import (
    PendingRedemption,
    RedemptionTracker,
    check_pending_redemptions,
)
from secretary.wealth.risk import (
    DEFAULT_RULES,
    RiskCheckResult,
    check_discipline,
    check_position_concentration,
    check_stop_loss,
    format_risk_report,
    run_risk_check,
)

__all__ = [
    # portfolio
    "get_portfolio_snapshot",
    "PortfolioSnapshot",
    "PortfolioData",
    "Holding",
    "Cash",
    "ClosedPosition",
    "PendingAction",
    "Transaction",
    "compute_portfolio",
    "parse_tencent_prices",
    "load_portfolio_data",
    "get_all_stock_codes",
    # monitor
    "check_runze",
    "check_stock_announcements",
    "check_all_holdings",
    "MonitorResult",
    "scan_keywords",
    "parse_cninfo_response",
    "parse_eastmoney_response",
    "format_announcement_alert",
    # national team
    "get_national_team_signal",
    "NationalTeamResult",
    "compute_score_from_flows",
    # jobs
    "WealthJobs",
    # redemption
    "PendingRedemption",
    "RedemptionTracker",
    "check_pending_redemptions",
    # qdii
    "QDII_MAPPING",
    "QDIISnapshot",
    "is_qdii",
    "analyze_qdii",
    "analyze_all_qdii",
    # decision
    "PricePosition",
    "Scenario",
    "DecisionAnalysis",
    "assess_position",
    "build_scenarios",
    "compute_decision",
    "format_decision_report",
    # risk
    "RiskCheckResult",
    "DEFAULT_RULES",
    "check_stop_loss",
    "check_position_concentration",
    "check_discipline",
    "run_risk_check",
    "format_risk_report",
    # eastmoney sync
    "AccountAssets",
    "AccountHolding",
    "TradeRecord",
    "SyncResult",
    "login_with_playwright",
    "refresh_session_with_playwright",
    "fetch_holdings",
    "fetch_trade_history",
    "full_sync",
    "sync_to_portfolio",
    "format_sync_report",
    "save_cookies",
    "load_cookies",
    "is_cookie_fresh",
]
