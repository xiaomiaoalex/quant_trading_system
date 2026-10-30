"""Trading runtime process: Control Plane API plus one account execution owner."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn

from trader.adapters.broker.binance_spot_demo_broker import (
    BinanceSpotDemoBroker,
    BinanceSpotDemoBrokerConfig,
)
from trader.adapters.broker.spot_execution_adapter import (
    BinanceSpotExchangePort,
    BinanceSpotSnapshotProvider,
)
from trader.api.auth import AuthSettings, PostgresSessionStore
from trader.api.env_config import get_binance_env, get_binance_recv_window
from trader.api.routes.sse import get_sse_manager
from trader.api.trading_app import create_trading_app
from trader.trading.jobs import BacktestApplication, PostgresResearchStore
from trader.trading.models import ExchangeOrderResult, ExchangeOutcome
from trader.trading.pg import create_pool
from trader.trading.postgres import PostgresTradingStore
from trader.trading.runtime import TradingReadinessProvider, TradingRuntime

logger = logging.getLogger(__name__)

API_PORT = int(os.getenv("QTS_TRADING_API_PORT", "8090"))


class _UnavailableVenue:
    """Fail-closed venue used when credentials are absent."""

    async def place_order(self, command, cl_ord_id):
        return ExchangeOrderResult(
            ExchangeOutcome.UNAVAILABLE, reason="BINANCE_CREDENTIALS_MISSING"
        )

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        return ExchangeOrderResult(
            ExchangeOutcome.UNAVAILABLE, reason="BINANCE_CREDENTIALS_MISSING"
        )

    async def query_order(self, account_id, cl_ord_id, symbol):
        return ExchangeOrderResult(
            ExchangeOutcome.UNAVAILABLE, reason="BINANCE_CREDENTIALS_MISSING"
        )


def _clock_ms() -> int:
    return int(time.time() * 1000)


@asynccontextmanager
async def lifespan(app):
    pool = None
    broker = None
    runtime = None
    try:
        pool = await create_pool(min_size=1, max_size=10)
        store = PostgresTradingStore(pool)
        research_store = PostgresResearchStore(pool)
        sessions = PostgresSessionStore(pool)
        await sessions.initialize()
        app.state.sessions = sessions
        app.state.research_store = research_store
        app.state.research_application = BacktestApplication(research_store)

        api_key = os.getenv("BINANCE_API_KEY")
        secret_key = os.getenv("BINANCE_SECRET_KEY")
        account_id = os.getenv("QTS_ACCOUNT_ID", "binance_spot")
        symbols = [
            symbol.strip().upper()
            for symbol in os.getenv("QTS_SYMBOLS", "BTCUSDT").split(",")
            if symbol.strip()
        ]

        exchange = _UnavailableVenue()
        snapshot_provider = None
        if api_key and secret_key:
            config = BinanceSpotDemoBrokerConfig.for_env(
                api_key,
                secret_key,
                env=get_binance_env(),
                recv_window=get_binance_recv_window(),
                proxy_url=os.getenv("BINANCE_PROXY_URL") or None,
            )
            broker = BinanceSpotDemoBroker(config)
            await broker.connect()
            exchange = BinanceSpotExchangePort(broker)
            snapshot = BinanceSpotSnapshotProvider(
                broker,
                account_id=account_id,
                symbols=symbols,
                clock=_clock_ms,
                lookback_ms=int(os.getenv("QTS_RECONCILE_LOOKBACK_MS", "86400000")),
            )
            snapshot_provider = snapshot.build
        else:

            async def snapshot_provider(_account_id):
                raise RuntimeError("BINANCE_CREDENTIALS_MISSING")

        manager = get_sse_manager()

        async def notify(event):
            channel = (
                "orders"
                if event["event_type"].startswith(("order.", "execution."))
                else "reconciliation"
            )
            await manager.broadcast(channel, event["event_type"], event)

        runtime = TradingRuntime(
            store=store,
            exchange=exchange,
            account_id=account_id,
            snapshot_provider=snapshot_provider,
            clock=_clock_ms,
            notifier=notify,
            owner=os.getenv("QTS_RUNTIME_OWNER", f"{os.getenv('HOSTNAME', 'local')}:{os.getpid()}"),
            lease_seconds=int(os.getenv("QTS_RUNTIME_LEASE_SECONDS", "30")),
        )
        started = await runtime.start()
        app.state.trading_service = runtime.service
        app.state.readiness_provider = TradingReadinessProvider(runtime)
        app.state.trading_runtime = runtime
        logger.info(
            "Trading runtime started=%s stages=%s reasons=%s",
            started,
            runtime.stages,
            runtime.last_reasons,
        )
    except Exception:
        logger.exception("Trading runtime failed to start; serving fail-closed health")
        app.state.readiness_provider = None
    try:
        yield
    finally:
        if runtime is not None:
            await runtime.stop()
        if broker is not None:
            try:
                await broker.disconnect()
            except Exception:
                pass
        if pool is not None:
            await pool.close()


app = create_trading_app(
    trading_service=None,
    sessions=None,
    auth_settings=AuthSettings.from_env(),
    lifespan=lifespan,
    include_sse=True,
)


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("QTS_API_HOST", "0.0.0.0"), port=API_PORT)
