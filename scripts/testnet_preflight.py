"""Read-only Binance demo preflight: connectivity, account state, coverage proof.

The script never places, cancels or modifies an order. It only reads the
sandbox account and runs the same coverage prover the trading worker uses, so
the recovery barrier can be verified against a real venue.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="Binance demo read-only preflight")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--account-id", default=os.getenv("QTS_ACCOUNT_ID", "binance_spot"))
    parser.add_argument("--lookback-ms", type=int, default=86_400_000)
    parser.add_argument("--settle-ms", type=int, default=250)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)

    from trader.adapters.broker.binance_spot_demo_broker import (
        BinanceSpotDemoBroker,
        BinanceSpotDemoBrokerConfig,
    )
    from trader.adapters.broker.spot_coverage import BinanceSpotCoverageProver
    from trader.adapters.broker.spot_execution_adapter import BinanceSpotSnapshotProvider

    evidence: dict = {"steps": []}

    def record(name: str, **payload):
        evidence["steps"].append({"step": name, **payload})
        print(f"[preflight] {name}: {json.dumps(payload, default=str)[:400]}")

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
        server_time = await broker.get_server_time()
        record("server_time", server_time_ms=server_time)
        # The worker opens the private stream before it snapshots the account,
        # so the proven window starts at the connection boundary.
        connected_from = server_time

        account = await broker._fetch_account()
        balances = {
            str(item.get("asset")): str(item.get("free"))
            for item in account.get("balances", [])
            if float(item.get("free", 0) or 0) or float(item.get("locked", 0) or 0)
        }
        record("account_balances", balances=balances)

        prices = await broker.get_ticker_prices([args.symbol])
        record("ticker", prices={k: str(v) for k, v in prices.items()})

        open_orders = await broker.get_open_orders()
        record(
            "open_orders",
            count=len(open_orders),
            client_order_ids=[order.cl_ord_id for order in open_orders],
        )

        clock = lambda: server_time  # noqa: E731
        provider = BinanceSpotSnapshotProvider(
            broker,
            account_id=args.account_id,
            symbols=[args.symbol],
            clock=broker.get_server_time,
        )
        historical = await provider.fetch_orders(args.symbol, server_time - args.lookback_ms)
        record(
            "historical_orders",
            complete=historical[1],
            count=len(historical[0]),
            client_order_ids=sorted({order.cl_ord_id for order in historical[0]})[:20],
        )
        first = await provider.build(args.account_id)
        last = await provider.build(args.account_id)
        record(
            "snapshots",
            first_complete=bool(first and first.complete),
            last_complete=bool(last and last.complete),
            first_executions=len(first.executions) if first else None,
            last_executions=len(last.executions) if last else None,
            open_orders=len(first.open_orders) if first else None,
        )

        prover = BinanceSpotCoverageProver(
            rest=provider,
            symbols=provider.symbols,
            exchange_clock=broker.get_server_time,
            lookback_ms=args.lookback_ms,
            settle_ms=args.settle_ms,
        )
        proof = await prover(
            generation=1,
            first=first,
            last=last,
            connected_from_ms=connected_from,
            buffered_through_ms=connected_from,
        )
        record(
            "coverage_proof",
            proven=proof is not None,
            proof=(
                None
                if proof is None
                else {
                    "source": proof.source,
                    "connected_from": proof.connected_from,
                    "covered_through": proof.covered_through,
                    "buffered_through": proof.buffered_through,
                    "contiguous": proof.contiguous,
                }
            ),
        )
        evidence["coverage_proven"] = proof is not None
    finally:
        await broker.disconnect()

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    return 0 if evidence.get("coverage_proven") else 4


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
