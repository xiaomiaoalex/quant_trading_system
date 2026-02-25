"""
API Routes - All route modules
=============================
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
]
