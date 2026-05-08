"""
Risk API Routes
==============
Risk limits management endpoints (versioned).

级别映射矩阵 (Level Mapping Matrix):
======================================
| RejectionReason         | recommended_level | 外部动作 (KillSwitch) |
|-------------------------|------------------|----------------------|
| LOW_RISK               | 0 (L0_NORMAL)    | 无动作               |
| MINOR_STREAM_LAG       | 1 (L1_NO_NEW_POS)| 禁止新开仓           |
| MAJOR_STREAM_LAG       | 1 (L1_NO_NEW_POS)| 禁止新开仓           |
| PRIVATE_STREAM_DOWN    | 2 (L2_CLOSE_ONLY)| 只允许平仓           |
| RATE_LIMIT_EXCEEDED    | 2 (L2_CLOSE_ONLY)| 只允许平仓           |
| CRITICAL_FAILURE       | 3 (L3_FULL_STOP) | 完全停止             |
| CONNECTION_LOST        | 3 (L3_FULL_STOP) | 完全停止             |

升降级规则：
- recommended_level > 当前级别：执行升级
- recommended_level <= 当前级别：保持不降级（Fail-Closed）
- 同一 dedup_key 不重复触发升级（幂等保护）
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from trader.adapters.persistence.market_risk_audit_repository import (
    get_market_risk_audit_repository,
)
from trader.api.crypto_risk_runtime import (
    crypto_risk_budget_to_dict,
    crypto_risk_probe_result_to_dict,
    crypto_risk_runtime_status_to_dict,
    get_crypto_risk_runtime_manager,
    merge_crypto_risk_budget,
)
from trader.api.models.schemas import (
    ActionResult,
    CryptoRiskAuditSummaryItem,
    CryptoRiskAuditSummaryResponse,
    CryptoRiskBudgetUpdateRequest,
    CryptoRiskProbeRequest,
    CryptoRiskProbeResponse,
    CryptoRiskRuntimeStatus,
    EventEnvelope,
    KillSwitchSetRequest,
    RiskEventIngestRequest,
    TimeWindowConfigSchema,
    TimeWindowConfigUpdateRequest,
    TimeWindowSlotSchema,
    VersionedConfig,
    VersionedConfigUpsertRequest,
)
from trader.core.domain.models.market_risk import AssetClass, MarketRiskAuditEvent
from trader.core.domain.rules.time_window_policy import TimeWindowConfig as TWConfig
from trader.core.domain.rules.time_window_policy import (
    TimeWindowPeriod,
    TimeWindowPolicy,
    TimeWindowSlot,
)
from trader.services import EventService, KillSwitchService, RiskService

router = APIRouter(tags=["Risk"])
logger = logging.getLogger(__name__)
EFFECT_STATUS_TIMEOUT_SEC = 2.0
CRYPTO_RISK_STREAM_KEY = "risk:crypto"
CRYPTO_RISK_BUDGET_UPDATED_EVENT = "crypto_risk.budget_updated"
CRYPTO_RISK_PROBE_RUN_EVENT = "crypto_risk.probe_run"
CRYPTO_RISK_PRE_TRADE_REJECTED_EVENT = "crypto_risk.pre_trade_rejected"


def _killswitch_matches(current_state, expected_state) -> bool:
    return (
        current_state.scope == expected_state.scope
        and current_state.level == expected_state.level
        and current_state.reason == expected_state.reason
        and current_state.updated_by == expected_state.updated_by
    )


async def _apply_killswitch_effect(
    service: RiskService,
    killswitch_service: KillSwitchService,
    *,
    scope: str,
    level: int,
    upgrade_key: str,
    reason: str,
    updated_by: str,
) -> tuple[bool, str | None]:
    """
    Apply KillSwitch side-effect with compensation.

    If the KillSwitch has already reached the target level, only mark the effect
    as applied to keep retries idempotent.
    """
    previous_state = killswitch_service.get_state(scope)
    killswitch_changed = False
    level_names = {0: "NORMAL", 1: "NO_NEW_POSITIONS", 2: "CLOSE_ONLY", 3: "FULL_STOP"}

    try:
        if previous_state.level < level:
            logger.info(
                "[KillSwitch] [RiskAPI] Applying KillSwitch effect: upgrade_key=%s scope=%s level=%s (%s) -> %s (%s) reason=%s updated_by=%s",
                upgrade_key,
                scope,
                previous_state.level,
                level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
                level,
                level_names.get(level, f"LEVEL_{level}"),
                reason,
                updated_by,
            )
            await killswitch_service.set_state_durable(
                KillSwitchSetRequest(
                    scope=scope,
                    level=level,
                    reason=reason,
                    updated_by=updated_by,
                )
            )
            killswitch_changed = True
        else:
            logger.info(
                "[KillSwitch] [RiskAPI] KillSwitch effect already satisfied: upgrade_key=%s scope=%s current_level=%s (%s) target_level=%s (%s)",
                upgrade_key,
                scope,
                previous_state.level,
                level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
                level,
                level_names.get(level, f"LEVEL_{level}"),
            )

        await asyncio.wait_for(
            service.mark_effect_applied(upgrade_key), timeout=EFFECT_STATUS_TIMEOUT_SEC
        )
        return True, None
    except Exception as exc:
        compensation_error: str | None = None
        if killswitch_changed:
            current_state = killswitch_service.get_state(scope)
            try:
                if _killswitch_matches(current_state, previous_state):
                    logger.info(
                        "[KillSwitch] [RiskAPI] KillSwitch compensation already converged: upgrade_key=%s scope=%s level=%s (%s)",
                        upgrade_key,
                        scope,
                        previous_state.level,
                        level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
                    )
                else:
                    logger.warning(
                        "[KillSwitch] [RiskAPI] Reverting KillSwitch effect: upgrade_key=%s scope=%s from_level=%s (%s) to_level=%s (%s) after error=%s",
                        upgrade_key,
                        scope,
                        current_state.level,
                        level_names.get(current_state.level, f"LEVEL_{current_state.level}"),
                        previous_state.level,
                        level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
                        exc,
                    )
                    await killswitch_service.set_state_durable(
                        KillSwitchSetRequest(
                            scope=previous_state.scope,
                            level=previous_state.level,
                            reason=previous_state.reason,
                            updated_by=previous_state.updated_by,
                        )
                    )
            except Exception as rollback_exc:
                verified_state = killswitch_service.get_state(scope)
                if _killswitch_matches(verified_state, previous_state):
                    logger.info(
                        "[KillSwitch] [RiskAPI] KillSwitch compensation converged after rollback exception: upgrade_key=%s scope=%s level=%s (%s)",
                        upgrade_key,
                        scope,
                        previous_state.level,
                        level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
                    )
                else:
                    compensation_error = (
                        f" rollback_failed: {rollback_exc}; "
                        f"current_level={verified_state.level}; expected_level={previous_state.level}"
                    )
                    logger.exception(
                        "[KillSwitch] [RiskAPI] KillSwitch compensation failed: upgrade_key=%s scope=%s current_level=%s (%s) expected_level=%s (%s)",
                        upgrade_key,
                        scope,
                        verified_state.level,
                        level_names.get(verified_state.level, f"LEVEL_{verified_state.level}"),
                        previous_state.level,
                        level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
                    )
            else:
                verified_state = killswitch_service.get_state(scope)
                if _killswitch_matches(verified_state, previous_state):
                    logger.info(
                        "[KillSwitch] [RiskAPI] KillSwitch compensation applied: upgrade_key=%s scope=%s level=%s (%s)",
                        upgrade_key,
                        scope,
                        previous_state.level,
                        level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
                    )
                else:
                    compensation_error = (
                        f" rollback_inconsistent: current_level={verified_state.level}; "
                        f"expected_level={previous_state.level}"
                    )
                    logger.error(
                        "[KillSwitch] [RiskAPI] KillSwitch compensation left inconsistent state: upgrade_key=%s scope=%s current_level=%s (%s) expected_level=%s (%s)",
                        upgrade_key,
                        scope,
                        verified_state.level,
                        level_names.get(verified_state.level, f"LEVEL_{verified_state.level}"),
                        previous_state.level,
                        level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
                    )

        error_message = f"{exc}{compensation_error or ''}"
        try:
            await asyncio.wait_for(
                service.mark_effect_failed(upgrade_key, error_message),
                timeout=EFFECT_STATUS_TIMEOUT_SEC,
            )
        except Exception as mark_failed_exc:
            logger.exception(
                "Failed to persist effect failure status upgrade_key=%s scope=%s error=%s",
                upgrade_key,
                scope,
                mark_failed_exc,
            )
            error_message = f"{error_message} mark_failed_error: {mark_failed_exc}"
        return False, error_message


@router.get("/v1/risk/limits", response_model=Optional[VersionedConfig])
async def get_risk_limits(
    scope: str = Query("GLOBAL", description="Risk scope: GLOBAL or per account/strategy")
):
    """
    Get latest risk limits.

    Returns the latest risk limits for the specified scope.
    """
    service = RiskService()
    return service.get_limits(scope)


@router.post("/v1/risk/limits", response_model=VersionedConfig)
async def set_risk_limits(request: VersionedConfigUpsertRequest):
    """
    Set new risk limits.

    Creates a new version of risk limits.
    """
    service = RiskService()
    return service.set_limits(request)


@router.get("/v1/risk/crypto/runtime", response_model=CryptoRiskRuntimeStatus)
async def get_crypto_risk_runtime_status():
    """Return crypto pre-trade risk runtime wiring status."""
    manager = get_crypto_risk_runtime_manager()
    return crypto_risk_runtime_status_to_dict(manager.status())


@router.patch("/v1/risk/crypto/budget", response_model=CryptoRiskRuntimeStatus)
async def patch_crypto_risk_budget(request: CryptoRiskBudgetUpdateRequest):
    """Hot-update crypto risk budget and late-bind the rebuilt pre-trade check."""
    manager = get_crypto_risk_runtime_manager()
    before_status = manager.status()
    try:
        budget = merge_crypto_risk_budget(
            before_status.risk_budget,
            symbol_notional_caps=request.symbol_notional_caps,
            symbol_clusters=request.symbol_clusters,
            cluster_notional_caps=request.cluster_notional_caps,
            total_notional_cap=request.total_notional_cap,
            max_margin_ratio=request.max_margin_ratio,
            min_liquidation_buffer_ratio=request.min_liquidation_buffer_ratio,
        )
        status = await manager.update_budget(budget, updated_by=request.updated_by)
        await _append_crypto_budget_audit_event(before_status, status, request.updated_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[CryptoRisk] Failed to hot-update risk budget")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return crypto_risk_runtime_status_to_dict(status)


@router.post("/v1/risk/crypto/probe", response_model=CryptoRiskProbeResponse)
async def probe_crypto_risk_runtime(request: CryptoRiskProbeRequest):
    """Run a read-only crypto risk source readiness probe."""
    manager = get_crypto_risk_runtime_manager()
    try:
        result = await manager.probe(
            symbols=tuple(request.symbols),
            requested_by=request.requested_by,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[CryptoRisk] Failed to run readiness probe")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    payload = crypto_risk_probe_result_to_dict(result)
    await _append_crypto_probe_audit_event(payload)
    return payload


@router.get("/v1/risk/crypto/budget/audit", response_model=list[EventEnvelope])
async def list_crypto_risk_budget_audit(
    since_ts_ms: Optional[int] = Query(None, description="Filter by timestamp (ms)"),
    limit: int = Query(200, ge=1, le=2000, description="Max audit events"),
):
    """List crypto risk budget update audit events."""
    events = await get_market_risk_audit_repository().list_events(
        stream_key=CRYPTO_RISK_STREAM_KEY,
        event_type=CRYPTO_RISK_BUDGET_UPDATED_EVENT,
        trace_id=None,
        since_ts_ms=since_ts_ms,
        limit=limit,
    )
    return [EventEnvelope(**_to_event_envelope_dict(event)) for event in events]


@router.get("/v1/risk/crypto/audit/summary", response_model=CryptoRiskAuditSummaryResponse)
async def get_crypto_risk_audit_summary(
    group_by: Literal["reason", "symbol", "strategy", "risk_level"] = Query(
        ..., description="分组维度: rejection reason | symbol | strategy | risk_level"
    ),
    since_ts_ms: Optional[int] = Query(None, description="过滤起始时间戳（Unix ms）"),
    limit: int = Query(50, ge=1, le=200, description="最多返回分组数"),
    event_type: Optional[str] = Query(
        "crypto_risk.pre_trade_rejected",
        description="事件类型，默认 crypto_risk.pre_trade_rejected",
    ),
):
    """
    聚合统计数字货币风控审计事件。

    支持四个分组维度：
    - reason: 按 rejection_reason 分组
    - symbol: 按 payload.symbol 分组
    - strategy: 按 payload.strategy_id 分组
    - risk_level: 按 payload.risk_level 分组

    每条记录返回 key（分组值）、count（该组事件总数）、
    latest_ts_ms（最新事件时间戳）、sample_event_id（最新事件ID）。
    """
    key_field_map = {
        "reason": "rejection_reason",
        "symbol": "symbol",
        "strategy": "strategy_id",
        "risk_level": "risk_level",
    }

    events = await get_market_risk_audit_repository().list_events(
        stream_key=CRYPTO_RISK_STREAM_KEY,
        event_type=event_type,
        trace_id=None,
        since_ts_ms=since_ts_ms,
        limit=5000,
    )

    if not events:
        return CryptoRiskAuditSummaryResponse(
            items=[],
            total=0,
            since_ts_ms=since_ts_ms or 0,
        )

    field = key_field_map[group_by]

    def _normalize_key(payload: dict[str, Any], field_name: str) -> str:
        """Extract and normalize a key field: strategy gets fallback, all others normalize None/empty."""
        raw: Any
        if field_name == "strategy_id":
            # strategy_id may be absent/None; fall back to strategy_name then "unknown"
            raw = payload.get("strategy_id")
            if not raw:
                raw = payload.get("strategy_name")
        else:
            raw = payload.get(field_name)
        normalized = str(raw).strip() if raw else ""
        return normalized if normalized else "unknown"

    # Aggregate
    buckets: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        payload = event.get("payload", {})
        key = _normalize_key(payload, field)
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(event)

    total_count = sum(len(v) for v in buckets.values())

    items = []
    for key, bucket in buckets.items():
        bucket.sort(key=lambda e: e.get("ts_ms", 0), reverse=True)
        latest = bucket[0]
        sample_id = str(latest.get("event_id")) if latest.get("event_id") is not None else None
        items.append(
            CryptoRiskAuditSummaryItem(
                key=key,
                count=len(bucket),
                latest_ts_ms=latest.get("ts_ms", 0),
                sample_event_id=sample_id,
            )
        )

    items.sort(key=lambda x: x.count, reverse=True)
    items = items[:limit]

    return CryptoRiskAuditSummaryResponse(
        items=items,
        total=total_count,
        since_ts_ms=since_ts_ms or 0,
    )


@router.get("/v1/risk/crypto/audit", response_model=list[EventEnvelope])
async def list_crypto_risk_audit(
    event_type: Optional[str] = Query(None, description="Filter by crypto risk event type"),
    trace_id: Optional[str] = Query(None, description="Filter by decision trace ID"),
    signal_id: Optional[str] = Query(None, description="Filter by strategy signal ID"),
    since_ts_ms: Optional[int] = Query(None, description="Filter by timestamp (ms)"),
    limit: int = Query(200, ge=1, le=2000, description="Max audit events"),
):
    """List crypto risk audit events from the market-neutral risk audit stream."""
    query_limit = 2000 if signal_id else limit
    events = await get_market_risk_audit_repository().list_events(
        stream_key=CRYPTO_RISK_STREAM_KEY,
        event_type=event_type,
        trace_id=trace_id,
        since_ts_ms=since_ts_ms,
        limit=query_limit,
    )
    if signal_id:
        signal_id_text = signal_id.strip()
        events = [
            event
            for event in events
            if str(event.get("payload", {}).get("signal_id", "")) == signal_id_text
        ]
    return [EventEnvelope(**_to_event_envelope_dict(event)) for event in events[:limit]]


async def _append_crypto_probe_audit_event(payload: dict[str, Any]) -> None:
    await _append_crypto_market_audit_event(
        event_type=CRYPTO_RISK_PROBE_RUN_EVENT,
        trace_prefix="crypto-risk-probe",
        payload=payload,
    )


async def _append_crypto_budget_audit_event(
    before_status: Any,
    after_status: Any,
    updated_by: str,
) -> None:
    await _append_crypto_market_audit_event(
        event_type=CRYPTO_RISK_BUDGET_UPDATED_EVENT,
        trace_prefix="crypto-risk-budget",
        payload={
            "updated_by": updated_by,
            "previous_budget": crypto_risk_budget_to_dict(before_status.risk_budget),
            "new_budget": crypto_risk_budget_to_dict(after_status.risk_budget),
            "runtime_before": _crypto_runtime_audit_view(before_status),
            "runtime_after": _crypto_runtime_audit_view(after_status),
        },
    )


async def _append_crypto_market_audit_event(
    *,
    event_type: str,
    trace_prefix: str,
    payload: dict[str, Any],
) -> None:
    decision_trace_id = str(
        payload.get("decision_trace_id") or f"{trace_prefix}:{uuid.uuid4().hex}"
    )
    audit_payload = {**payload, "decision_trace_id": decision_trace_id}
    event = MarketRiskAuditEvent(
        stream_key=CRYPTO_RISK_STREAM_KEY,
        event_type=event_type,
        schema_version=1,
        trace_id=decision_trace_id,
        ts_ms=int(time.time() * 1000),
        asset_class=AssetClass.CRYPTO,
        venue="binance",
        account_id="crypto_risk",
        payload=audit_payload,
    )
    await get_market_risk_audit_repository().append(event)


def _to_event_envelope_dict(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": record.get("event_id"),
        "stream_key": record["stream_key"],
        "event_type": record["event_type"],
        "schema_version": record.get("schema_version", 1),
        "trace_id": record.get("trace_id"),
        "ts_ms": record["ts_ms"],
        "payload": record.get("payload", {}),
    }


def _crypto_runtime_audit_view(status: Any) -> dict[str, Any]:
    return {
        "enabled": bool(status.enabled),
        "wired": bool(status.wired),
        "fail_closed": bool(status.fail_closed),
        "execution_env": status.execution_env,
        "base_symbols": list(status.base_symbols),
        "last_error": status.last_error,
    }


@router.post("/v1/risk/events", response_model=ActionResult)
async def ingest_risk_event(request: RiskEventIngestRequest, response: Response):
    """
    Ingest risk event with recommended_level-driven KillSwitch upgrade.

    闭环逻辑（原子事务）：
    BEGIN -> dedup -> upgrade record -> side-effect intent -> COMMIT

    - Returns 201 when a new dedup_key is accepted.
    - Returns 409 when dedup_key already exists (idempotent duplicate).
    - Returns 500 when side-effect fails (Fail-Closed).
    """
    service = RiskService()
    killswitch_service = KillSwitchService()

    current_state = killswitch_service.get_state(request.scope)
    current_level = current_state.level

    if request.recommended_level > current_level:
        upgrade_key = f"upgrade:{request.scope}:{request.recommended_level}:{request.dedup_key}"

        event_data = request.model_dump()
        event_id, created, is_first_upgrade, is_first_effect = (
            await service.ingest_event_with_upgrade(
                event_data, upgrade_key, request.recommended_level
            )
        )

        if is_first_effect:
            applied, error = await _apply_killswitch_effect(
                service,
                killswitch_service,
                scope=request.scope,
                level=request.recommended_level,
                upgrade_key=upgrade_key,
                reason=f"Risk event upgrade: {request.reason}",
                updated_by=f"risk_event:{request.dedup_key}",
            )
            if not applied:
                response.status_code = 500
                return ActionResult(ok=False, message=f"upgrade failed: {error}")

        if created:
            response.status_code = 201
            return ActionResult(ok=True, message="risk event accepted")

        response.status_code = 409
        return ActionResult(ok=True, message="risk event duplicate")
    else:
        created = await service.ingest_event(request)

        if created:
            response.status_code = 201
            return ActionResult(ok=True, message="risk event accepted")

        response.status_code = 409
        return ActionResult(ok=True, message="risk event duplicate")


@router.post("/v1/risk/recover", response_model=ActionResult)
async def recover_pending_effects():
    """
    Recovery endpoint: scan and retry pending/failed effects.

    This is a manual trigger for recovery after failures.
    """
    service = RiskService()
    killswitch_service = KillSwitchService()

    try:
        pending = await service.get_pending_effects()
    except Exception as e:
        return ActionResult(ok=False, message=f"获取待恢复效果失败: {str(e)}")

    if not pending:
        return ActionResult(ok=True, message="无待恢复效果")

    recovered = 0
    failed = 0

    for effect in pending:
        upgrade_key = effect["upgrade_key"]
        level = effect.get("level", 1)
        applied, _error = await _apply_killswitch_effect(
            service,
            killswitch_service,
            scope=effect.get("scope", "GLOBAL"),
            level=level,
            upgrade_key=upgrade_key,
            reason=f"Recovery: {effect.get('last_error', 'unknown')}",
            updated_by="recovery_trigger",
        )
        if applied:
            recovered += 1
        else:
            failed += 1

    return ActionResult(
        ok=True, message=f"recovery completed: {recovered} recovered, {failed} failed"
    )


# ==================== Time Window Config Endpoints ====================

# Singleton TimeWindowPolicy instance with async-safe initialization
_time_window_policy: TimeWindowPolicy | None = None
_time_window_lock: asyncio.Lock = asyncio.Lock()


async def _get_time_window_policy() -> TimeWindowPolicy:
    """
    获取或创建 TimeWindowPolicy 单例（async-safe）。

    这是模块级别的单例，用于存储和管理时间窗口配置。
    使用双检锁保证线程安全。
    """
    global _time_window_policy
    if _time_window_policy is None:
        async with _time_window_lock:
            # Double-check after acquiring lock
            if _time_window_policy is None:
                _time_window_policy = TimeWindowPolicy()
    return _time_window_policy


@router.get("/v1/risk/time-window/config", response_model=TimeWindowConfigSchema)
async def get_time_window_config():
    """
    Get current time window configuration.

    Returns the current time window configuration including all slots
    and the default coefficient.
    """
    policy = await _get_time_window_policy()
    config = policy.config

    return TimeWindowConfigSchema(
        slots=[
            TimeWindowSlotSchema(
                period=s.period.value,
                start_hour=s.start_hour,
                start_minute=s.start_minute,
                end_hour=s.end_hour,
                end_minute=s.end_minute,
                position_coefficient=s.position_coefficient,
                allow_new_position=s.allow_new_position,
            )
            for s in config.slots
        ],
        default_coefficient=config.default_coefficient,
    )


@router.get("/v1/risk/time-window/evaluate")
async def evaluate_time_window(
    hour: int = Query(..., ge=0, le=23), minute: int = Query(..., ge=0, le=59)
):
    """
    Evaluate time window policy for a specific time.

    This endpoint allows evaluating what the time window policy
    would return for a given UTC time, useful for testing and
    verification without modifying the current configuration.

    Returns the period, position_coefficient, and allow_new_position
    for the given time.
    """
    policy = await _get_time_window_policy()
    ctx = policy.evaluate(hour, minute)

    return {
        "period": ctx.period.value,
        "position_coefficient": ctx.position_coefficient,
        "allow_new_position": ctx.allow_new_position,
    }


@router.put("/v1/risk/time-window/config", response_model=TimeWindowConfigSchema)
async def update_time_window_config(request: TimeWindowConfigUpdateRequest):
    """
    Update time window configuration.

    This endpoint allows hot-updating the time window configuration
    without restarting the service.

    The configuration change is validated and applied atomically.
    On successful update, returns the new configuration.
    """
    policy = await _get_time_window_policy()

    # Convert schema to domain model
    # Note: period validation is handled by Pydantic's Literal type at request parsing time,
    # but we add defensive handling for ValueError from TimeWindowPeriod enum conversion
    try:
        slots = [
            TimeWindowSlot(
                period=TimeWindowPeriod(s.period),
                start_hour=s.start_hour,
                start_minute=s.start_minute,
                end_hour=s.end_hour,
                end_minute=s.end_minute,
                position_coefficient=s.position_coefficient,
                allow_new_position=s.allow_new_position,
            )
            for s in request.slots
        ]
    except ValueError as e:
        logger.warning(f"Invalid time window period in config update: {e}")
        raise HTTPException(status_code=422, detail=f"Invalid period value in slot: {e}")

    new_config = TWConfig(
        slots=slots,
        default_coefficient=request.default_coefficient,
    )

    # Update the policy with async-safe locking
    # Note: We use the same _time_window_lock to ensure atomic updates
    async with _time_window_lock:
        policy.update_config(new_config)

    logger.info(
        "TimeWindowConfig updated",
        extra={
            "updated_by": request.updated_by,
            "slot_count": len(request.slots),
            "default_coefficient": request.default_coefficient,
        },
    )

    # Return the new configuration
    return TimeWindowConfigSchema(
        slots=[
            TimeWindowSlotSchema(
                period=s.period.value,
                start_hour=s.start_hour,
                start_minute=s.start_minute,
                end_hour=s.end_hour,
                end_minute=s.end_minute,
                position_coefficient=s.position_coefficient,
                allow_new_position=s.allow_new_position,
            )
            for s in policy.config.slots
        ],
        default_coefficient=policy.config.default_coefficient,
    )
