"""
KillSwitch API Routes
=====================
Kill switch (emergency stop) management endpoints.
"""
import logging
from typing import Optional
from fastapi import APIRouter, Query

from trader.api.models.schemas import KillSwitchState, KillSwitchSetRequest
from trader.services import KillSwitchService

router = APIRouter(tags=["KillSwitch"])
logger = logging.getLogger(__name__)


@router.get("/v1/killswitch", response_model=KillSwitchState)
async def get_kill_switch_state(scope: str = Query("GLOBAL", description="Scope: GLOBAL or per account")):
    """
    Get kill switch state.

    Returns the current kill switch state for the specified scope.
    """
    service = KillSwitchService()
    return service.get_state(scope)


@router.post("/v1/killswitch", response_model=KillSwitchState)
async def set_kill_switch(request: KillSwitchSetRequest):
    """
    Set kill switch level.

    Sets the kill switch level (0-3) for emergency control.
    - Level 0: Normal operation
    - Level 1: No new positions
    - Level 2: Close positions only
    - Level 3: Full stop
    """
    service = KillSwitchService()

    # ====== 统一日志入口：追踪所有 KillSwitch 变更 ======
    level_names = {0: "NORMAL", 1: "NO_NEW_POSITIONS", 2: "CLOSE_ONLY", 3: "FULL_STOP"}
    previous_state = service.get_state(request.scope)

    logger.info(
        "[KillSwitch] [API] Incoming set request: "
        "scope=%s current_level=%s (%s) -> requested_level=%s (%s) "
        "reason='%s' updated_by='%s'",
        request.scope,
        previous_state.level,
        level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
        request.level,
        level_names.get(request.level, f"LEVEL_{request.level}"),
        request.reason or "N/A",
        request.updated_by,
    )

    new_state = service.set_state(request)

    logger.info(
        "[KillSwitch] [API] State changed: "
        "scope=%s level=%s (%s) -> %s (%s) by %s",
        request.scope,
        previous_state.level,
        level_names.get(previous_state.level, f"LEVEL_{previous_state.level}"),
        new_state.level,
        level_names.get(new_state.level, f"LEVEL_{new_state.level}"),
        request.updated_by,
    )

    # ====== SSE 广播：KillSwitch 变化后通知所有前端 ======
    try:
        from trader.api.routes.sse import broadcast_monitor_update, broadcast_strategy_update
        from trader.storage.in_memory import get_storage

        # 1. 广播 Monitor 页面更新（killswitch_level 会变化）
        storage = get_storage()
        ks_state = storage.get_kill_switch("GLOBAL")
        await broadcast_monitor_update({
            "killswitch_level": ks_state.get("level", 0),
            "killswitch_scope": ks_state.get("scope", "GLOBAL"),
            "killswitch_reason": ks_state.get("reason"),
            "updated_by": request.updated_by,
            "_triggered_by": "killswitch_change",
        })

        # 2. 如果 L2+ 触发，广播策略状态更新（告知所有策略被停止）
        if new_state.level >= 2:
            await broadcast_strategy_update("*", {
                "event": "killswitch_l2_plus",
                "level": new_state.level,
                "level_name": level_names.get(new_state.level, f"LEVEL_{new_state.level}"),
                "reason": new_state.reason,
                "updated_by": request.updated_by,
            })
            logger.info("[KillSwitch] [API] SSE broadcasts sent for L2+ trigger")

        # ====== L2+: 撤销所有挂单 ======
        if new_state.level >= 2 and previous_state.level < 2:
            try:
                from trader.services.order import OrderService
                order_service = OrderService()
                all_orders = order_service.list_orders(limit=10000)
                pending_orders = [
                    o for o in all_orders
                    if o.status in ("NEW", "SUBMITTED", "PARTIALLY_FILLED", "PENDING", "CREATED")
                ]
                cancelled_count = 0
                for order in pending_orders:
                    result = order_service.cancel_order(order.cl_ord_id)
                    if result.ok:
                        cancelled_count += 1
                        logger.info(f"[KillSwitch] [API] Cancelled pending order: {order.cl_ord_id}")
                logger.info(
                    f"[KillSwitch] [API] L2+ triggered: cancelled {cancelled_count}/{len(pending_orders)} pending orders"
                )
            except Exception as cancel_err:
                logger.error(f"[KillSwitch] [API] Failed to cancel pending orders: {cancel_err}")

        # ====== L0: 自动恢复被 KillSwitch 停止的策略 ======
        if new_state.level == 0 and previous_state.level >= 2:
            try:
                from trader.api.routes.strategies import get_strategy_runner, get_strategy_orchestrator

                runner = get_strategy_runner()
                orchestrator = get_strategy_orchestrator()
                infos = runner.list_strategies()

                recovered_count = 0
                recovered_list = []

                for info in infos:
                    # 只恢复因 KillSwitch 而停止的策略（blocked_reason 包含 "KillSwitch"）
                    if info.status.value == "STOPPED" and info.blocked_reason and "KillSwitch" in info.blocked_reason:
                        strategy_id = info.strategy_id
                        try:
                            # 获取该策略之前运行的 symbol（从 orchestrator context 获取）
                            ctx = orchestrator.get_context(strategy_id)
                            symbol = ctx.symbol if ctx and ctx.symbol else "BTCUSDT"

                            # 重启策略
                            await runner.start(strategy_id)
                            await orchestrator.start_strategy(strategy_id, symbol)

                            recovered_count += 1
                            recovered_list.append(strategy_id)
                            logger.info(
                                f"[KillSwitch] [API] Auto-recovered strategy: {strategy_id} on {symbol}"
                            )
                        except Exception as recover_err:
                            logger.error(
                                f"[KillSwitch] [API] Failed to recover strategy {strategy_id}: {recover_err}"
                            )

                if recovered_count > 0:
                    # 广播恢复事件
                    await broadcast_strategy_update("*", {
                        "event": "killswitch_recovered",
                        "level": 0,
                        "level_name": "NORMAL",
                        "recovered_strategies": recovered_list,
                        "recovered_count": recovered_count,
                        "updated_by": request.updated_by,
                    })
                    logger.info(
                        f"[KillSwitch] [API] Auto-recovery complete: {recovered_count}/{len([i for i in infos if i.blocked_reason and 'KillSwitch' in i.blocked_reason])} strategies recovered"
                    )
            except Exception as recover_err:
                logger.error(f"[KillSwitch] [API] Auto-recovery failed: {recover_err}")
    except Exception as e:
        logger.error(f"[KillSwitch] [API] Failed to broadcast SSE updates: {e}")

    return new_state
