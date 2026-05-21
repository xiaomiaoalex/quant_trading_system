"""
NAV Service — 净资产价值快照计算与广播（Stage 6）
=================================================
在每次成交后由 oms_callback 以 asyncio.create_task 调用。
best-effort：任何异常只 warning，不影响主流程。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from trader.storage.in_memory import ControlPlaneInMemoryStorage

logger = logging.getLogger(__name__)

# 初始资金占位（后续可从 deployment.config 读取 initial_capital）
_DEFAULT_INITIAL_CAPITAL = 100_000.0


async def record_nav_snapshot(
    strategy_id: str,
    storage: "ControlPlaneInMemoryStorage",
    deployment_id: Optional[str] = None,
) -> None:
    """
    计算 strategy_id 的当前 NAV 并写入内存存储、PostgreSQL（best-effort）
    和 SSE 广播 channel=nav:{deployment_id}。

    Args:
        strategy_id: 触发成交的策略 ID
        storage: 控制面内存存储（用于查询 positions 和写 nav_series）
        deployment_id: 部署 ID；None 时优先从 positions 字段推断，兜底用 strategy_id
    """
    try:
        from trader.core.domain.models.nav import NAVPoint

        positions = storage.list_positions(strategy_id=strategy_id)

        realized_pnl = sum(float(p.get("realized_pnl") or 0) for p in positions)

        # 基于实时 mark_price 与持仓成本重新计算 unrealized_pnl，
        # 确保 equity = cash + market_value 自洽
        market_value = 0.0
        total_cost = 0.0
        for p in positions:
            qty = float(p.get("qty") or 0)
            mark_price = float(p.get("mark_price") or 0)
            avg_cost = float(p.get("avg_cost") or 0)
            market_value += qty * mark_price
            total_cost += qty * avg_cost

        unrealized_pnl = market_value - total_cost
        total_pnl = realized_pnl + unrealized_pnl
        equity = _DEFAULT_INITIAL_CAPITAL + total_pnl
        cash = equity - market_value

        # 推断 deployment_id
        eff_deployment_id = deployment_id or next(
            (p.get("deployment_id") for p in positions if p.get("deployment_id")),
            strategy_id,
        )

        nav = NAVPoint(
            deployment_id=eff_deployment_id,
            strategy_id=strategy_id,
            timestamp_ms=int(time.time() * 1000),
            equity=equity,
            cash=cash,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            total_pnl=total_pnl,
        )

        # 1. 写内存（立即可查）
        storage.append_nav_point(eff_deployment_id, nav.to_dict())

        # 2. 写 PostgreSQL（best-effort）
        try:
            from trader.storage.nav_store import append_nav_point_pg

            await append_nav_point_pg(nav)
        except Exception as pg_exc:
            logger.warning("NAV PG write failed strategy=%s: %s", strategy_id, pg_exc)

        # 3. SSE 广播（双频道：eff_deployment_id + strategy_id，确保前端无论用哪个 ID 订阅都能收到）
        try:
            from trader.api.routes.sse import get_sse_manager

            sse_mgr = get_sse_manager()
            payload = {
                "deployment_id": eff_deployment_id,
                "strategy_id": strategy_id,
                "nav_point": {
                    "timestamp_ms": nav.timestamp_ms,
                    "equity": nav.equity,
                    "cash": nav.cash,
                    "unrealized_pnl": nav.unrealized_pnl,
                    "realized_pnl": nav.realized_pnl,
                    "total_pnl": nav.total_pnl,
                },
            }
            await sse_mgr.broadcast(f"nav:{eff_deployment_id}", "nav_update", payload)
            if eff_deployment_id != strategy_id:
                await sse_mgr.broadcast(f"nav:{strategy_id}", "nav_update", payload)
        except Exception as sse_exc:
            logger.warning("NAV SSE broadcast failed strategy=%s: %s", strategy_id, sse_exc)

    except Exception as exc:
        logger.warning("record_nav_snapshot failed strategy=%s: %s", strategy_id, exc)
