"""Cancel an explicit allowlist of sandbox resting orders.

Safety rules:
- every target id must be passed explicitly on the command line
- ids owned by this system (prefix ``qts-``) are refused
- the script cancels only those ids and prints every action
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from trader.adapters.broker.binance_spot_demo_broker import (  # noqa: E402
    BinanceSpotDemoBroker,
    BinanceSpotDemoBrokerConfig,
)

SYSTEM_PREFIX = "qts-"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Cancel listed sandbox orders")
    parser.add_argument("--cl-ord-id", action="append", required=True)
    args = parser.parse_args()

    targets = [value.strip() for value in args.cl_ord_id if value.strip()]
    if not targets:
        raise SystemExit("no target ids given")
    for target in targets:
        if target.startswith(SYSTEM_PREFIX):
            raise SystemExit(f"refusing to cancel system-owned order: {target}")

    broker = BinanceSpotDemoBroker(
        BinanceSpotDemoBrokerConfig.for_env(
            os.environ["BINANCE_API_KEY"],
            os.environ["BINANCE_SECRET_KEY"],
            env=os.environ.get("BINANCE_ENV", "demo"),
            proxy_url=os.environ.get("BINANCE_PROXY_URL") or None,
        )
    )
    await broker.connect()
    try:
        open_orders = {order.cl_ord_id: order for order in await broker.get_open_orders()}
        for target in targets:
            order = open_orders.get(target)
            if order is None:
                print(f"skip {target}: not an open order")
                continue
            await broker.cancel_order(cl_ord_id=order.cl_ord_id, symbol=order.symbol)
            print(f"cancelled {target} ({order.symbol})")
        remaining = [order.cl_ord_id for order in await broker.get_open_orders()]
        print("remaining open orders:", remaining)
    finally:
        await broker.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
