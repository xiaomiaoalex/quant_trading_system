"""
API Routes - All route modules
================================
Aggregates all API route modules.
"""

from trader.api.routes import (
    allocations,
    audit,
    backtests,
    brokers,
    chat,
    data_catalog,
    deployments,
    events,
    health,
    killswitch,
    monitor,
    orders,
    portfolio,
    portfolio_autopilot,
    portfolio_research,
    reconciler,
    risk,
    sse,
    strategies,
    strategy_candidates,
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
