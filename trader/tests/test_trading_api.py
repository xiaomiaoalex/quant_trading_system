"""Control-plane API acceptance tests: auth, CSRF, commands, health and research."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest

from trader.api.auth import AuthSettings, MemorySessionStore, hash_password
from trader.api.trading_app import create_trading_app
from trader.tests.test_trading_service import Clock, FakeExchange, bootstrap, command, make_service
from trader.trading.jobs import BacktestApplication, MemoryResearchStore
from trader.trading.service import TradingService

PASSWORD = "correct horse battery staple"


class Runtime:
    def __init__(self, service, ready=True, reasons=()):
        self.service = service
        self.ready = ready
        self.reasons = list(reasons)

    async def check(self):
        return {
            "ready": self.ready,
            "reasons": [] if self.ready else self.reasons,
            "account_id": "acct",
            "db_available": True,
            "instance_owner": "runtime-1",
        }


async def build_client(*, ready=True, reasons=(), research=False):
    service = make_service(FakeExchange(), clock=Clock())
    await bootstrap(service)
    sessions = MemorySessionStore()
    settings = AuthSettings(
        username="owner",
        password_hash=hash_password(PASSWORD),
        secure_cookie=False,
        origin="http://testserver",
        require_auth=True,
    )
    store = MemoryResearchStore() if research else None
    application = BacktestApplication(store) if research else None
    app = create_trading_app(
        trading_service=service,
        research_store=store,
        research_application=application,
        sessions=sessions,
        auth_settings=settings,
        readiness_provider=Runtime(service, ready=ready, reasons=reasons),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    return client, service, store


async def login(client):
    response = await client.post("/v1/auth/login", json={"username": "owner", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def order_payload(**overrides):
    values = {
        "command_id": "cmd-api-1",
        "account_id": "acct",
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "side": "BUY",
        "qty": "1",
        "price": "100",
        "deployment_id": "dep-1",
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_unauthenticated_requests_are_rejected_and_login_sets_httponly_cookie():
    client, _, _ = await build_client()
    try:
        response = await client.post("/v1/trading/orders", json=order_payload())
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required"

        csrf = await login(client)
        assert csrf
        cookie = next(value for value in client.cookies.jar if value.name == "qts_session")
        assert cookie.has_nonstandard_attr("HttpOnly")
        me = await client.get("/v1/auth/me")
        assert me.status_code == 200 and me.json()["actor"] == "owner"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_writes_require_csrf_and_forged_actor_is_ignored():
    client, service, _ = await build_client()
    try:
        await login(client)
        missing = await client.post("/v1/trading/orders", json=order_payload())
        assert missing.status_code == 403
        assert missing.json()["detail"] == "CSRF validation failed"

        bad = await client.post(
            "/v1/trading/orders",
            json=order_payload(),
            headers={"x-csrf-token": "forged"},
        )
        assert bad.status_code == 403

        csrf = (await client.get("/v1/auth/me")).status_code  # still authenticated
        login_response = await client.post(
            "/v1/auth/login", json={"username": "owner", "password": PASSWORD}
        )
        csrf = login_response.json()["csrf_token"]
        placed = await client.post(
            "/v1/trading/orders",
            json=order_payload(actor="admin", requested_by="admin"),
            headers={"x-csrf-token": csrf},
        )
        assert placed.status_code == 202, placed.text
        assert placed.json()["status"] == "CONFIRMED"
        record = await service.command("acct", "cmd-api-1")
        assert record["actor"] == "owner"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_command_and_order_queries_return_persistent_state_after_cancel():
    client, service, _ = await build_client()
    try:
        csrf = await login(client)
        placed = await client.post(
            "/v1/trading/orders", json=order_payload(), headers={"x-csrf-token": csrf}
        )
        cl_ord_id = placed.json()["cl_ord_id"]

        cancel = await client.post(
            f"/v1/trading/orders/{cl_ord_id}/cancel",
            json={"command_id": "cancel-api-1", "account_id": "acct", "actor": "admin"},
            headers={"x-csrf-token": csrf},
        )
        assert cancel.status_code == 202
        assert cancel.json()["status"] == "CONFIRMED"
        # A confirmed cancel request is not a cancelled order.
        order = await client.get(f"/v1/trading/orders/{cl_ord_id}", params={"account_id": "acct"})
        assert order.status_code == 200
        assert order.json()["status"] == "OPEN"

        command_response = await client.get(
            "/v1/commands/cancel-api-1", params={"account_id": "acct"}
        )
        assert command_response.status_code == 200
        assert command_response.json()["status"] == "CONFIRMED"
        assert command_response.json()["actor"] == "owner"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_logout_revokes_the_session():
    client, _, _ = await build_client()
    try:
        csrf = await login(client)
        logout = await client.post("/v1/auth/logout", headers={"x-csrf-token": csrf})
        assert logout.status_code == 200
        after = await client.get("/v1/trading/accounts/acct/risk")
        assert after.status_code == 401
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_login_rate_limit_after_five_failures():
    client, _, _ = await build_client()
    try:
        for _ in range(5):
            response = await client.post(
                "/v1/auth/login", json={"username": "owner", "password": "wrong password"}
            )
            assert response.status_code == 401
        blocked = await client.post(
            "/v1/auth/login", json={"username": "owner", "password": PASSWORD}
        )
        assert blocked.status_code == 429
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_trading_readiness_is_separate_from_process_liveness():
    ready_client, _, _ = await build_client(ready=True)
    blocked_client, _, _ = await build_client(
        ready=False, reasons=["ACCOUNT_NOT_RECONCILED", "UNKNOWN_COMMAND"]
    )
    try:
        assert (await ready_client.get("/health/live")).status_code == 200
        assert (await ready_client.get("/health/ready")).status_code == 200
        ready = await ready_client.get("/health/trading")
        assert ready.status_code == 200 and ready.json()["ready"] is True

        blocked = await blocked_client.get("/health/trading")
        assert blocked.status_code == 503
        assert blocked.json()["ready"] is False
        assert "ACCOUNT_NOT_RECONCILED" in blocked.json()["reasons"]
    finally:
        await ready_client.aclose()
        await blocked_client.aclose()


@pytest.mark.asyncio
async def test_not_ready_service_rejects_new_risk_with_503():
    service = make_service(FakeExchange(), clock=Clock())
    # No reconciliation snapshot: the account cannot accept new risk.
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_trading_app(
                trading_service=service,
                sessions=MemorySessionStore(),
                auth_settings=AuthSettings(),
            )
        ),
        base_url="http://testserver",
    )
    try:
        response = await client.post("/v1/trading/orders", json=order_payload())
        assert response.status_code == 503
        assert "ACCOUNT_NOT_RECONCILED" in response.json()["detail"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_research_api_returns_a_durable_run_id_and_rejects_vectorbt_admission():
    client, _, store = await build_client(research=True)
    try:
        csrf = await login(client)
        bars = [
            {
                "open_time_ms": 0,
                "close_time_ms": 60_000,
                "open": "100",
                "high": "100",
                "low": "100",
                "close": "100",
                "volume": "1",
            }
        ]
        dataset_response = await client.post(
            "/v1/research/datasets",
            json={
                "identity": {
                    "venue": "binance",
                    "market_type": "SPOT",
                    "symbol": "BTCUSDT",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                    "interval": "1m",
                    "source": "fixture",
                    "version": "1",
                },
                "bars": bars,
            },
            headers={"x-csrf-token": csrf},
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset_id = dataset_response.json()["dataset_id"]

        manifest = {
            "dataset_ids": [dataset_id],
            "strategy": {"module_path": "trader.strategies.ema_cross_btc", "params": {}},
            "initial_cash": "1000",
            "fee_bps": "10",
            "slippage_bps": "5",
            "engine": "event_runtime",
            "purpose": "research_screening",
        }
        submitted = await client.post(
            "/v1/research/backtests",
            json={"manifest": manifest},
            headers={"x-csrf-token": csrf},
        )
        assert submitted.status_code == 202, submitted.text
        run_id = submitted.json()["run_id"]
        assert submitted.json()["status"] == "QUEUED"

        fetched = await client.get(f"/v1/research/backtests/{run_id}")
        assert fetched.status_code == 200
        assert fetched.json()["manifest_hash"] == submitted.json()["manifest_hash"]

        vectorbt = await client.post(
            "/v1/research/backtests",
            json={"manifest": {**manifest, "engine": "vectorbt", "purpose": "admission_evidence"}},
            headers={"x-csrf-token": csrf},
        )
        assert vectorbt.status_code == 422
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_orders_can_be_listed_for_operator_action_state():
    client, _, _ = await build_client()
    try:
        csrf = await login(client)
        placed = await client.post(
            "/v1/trading/orders", json=order_payload(), headers={"x-csrf-token": csrf}
        )
        assert placed.status_code == 202
        listed = await client.get("/v1/trading/orders", params={"account_id": "acct"})
        assert listed.status_code == 200
        assert [item["cl_ord_id"] for item in listed.json()] == [placed.json()["cl_ord_id"]]
        assert listed.json()[0]["status"] == "OPEN"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_control_actions_are_commands_with_audit_identity():
    client, service, _ = await build_client()
    try:
        csrf = await login(client)
        blocked = await client.post(
            "/v1/trading/control",
            json={
                "command_id": "ctl-1",
                "account_id": "acct",
                "action": "BLOCK_NEW_RISK",
                "actor": "admin",
            },
            headers={"x-csrf-token": csrf},
        )
        assert blocked.status_code == 202
        assert blocked.json()["kind"] == "CONTROL"
        assert blocked.json()["status"] == "CONFIRMED"
        record = await service.command("acct", "ctl-1")
        assert record["actor"] == "owner"

        rejected = await client.post(
            "/v1/trading/orders",
            json=order_payload(command_id="after-block"),
            headers={"x-csrf-token": csrf},
        )
        assert rejected.status_code == 503
        assert "NEW_RISK_BLOCKED" in rejected.json()["detail"]

        resumed = await client.post(
            "/v1/trading/control",
            json={"command_id": "ctl-2", "account_id": "acct", "action": "RESUME_NEW_RISK"},
            headers={"x-csrf-token": csrf},
        )
        assert resumed.status_code == 202
        allowed = await client.post(
            "/v1/trading/orders",
            json=order_payload(command_id="after-resume"),
            headers={"x-csrf-token": csrf},
        )
        assert allowed.status_code == 202
    finally:
        await client.aclose()


class _ReadyProvider:
    async def check(self):
        return {"ready": True, "reasons": [], "account_id": "acct"}


@pytest.mark.asyncio
async def test_readiness_does_not_inherit_another_apps_global_provider():
    from trader.api.routes import health

    health.configure_trading_readiness(_ReadyProvider())
    try:
        app = create_trading_app(trading_service=None)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.get("/health/trading")
            assert response.status_code == 503
            assert response.json()["ready"] is False
        finally:
            await client.aclose()
    finally:
        health.configure_trading_readiness(None)


@pytest.mark.asyncio
async def test_legacy_app_without_state_still_uses_the_global_provider():
    from fastapi import FastAPI

    from trader.api.routes import health

    app = FastAPI()
    app.include_router(health.router)
    health.configure_trading_readiness(_ReadyProvider())
    try:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        try:
            response = await client.get("/health/trading")
            assert response.status_code == 200
            assert response.json()["ready"] is True
        finally:
            await client.aclose()
    finally:
        health.configure_trading_readiness(None)
