"""Control-plane endpoints for authentication and the unified spot engine."""

from __future__ import annotations

import secrets
import time
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from trader.api.auth import AuthSettings, csrf_for, verify_password
from trader.trading.models import (
    AccountRiskView,
    Conflict,
    NotReady,
    PlaceOrderCommand,
    TradingError,
    json_value,
)
from trader.trading.risk import RiskRejected

router = APIRouter(tags=["Trading"])


class LoginRequest(BaseModel):
    username: str
    password: str


class PlaceOrderRequest(BaseModel):
    command_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    base_asset: str = Field(min_length=1)
    quote_asset: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    qty: Decimal
    price: Decimal
    deployment_id: str = "manual"
    order_type: Literal["MARKET", "LIMIT"] = "LIMIT"
    time_in_force: str = "GTC"
    decision_id: str = ""
    actor: str | None = None  # legacy input; ignored, identity comes from the session


class CancelRequest(BaseModel):
    command_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1)
    actor: str | None = None


class ControlRequest(BaseModel):
    command_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1)
    action: Literal["BLOCK_NEW_RISK", "RESUME_NEW_RISK", "CANCEL_ALL", "RECONNECT", "RESOLVE"]
    # RESOLVE settles one pending difference. The control process only records
    # the command; the execution process checks the evidence and applies it.
    code: str = ""
    evidence: dict | None = None
    actor: str | None = None


def _service(request: Request):
    service = getattr(request.app.state, "trading_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Trading runtime unavailable")
    return service


def _receipt(receipt) -> dict:
    return json_value(receipt)


def _executor(request: Request):
    # Runtime owns the account lease. Direct service injection is for isolated
    # tests and control applications that do not assemble an execution runtime.
    runtime = getattr(request.app.state, "trading_runtime", None)
    return runtime if runtime is not None else _service(request)


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, NotReady):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RiskRejected):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, TradingError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=503, detail="Trading storage unavailable")


@router.post("/v1/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    settings: AuthSettings = request.app.state.auth_settings
    if not settings.enabled:
        return {"actor": "dev", "csrf_token": ""}
    sessions = getattr(request.app.state, "sessions", None)
    if sessions is None or not settings.password_hash:
        raise HTTPException(status_code=503, detail="Authentication not configured")
    key = f"{request.client.host if request.client else 'unknown'}:{payload.username}"
    if not await sessions.permit_login(key, time.time()):
        raise HTTPException(status_code=429, detail="Too many login attempts")
    if payload.username != settings.username or not verify_password(
        payload.password, settings.password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    await sessions.reset_login(key)
    token = secrets.token_urlsafe(32)
    await sessions.create(token, settings.username, time.time() + settings.lifetime_seconds)
    response.set_cookie(
        "qts_session",
        token,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="strict",
        max_age=settings.lifetime_seconds,
    )
    return {"actor": settings.username, "csrf_token": csrf_for(token)}


@router.post("/v1/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("qts_session", "")
    sessions = getattr(request.app.state, "sessions", None)
    if sessions is not None and token:
        await sessions.revoke(token)
    response.delete_cookie("qts_session")
    return {"ok": True}


@router.get("/v1/auth/me")
async def me(request: Request, response: Response):
    # Session cookies survive reloads; the in-memory frontend CSRF token does not.
    response.headers["Cache-Control"] = "no-store"
    token = request.cookies.get("qts_session", "")
    enabled = request.app.state.auth_settings.enabled
    return {
        "actor": request.state.actor,
        "csrf_token": csrf_for(token) if enabled and token else "",
    }


@router.post("/v1/trading/orders", status_code=202)
async def place_order(payload: PlaceOrderRequest, request: Request):
    command = PlaceOrderCommand(
        command_id=payload.command_id,
        account_id=payload.account_id,
        symbol=payload.symbol,
        base_asset=payload.base_asset,
        quote_asset=payload.quote_asset,
        side=payload.side,
        qty=payload.qty,
        price=payload.price,
        deployment_id=payload.deployment_id,
        order_type=payload.order_type,
        time_in_force=payload.time_in_force,
        decision_id=payload.decision_id,
    )
    try:
        receipt = await _executor(request).place(command, actor=request.state.actor)
    except Exception as exc:
        raise _handle(exc) from exc
    return _receipt(receipt)


@router.post("/v1/trading/orders/{cl_ord_id}/cancel", status_code=202)
async def cancel_order(cl_ord_id: str, payload: CancelRequest, request: Request):
    try:
        receipt = await _executor(request).cancel(
            payload.command_id,
            payload.account_id,
            cl_ord_id,
            actor=request.state.actor,
        )
    except Exception as exc:
        raise _handle(exc) from exc
    return _receipt(receipt)


@router.post("/v1/trading/control", status_code=202)
async def control_action(payload: ControlRequest, request: Request):
    try:
        receipt = await _executor(request).control(
            payload.command_id,
            payload.account_id,
            payload.action,
            actor=request.state.actor,
            code=payload.code,
            evidence=payload.evidence,
        )
    except Exception as exc:
        raise _handle(exc) from exc
    return _receipt(receipt)


@router.get("/v1/commands/{command_id}")
async def get_command(command_id: str, request: Request, account_id: str = Query(...)):
    record = await _service(request).command(account_id, command_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Command not found")
    return record


@router.post("/v1/trading/reconnect", status_code=202)
async def request_reconnect(payload: CancelRequest, request: Request):
    try:
        receipt = await _executor(request).control(
            payload.command_id, payload.account_id, "RECONNECT", actor=request.state.actor
        )
    except Exception as exc:
        raise _handle(exc) from exc
    return _receipt(receipt)


@router.get("/v1/trading/orders")
async def list_orders(request: Request, account_id: str = Query(...), limit: int = 200):
    try:
        return await _service(request).list_orders(account_id, min(max(limit, 1), 2000))
    except Exception as exc:
        raise _handle(exc) from exc


@router.get("/v1/trading/orders/{cl_ord_id}")
async def get_order(cl_ord_id: str, request: Request, account_id: str = Query(...)):
    order = await _service(request).order(account_id, cl_ord_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("/v1/trading/accounts/{account_id}/risk")
async def risk_view(account_id: str, request: Request):
    try:
        view: AccountRiskView = await _service(request).risk_view(account_id)
    except Exception as exc:
        raise _handle(exc) from exc
    return json_value(view)


@router.get("/v1/trading/metrics")
async def metrics(request: Request, account_id: str = Query(...)):
    try:
        return await _service(request).metrics(account_id)
    except Exception as exc:
        raise _handle(exc) from exc


# Fact write endpoints (executions / observations / reconcile / resolve) are
# deliberately absent from the control plane. Only the execution process writes
# account facts; an operator session settles a difference through the durable
# RESOLVE control command above, which the execution process checks and applies.
