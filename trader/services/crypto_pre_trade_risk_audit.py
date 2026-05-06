from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from enum import Enum
from typing import Any

from trader.adapters.persistence.market_risk_audit_repository import (
    get_market_risk_audit_repository,
)
from trader.core.application.risk_engine import (
    KillSwitchLevel,
    RejectionReason,
    RiskCheckResult,
    RiskLevel,
)
from trader.core.domain.models.market_risk import AssetClass, MarketRiskAuditEvent
from trader.core.domain.models.signal import Signal

logger = logging.getLogger(__name__)

CRYPTO_RISK_STREAM_KEY = "risk:crypto"
CRYPTO_PRE_TRADE_REJECTED_EVENT = "crypto_risk.pre_trade_rejected"


def build_audited_crypto_pre_trade_risk_check(
    check: Callable[[Signal], Awaitable[RiskCheckResult] | RiskCheckResult],
) -> Callable[[Signal], Awaitable[RiskCheckResult]]:
    async def _audited(signal: Signal) -> RiskCheckResult:
        try:
            result_or_awaitable = check(signal)
            result = (
                await result_or_awaitable
                if inspect.isawaitable(result_or_awaitable)
                else result_or_awaitable
            )
        except Exception as exc:
            await _append_pre_trade_rejection_event(
                signal=signal,
                result=RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.CRITICAL,
                    rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
                    message=f"Pre-trade risk check unavailable: {exc}",
                    details={"error": str(exc)},
                ),
            )
            raise

        if not result.passed:
            await _append_pre_trade_rejection_event(signal=signal, result=result)
        return result

    return _audited


async def _append_pre_trade_rejection_event(
    *,
    signal: Signal,
    result: RiskCheckResult,
) -> None:
    details = _json_safe(result.details or {})
    recommended_level = _recommended_killswitch_level(result, details)
    decision_trace_id = _trace_id(signal)
    payload = {
        "decision_trace_id": decision_trace_id,
        "signal_id": signal.signal_id,
        "strategy_id": _metadata_text(signal, "strategy_id"),
        "deployment_id": _metadata_text(signal, "deployment_id"),
        "strategy_name": signal.strategy_name,
        "symbol": signal.symbol,
        "signal_type": signal.signal_type.value,
        "qty": str(signal.quantity),
        "price": str(signal.price),
        "confidence": str(signal.confidence),
        "rejection_reason": (
            result.rejection_reason.value if result.rejection_reason is not None else None
        ),
        "risk_level": result.risk_level.value,
        "message": result.message,
        "details": details,
        "recommended_killswitch_level": recommended_level,
    }
    if signal.stop_loss is not None:
        payload["stop_loss"] = str(signal.stop_loss)
    if signal.take_profit is not None:
        payload["take_profit"] = str(signal.take_profit)

    event = MarketRiskAuditEvent(
        stream_key=CRYPTO_RISK_STREAM_KEY,
        event_type=CRYPTO_PRE_TRADE_REJECTED_EVENT,
        schema_version=1,
        trace_id=decision_trace_id,
        ts_ms=int(time.time() * 1000),
        asset_class=AssetClass.CRYPTO,
        venue="binance",
        account_id="crypto_risk",
        payload=payload,
    )

    try:
        await get_market_risk_audit_repository().append(event)
    except Exception as exc:
        logger.warning("Failed to append crypto pre-trade rejection audit event: %s", exc)


def _trace_id(signal: Signal) -> str:
    trace_id = signal.metadata.get("decision_trace_id") or signal.metadata.get("trace_id")
    if trace_id:
        return str(trace_id)
    return f"crypto-pre-trade:{signal.signal_id}"


def _metadata_text(signal: Signal, key: str) -> str | None:
    value = signal.metadata.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _recommended_killswitch_level(result: RiskCheckResult, details: Any) -> int:
    if isinstance(details, dict) and details.get("recommended_killswitch_level") is not None:
        return int(details["recommended_killswitch_level"])

    if result.rejection_reason == RejectionReason.RISK_SYSTEM_ERROR:
        return int(KillSwitchLevel.L3_LIQUIDATE_AND_DISCONNECT)
    if result.rejection_reason in {
        RejectionReason.DAILY_LOSS_LIMIT,
        RejectionReason.MAX_DRAWDOWN,
        RejectionReason.CRYPTO_MARGIN_LIMIT,
        RejectionReason.CRYPTO_LIQUIDATION_BUFFER,
    }:
        return int(KillSwitchLevel.L2_CANCEL_ALL_AND_HALT)
    return int(KillSwitchLevel.L1_NO_NEW_POSITIONS)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value
