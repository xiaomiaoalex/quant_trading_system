"""End-to-end verification of the unified trading core against a Binance sandbox.

Flow: login -> health -> place LIMIT -> observe -> cancel -> confirm cancel ->
place MARKET -> wait for the real fill -> read the persisted ledger.

Only use a sandbox account. The script never touches production credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    return response.json()["csrf_token"]


async def wait_for(
    client: httpx.AsyncClient,
    path: str,
    predicate,
    timeout: float = 120.0,
    interval: float = 3.0,
    params: dict | None = None,
):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = await client.get(path, params=params)
        last = response.json()
        if predicate(last):
            return last
        await asyncio.sleep(interval)
    raise TimeoutError(f"condition not reached for {path}: {last}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Binance sandbox E2E verification")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--username", default=os.getenv("QTS_USERNAME", "owner"))
    parser.add_argument("--password", default=os.getenv("QTS_E2E_PASSWORD", ""))
    parser.add_argument("--account-id", default=os.getenv("QTS_ACCOUNT_ID", "binance_spot"))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit-qty", default="0.001")
    parser.add_argument("--market-qty", default="0.0002")
    parser.add_argument("--out", default="")
    parser.add_argument("--reconnect", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / ".env.postgres", override=False)

    password = args.password
    if not password:
        password_file = Path(tempfile.gettempdir()) / "qts_operator_password.txt"
        if password_file.exists():
            password = password_file.read_text(encoding="utf-8").strip()

    evidence: dict = {"steps": []}

    def record(name: str, **payload):
        evidence["steps"].append({"step": name, **payload})
        print(f"[e2e] {name}: {json.dumps(payload, default=str)[:400]}")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        csrf = await login(client, args.username, password)
        record("login", actor="authenticated", csrf=bool(csrf))
        headers = {"x-csrf-token": csrf}

        health = await client.get("/health/trading")
        record(
            "health",
            status_code=health.status_code,
            body=health.json(),
        )
        if health.status_code != 200:
            record("aborted", reason="trading runtime not ready")
            await _write(args.out, evidence)
            return 2

        metrics_before = (
            await client.get("/v1/trading/metrics", params={"account_id": args.account_id})
        ).json()
        record("metrics_before", metrics=metrics_before)

        from trader.adapters.broker.binance_spot_demo_broker import (
            BinanceSpotDemoBroker,
            BinanceSpotDemoBrokerConfig,
        )

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
            prices = await broker.get_ticker_prices([args.symbol])
            last_price = Decimal(str(prices[args.symbol]))
            # Stay inside the venue percent-price filter band but below the market
            # so the order rests instead of filling.
            limit_price = (last_price * Decimal("0.99")).quantize(Decimal("0.01"))
            record("market_price", symbol=args.symbol, last=str(last_price), limit=str(limit_price))

            place = await client.post(
                "/v1/trading/orders",
                headers=headers,
                json={
                    "command_id": f"e2e-limit-{uuid.uuid4().hex[:8]}",
                    "account_id": args.account_id,
                    "symbol": args.symbol,
                    "base_asset": args.symbol[:-4],
                    "quote_asset": args.symbol[-4:],
                    "side": "BUY",
                    "qty": args.limit_qty,
                    "price": str(limit_price),
                    "order_type": "LIMIT",
                    "deployment_id": "e2e",
                },
            )
            place.raise_for_status()
            limit_receipt = place.json()
            record("place_limit", receipt=limit_receipt)
            # The Control API only queues the command; the execution process
            # confirms it against the venue, so the receipt starts as QUEUED.
            limit_command = await wait_for(
                client,
                f"/v1/commands/{limit_receipt['command_id']}?account_id={args.account_id}",
                lambda payload: payload["status"] in {"CONFIRMED", "FAILED", "UNKNOWN"},
                timeout=120.0,
            )
            record("limit_command", command=limit_command)
            if limit_command["status"] != "CONFIRMED":
                record("aborted", reason="venue rejected the limit order", command=limit_command)
                await _write(args.out, evidence)
                return 3
            cl_ord_id = limit_receipt["cl_ord_id"]

            order = await wait_for(
                client,
                f"/v1/trading/orders/{cl_ord_id}?account_id={args.account_id}",
                lambda payload: payload["status"] in {"OPEN", "PARTIALLY_FILLED"},
            )
            record("limit_open", order=order)

            cancel = await client.post(
                f"/v1/trading/orders/{cl_ord_id}/cancel",
                headers=headers,
                json={
                    "command_id": f"e2e-cancel-{uuid.uuid4().hex[:8]}",
                    "account_id": args.account_id,
                },
            )
            cancel.raise_for_status()
            record("cancel_requested", receipt=cancel.json())
            cancel_command_id = cancel.json()["command_id"]

            command = await wait_for(
                client,
                f"/v1/commands/{cancel_command_id}?account_id={args.account_id}",
                lambda payload: payload["status"] in {"CONFIRMED", "FAILED", "UNKNOWN"},
            )
            record("cancel_command", command=command)

            cancelled = await wait_for(
                client,
                f"/v1/trading/orders/{cl_ord_id}?account_id={args.account_id}",
                lambda payload: payload["status"] == "CANCELLED",
                timeout=180.0,
            )
            record("limit_cancelled", order=cancelled)

            market = await client.post(
                "/v1/trading/orders",
                headers=headers,
                json={
                    "command_id": f"e2e-market-{uuid.uuid4().hex[:8]}",
                    "account_id": args.account_id,
                    "symbol": args.symbol,
                    "base_asset": args.symbol[:-4],
                    "quote_asset": args.symbol[-4:],
                    "side": "BUY",
                    "qty": args.market_qty,
                    "price": str(last_price),
                    "order_type": "MARKET",
                    "deployment_id": "e2e",
                },
            )
            market.raise_for_status()
            market_receipt = market.json()
            record("place_market", receipt=market_receipt)
            market_cl_ord_id = market_receipt["cl_ord_id"]

            filled = await wait_for(
                client,
                f"/v1/trading/orders/{market_cl_ord_id}?account_id={args.account_id}",
                lambda payload: payload["status"] == "FILLED",
                timeout=180.0,
            )
            record("market_filled", order=filled)

            settled = await wait_for(
                client,
                "/v1/trading/metrics",
                lambda payload: payload["unbooked_fills"] == 0 and payload["active_holds"] == 0,
                timeout=180.0,
                interval=3.0,
                params={"account_id": args.account_id},
            )
            record("accounting_settled", metrics=settled)

            risk = (await client.get(f"/v1/trading/accounts/{args.account_id}/risk")).json()
            record("risk_view", risk=risk)
            metrics_after = (
                await client.get("/v1/trading/metrics", params={"account_id": args.account_id})
            ).json()
            record("metrics_after", metrics=metrics_after)

            ledger = await _read_ledger(args.account_id)
            record("ledger", entries=ledger[-6:])
            if any("error" in entry for entry in ledger):
                raise RuntimeError("ledger read failed")

            if args.reconnect:
                reconnect_id = f"e2e-reconnect-{int(time.time() * 1000)}"
                reconnect = await client.post(
                    "/v1/trading/reconnect",
                    headers=headers,
                    json={"command_id": reconnect_id, "account_id": args.account_id},
                )
                reconnect.raise_for_status()
                await wait_for(
                    client,
                    f"/v1/commands/{reconnect_id}?account_id={args.account_id}",
                    lambda payload: payload.get("status") == "CONFIRMED",
                    timeout=120.0,
                )
                record("reconnected", body=reconnect.json())
                await wait_for(
                    client,
                    "/health/trading",
                    lambda payload: payload.get("ready") is True,
                    timeout=120.0,
                )
                record("ready_after_reconnect", ok=True)
        finally:
            await broker.disconnect()

    await _write(args.out, evidence)
    print("[e2e] ok")
    return 0


async def _read_ledger(account_id: str) -> list[dict]:
    import asyncpg

    from trader.trading.pg import connection_string

    connection = await asyncpg.connect(connection_string())
    try:
        rows = await connection.fetch(
            "SELECT entry_id, data FROM trading_ledger WHERE account_id=$1 ORDER BY entry_id",
            account_id,
        )
        result = []
        for row in rows:
            data = row["data"]
            result.append(json.loads(data) if isinstance(data, str) else dict(data))
        return result
    except Exception as exc:
        return [{"error": str(exc)}]
    finally:
        await connection.close()


async def _write(path: str, payload: dict) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
