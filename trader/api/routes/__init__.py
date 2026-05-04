"""
API Routes - All route modules
================================
Aggregates all API route modules.
"""
from trader.api.routes import (
    health,
    strategies,
    deployments,
    backtests,
    risk,
    orders,
    portfolio,
    events,
    killswitch,
    brokers,
    reconciler,
    monitor,
    chat,
    portfolio_research,
    strategy_candidates,
    allocations,
    portfolio_autopilot,
    data_catalog,
    audit,
    sse,
)

__all__ = [
    "health",
    "strategies",
    "deployments",
    "backtests",
    "risk",
    "orders",
    "portfolio",
    "events",
    "killswitch",
    "brokers",
    "reconciler",
    "monitor",
    "chat",
    "portfolio_research",
    "strategy_candidates",
    "allocations",
    "portfolio_autopilot",
    "data_catalog",
    "audit",
    "sse",
]
