import logging
import os
import importlib
import sys
from collections.abc import Iterator

import pytest
from dotenv import load_dotenv


if os.path.exists(".env.postgres"):
    load_dotenv(".env.postgres")
else:
    load_dotenv()


_SANITIZED_ENV_KEYS = (
    "LIVE_TRADING_ENABLED",
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_PROXY_URL",
    "BINANCE_BACKUP_PROXY_URL",
    "BINANCE_PROXY",
    "BINANCE_PROXY_FAILOVER_THRESHOLD",
    "BINANCE_PROXY_FAILOVER_COOLDOWN_SECONDS",
)
_TRACKED_ENV_KEYS = (
    *_SANITIZED_ENV_KEYS,
    "BINANCE_ENV",
    "POSTGRES_CONNECTION_STRING",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)
_BASE_ENV = {
    key: None if key in _SANITIZED_ENV_KEYS else os.environ.get(key)
    for key in _TRACKED_ENV_KEYS
}
try:
    _REAL_ASYNCPG = importlib.import_module("asyncpg")
except Exception:
    _REAL_ASYNCPG = None


def _enable_pytest_log_capture() -> None:
    logging.getLogger("trader").propagate = True


def _restore_tracked_env() -> None:
    for key, value in _BASE_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _enable_pytest_log_capture()


def _restore_asyncpg_module() -> None:
    if _REAL_ASYNCPG is None:
        sys.modules.pop("asyncpg", None)
    else:
        sys.modules["asyncpg"] = _REAL_ASYNCPG


def _reset_global_test_state() -> None:
    from trader.adapters.binance.proxy_failover import reset_proxy_failover_controller
    from trader.adapters.persistence.execution_repository import reset_execution_repository
    from trader.adapters.persistence.killswitch_repository import reset_killswitch_repository
    from trader.adapters.persistence.position_repository import reset_position_repository
    from trader.adapters.persistence.risk_repository import reset_risk_event_repository
    from trader.adapters.persistence.runtime_state_repository import reset_runtime_state_repository
    from trader.api.routes import monitor
    from trader.api.routes.strategies import reset_strategy_route_state_for_tests
    from trader.core.domain.services.order_ownership_registry import (
        reset_order_ownership_registry,
    )
    from trader.core.domain.services.position_lot_registry import reset_lot_manager
    from trader.services.strategy_event_service import reset_strategy_event_service
    from trader.storage.in_memory import reset_storage

    reset_strategy_route_state_for_tests()
    monitor._monitor_service = None
    monitor._portfolio_service = None
    reset_proxy_failover_controller()
    reset_strategy_event_service()
    reset_lot_manager()
    reset_order_ownership_registry()
    reset_execution_repository()
    reset_killswitch_repository()
    reset_position_repository()
    reset_risk_event_repository()
    reset_runtime_state_repository()
    reset_storage()
    _restore_tracked_env()
    _restore_asyncpg_module()


@pytest.fixture(autouse=True)
def isolate_global_test_state() -> Iterator[None]:
    _reset_global_test_state()
    yield
    _reset_global_test_state()


_restore_tracked_env()
