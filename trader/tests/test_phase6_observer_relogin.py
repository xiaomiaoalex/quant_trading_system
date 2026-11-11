"""Engineer test for the observation client's session re-login behaviour.

The control session has a bounded lifetime (12h). A 24h window therefore needs
the observer to log in again when the session expires; an expired session
answers with a 401 status instead of raising, so the token has to be dropped
on the response path.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def _observer_module():
    spec = importlib.util.spec_from_file_location(
        "engineer_phase6_observer", ROOT / "scripts" / "observe_trading_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Args:
    username = "owner"
    account_id = "binance_spot"
    retries = 2


def test_observer_relogins_after_an_expired_session() -> None:
    module = _observer_module()
    calls: list[str] = []
    logins = {"count": 0}
    expired = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/auth/login":
            logins["count"] += 1
            expired["value"] = False
            return httpx.Response(200, json={"csrf_token": f"csrf-{logins['count']}"})
        if request.url.path == "/health/trading":
            return httpx.Response(200, json={"ready": True, "reasons": []})
        if expired["value"]:
            return httpx.Response(401, json={"detail": "expired"})
        if request.url.path == "/v1/trading/metrics":
            return httpx.Response(
                200,
                json={
                    "status": "READY",
                    "evidence": {"executions": {"count": 1}},
                    "coverage": {"contiguous": True},
                    "unresolved_entries": [],
                },
            )
        return httpx.Response(200, json={"status": "READY", "reasons": []})

    transport = httpx.MockTransport(handler)
    args = _Args()

    async def run() -> tuple[dict, dict]:
        async with httpx.AsyncClient(
            base_url="http://control.test", transport=transport, timeout=5.0
        ) as client:
            state = {"csrf": ""}
            first = await module._sample(client, args, "password", state, 1)
            expired["value"] = True  # the 12h control session lifetime elapsed
            second = await module._sample(client, args, "password", state, 2)
            third = await module._sample(client, args, "password", state, 3)
            return first, second, third

    first, second, third = asyncio.run(run())

    assert first["metrics_status_code"] == 200
    assert second["metrics_status_code"] == 200
    assert second["metrics"]["evidence"]["executions"]["count"] == 1
    assert second["metrics"]["coverage"]["contiguous"] is True
    assert [entry["ok"] for entry in second["attempts"]] == [False, True]
    assert third["metrics_status_code"] == 200
    assert third["metrics"]["evidence"]["executions"]["count"] == 1
    assert logins["count"] == 2, calls
