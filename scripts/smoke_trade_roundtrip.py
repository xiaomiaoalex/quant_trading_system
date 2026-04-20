"""
smoke_trade_roundtrip.py
========================

Binance Spot Demo/Testnet 最小真实下单回合测试：
1) 连接
2) 市价买入
3) 市价卖出（默认卖出买入成交数量）

环境变量：
- BINANCE_API_KEY
- BINANCE_SECRET_KEY
- BINANCE_ENV                 可选：demo/testnet，默认 demo
- SMOKE_SYMBOL                可选：默认 BTCUSDT
- SMOKE_QTY                   可选：默认 0.0001
- BINANCE_PROXY_URL           可选：例如 http://127.0.0.1:4780

运行：
  .venv\\Scripts\\python.exe scripts\\smoke_trade_roundtrip.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from trader.adapters.broker.binance_spot_demo_broker import (  # noqa: E402
    BinanceSpotDemoBroker,
    BinanceSpotDemoBrokerConfig,
)
from trader.core.application.ports import BrokerBusinessError, BrokerOrder  # noqa: E402
from trader.core.domain.models.order import OrderSide, OrderType  # noqa: E402


def _load_env() -> None:
    # 固定读取仓库根目录 .env，避免 cwd 不同导致读取失败
    repo_root = Path(__file__).resolve().parent.parent
    env_file = repo_root / ".env"
    load_dotenv(env_file)


def _build_config() -> BinanceSpotDemoBrokerConfig:
    api_key = os.getenv("BINANCE_API_KEY")
    secret_key = os.getenv("BINANCE_SECRET_KEY")
    proxy_url = os.getenv("BINANCE_PROXY_URL")
    env_name = os.getenv("BINANCE_ENV", "demo").strip().lower()

    if not api_key or not secret_key:
        raise RuntimeError("Missing BINANCE_API_KEY / BINANCE_SECRET_KEY")

    if env_name == "testnet":
        return BinanceSpotDemoBrokerConfig.for_testnet(
            api_key=api_key,
            secret_key=secret_key,
            proxy_url=proxy_url,
            verify_ssl=True,
        )
    if env_name == "demo":
        return BinanceSpotDemoBrokerConfig.for_demo(
            api_key=api_key,
            secret_key=secret_key,
            proxy_url=proxy_url,
            verify_ssl=True,
        )
    raise RuntimeError(f"Unsupported BINANCE_ENV={env_name}, expected demo/testnet")


async def _wait_for_fill(
    broker: BinanceSpotDemoBroker,
    *,
    symbol: str,
    client_order_id: str,
    timeout_sec: float = 8.0,
) -> Decimal:
    start = time.time()
    last_qty = Decimal("0")
    while time.time() - start < timeout_sec:
        order = await broker.get_order(client_order_id=client_order_id, symbol=symbol)
        if order is None:
            await asyncio.sleep(0.25)
            continue
        last_qty = order.filled_quantity
        if last_qty > 0:
            return last_qty
        await asyncio.sleep(0.25)
    return last_qty


def _round_qty_by_step(value: Decimal, step_size: Decimal) -> Decimal:
    """
    Quantize quantity to the nearest valid LOT_SIZE step.

    Uses BinanceSpotDemoBroker.quantize_by_step_size to floor the quantity
    to the nearest valid multiple of stepSize.
    """
    if step_size <= 0:
        # Fallback to 8-decimal quantization if no step_size available
        return value.quantize(Decimal("0.00000001"))
    return BinanceSpotDemoBroker.quantize_by_step_size(value, step_size)


async def _place_market_sell_with_retry(
    broker: BinanceSpotDemoBroker,
    *,
    symbol: str,
    base_qty: Decimal,
    step_size: Decimal,
) -> tuple[Decimal, BrokerOrder]:
    """
    市价卖出带余额不足重试。

    BUY 成交后可卖数量常因手续费略小于 filled_qty，这里按梯度缩减重试。
    使用 step_size 动态量化，确保符合 Binance LOT_SIZE 要求。
    """
    factors = [Decimal("0.998"), Decimal("0.995"), Decimal("0.990"), Decimal("0.980")]
    last_error: Exception | None = None
    for factor in factors:
        sell_qty = _round_qty_by_step(base_qty * factor, step_size)
        if sell_qty <= 0:
            continue
        sell_cid = f"smoke_sell_{int(time.time())}"
        try:
            sell = await broker.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=sell_qty,
                client_order_id=sell_cid,
            )
            return sell_qty, sell
        except BrokerBusinessError as exc:
            last_error = exc
            if "insufficient balance" not in str(exc).lower():
                raise
            await asyncio.sleep(0.2)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to place market sell order: no valid quantity candidate")


async def main() -> int:
    _load_env()
    cfg = _build_config()

    symbol = os.getenv("SMOKE_SYMBOL", "BTCUSDT").strip().upper()
    qty = Decimal(os.getenv("SMOKE_QTY", "0.0001").strip())
    if qty <= 0:
        raise RuntimeError("SMOKE_QTY must be > 0")

    broker = BinanceSpotDemoBroker(cfg)
    await broker.connect()
    step_size = await broker.get_symbol_step_size(symbol)
    print(f"connected: broker={cfg.broker_name}, symbol={symbol}, qty={qty}, step_size={step_size}")

    try:
        buy_cid = f"smoke_buy_{int(time.time())}"
        buy = await broker.place_order(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=qty,
            client_order_id=buy_cid,
        )
        buy_filled = buy.filled_quantity
        if buy_filled <= 0:
            buy_filled = await _wait_for_fill(
                broker,
                symbol=symbol,
                client_order_id=buy_cid,
            )

        print(
            f"BUY  ok: status={buy.status.value if hasattr(buy.status, 'value') else buy.status}, "
            f"filled={buy_filled}, avg={buy.average_price}, order_id={buy.broker_order_id}"
        )

        sell_base_qty = buy_filled if buy_filled > 0 else qty
        sell_qty, sell = await _place_market_sell_with_retry(
            broker,
            symbol=symbol,
            base_qty=sell_base_qty,
            step_size=step_size,
        )
        sell_filled = sell.filled_quantity
        if sell_filled <= 0:
            sell_filled = await _wait_for_fill(
                broker,
                symbol=symbol,
                client_order_id=str(sell.client_order_id),
            )
        print(
            f"SELL ok: status={sell.status.value if hasattr(sell.status, 'value') else sell.status}, "
            f"request_qty={sell_qty}, filled={sell_filled}, avg={sell.average_price}, order_id={sell.broker_order_id}"
        )
        print("round-trip done")
        return 0
    finally:
        await broker.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
