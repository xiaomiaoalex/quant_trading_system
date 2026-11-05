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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    return response.json().get("csrf_token", "")


def runtime_identity(health_body: object, observed_at_ms: int) -> dict:
    """Extract the runtime release identity a sample was actually served by.

    The identity comes from the runtime that answered the health request, not
    from the operator: it is what ties an observation window to one deployed
    release.  Missing fields stay empty on purpose - the strict observation
    gate rejects them instead of treating them as "unknown but fine".
    """
    body: dict[str, Any] = health_body if isinstance(health_body, dict) else {}
    raw_release = body.get("release")
    release: dict[str, Any] = raw_release if isinstance(raw_release, dict) else {}
    return {
        "release_id": str(release.get("release_id") or ""),
        "commit": str(release.get("commit") or "").lower(),
        "image_digest": str(release.get("image_digest") or ""),
        "environment": str(release.get("environment") or ""),
        "instance_owner": str(body.get("instance_owner") or ""),
        "lease_token": str(body.get("lease_token") or ""),
        "observed_at_ms": int(observed_at_ms),
    }


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

    async with httpx.AsyncClient(base_url=args.base_url, timeout=20.0) as client:
        csrf = ""
        # The window starts at the first *recorded* sample, after the client
        # has been initialized.  Anchoring before the client setup would lose
        # the setup time and make a 24h request record slightly less than 24h.
        deadline: float | None = None
        while True:
            observed_at = time.time()
            line: dict[str, Any] = {"ts_ms": int(observed_at * 1000)}
            try:
                if not csrf:
                    csrf = await login(client, args.username, password)
                health = await client.get("/health/trading")
                health_body = health.json()
                line["health"] = {
                    "status_code": health.status_code,
                    "body": health_body,
                }
                # Every sample carries the identity of the runtime that
                # answered it, so the strict gate can prove the window belongs
                # to exactly one release instead of trusting a single flag.
                line["runtime"] = runtime_identity(health_body, line["ts_ms"])
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
            if deadline is None:
                # Same clock and same base as the recorded ts_ms above.
                deadline = observed_at + args.duration
            # The recorded window must cover the whole requested duration:
            # the first sample is taken at the start and a final sample is
            # taken at (or after) the deadline.  Stopping the loop before the
            # deadline would record "duration minus one interval" and would
            # make a 24h request fail a 24h floor.
            if observed_at >= deadline:
                break
            await asyncio.sleep(min(args.interval, max(0.0, deadline - observed_at)))

    if args.marker:
        with out.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"ts_ms": int(time.time() * 1000), "marker": args.marker}) + "\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
