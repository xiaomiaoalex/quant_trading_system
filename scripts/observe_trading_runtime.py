"""Continuous observation of the trading runtime for the test-environment window.

Writes one JSON object per line (JSONL). Each line records the runtime health,
the operator-visible metrics and the account risk summary, so a reviewer can
reconstruct the window. Restart or reconnect events are appended with --marker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    return response.json().get("csrf_token", "")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Observe the trading runtime")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--username", default=os.getenv("QTS_USERNAME", "owner"))
    parser.add_argument("--password", default=os.getenv("QTS_E2E_PASSWORD", ""))
    parser.add_argument("--account-id", default=os.getenv("QTS_ACCOUNT_ID", "binance_spot"))
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--duration", type=float, default=24 * 3600.0)
    parser.add_argument("--out", default="artifacts/observation.jsonl")
    parser.add_argument("--marker", default="")
    args = parser.parse_args()

    password = args.password
    if not password:
        password_file = Path(tempfile.gettempdir()) / "qts_operator_password.txt"
        if password_file.exists():
            password = password_file.read_text(encoding="utf-8").strip()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.time() + args.duration
    async with httpx.AsyncClient(base_url=args.base_url, timeout=20.0) as client:
        csrf = ""
        while time.time() < deadline:
            line = {"ts_ms": int(time.time() * 1000)}
            try:
                if not csrf:
                    csrf = await login(client, args.username, password)
                health = await client.get("/health/trading")
                line["health"] = {
                    "status_code": health.status_code,
                    "body": health.json(),
                }
                metrics = await client.get(
                    "/v1/trading/metrics", params={"account_id": args.account_id}
                )
                if metrics.status_code == 200:
                    line["metrics"] = metrics.json()
                risk = await client.get(f"/v1/trading/accounts/{args.account_id}/risk")
                if risk.status_code == 200:
                    body = risk.json()
                    line["risk"] = {
                        "status": body["status"],
                        "reasons": body["reasons"],
                        "generation": body["generation"],
                        "account_version": body["account_version"],
                        "positions": body["positions"],
                    }
            except Exception as exc:
                line["error"] = f"{type(exc).__name__}: {exc}"
                if "401" in str(exc) or "403" in str(exc):
                    # The session really is gone; reload the local password and
                    # log in again on the next sample.
                    csrf = ""
                    password_file = Path(tempfile.gettempdir()) / "qts_operator_password.txt"
                    if password_file.exists():
                        password = password_file.read_text(encoding="utf-8").strip()
            with out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, default=str) + "\n")
            print(json.dumps(line, default=str)[:300])
            await asyncio.sleep(args.interval)

    if args.marker:
        with out.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"ts_ms": int(time.time() * 1000), "marker": args.marker}) + "\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
