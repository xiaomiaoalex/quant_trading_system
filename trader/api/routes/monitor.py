"""
Monitor API Routes
==================
System monitoring and alerting endpoints.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

from trader.api.models.schemas import MonitorSnapshot, Alert, AlertRule, PositionView
from trader.services import MonitorService, PortfolioService


router = APIRouter(tags=["Monitor"])


# Singleton instances with proper synchronization
_monitor_service: MonitorService | None = None
_portfolio_service: PortfolioService | None = None
_init_lock: asyncio.Lock = asyncio.Lock()  # Single lock for initialization


@dataclass
class PositionForExposure:
    """
    持仓数据用于计算敞口。
    
    统一接口：quantity（数量）和 current_price（当前价格）
    """
    quantity: float
    current_price: float


async def get_monitor_service() -> MonitorService:
    """
    获取或创建 MonitorService 单例（async-safe）
    
    FastAPI routes are all async, so we only need one async-safe initialization.
    The asyncio.Lock ensures only one coroutine initializes the singleton.
    """
    global _monitor_service
    
    if _monitor_service is not None:
        return _monitor_service
    
    async with _init_lock:
        # Double-check after acquiring lock
        if _monitor_service is None:
            _monitor_service = MonitorService()
    
    return _monitor_service


async def get_portfolio_service() -> PortfolioService:
    """
    获取或创建 PortfolioService 单例（async-safe）
    
    使用与 MonitorService 相同的锁机制保证线程安全。
    虽然 PortfolioService 初始化本身不需要 async 操作，但为了保证
    singleton 的线程安全，需要使用 async 锁来与其他 async 路由协调。
    """
    global _portfolio_service
    
    if _portfolio_service is not None:
        return _portfolio_service
    
    async with _init_lock:
        # Double-check after acquiring lock
        if _portfolio_service is None:
            _portfolio_service = PortfolioService()
    
    return _portfolio_service


@router.get("/v1/monitor/snapshot", response_model=MonitorSnapshot)
async def get_monitor_snapshot(
    open_orders_count: int = Query(0, description="未成交订单数"),
    pending_orders_count: int = Query(0, description="待处理订单数"),
    daily_pnl: str = Query("0", description="当日盈亏"),
    daily_pnl_pct: str = Query("0", description="当日盈亏百分比"),
    realized_pnl: str = Query("0", description="已实现盈亏"),
    unrealized_pnl: str = Query("0", description="未实现盈亏"),
    killswitch_level: int = Query(0, ge=0, le=3, description="KillSwitch级别"),
    killswitch_scope: str = Query("GLOBAL", description="KillSwitch范围"),
) -> MonitorSnapshot:
    """
    获取系统监控快照。
    
    返回完整的系统状态快照，包括：
    - 持仓信息（从 PortfolioService 获取）
    - 订单信息
    - PnL信息
    - KillSwitch状态
    - 适配器健康状态
    - 活跃告警列表
    """
    service = await get_monitor_service()
    portfolio_svc = await get_portfolio_service()
    
    # 从 PortfolioService 获取持仓列表
    # Fail-Closed: 获取失败时使用空列表
    positions_for_exposure: List[PositionForExposure] = []
    try:
        raw_positions: List[PositionView] = portfolio_svc.list_positions()
        positions_for_exposure = [
            PositionForExposure(
                quantity=float(p.qty) if p.qty and p.qty.strip() else 0.0,
                current_price=float(p.mark_price) if p.mark_price and p.mark_price.strip() else 0.0,
            )
            for p in raw_positions
        ]
    except (ConnectionError, TimeoutError) as e:
        # 基础设施错误 - 连接失败可能是临时性的
        logger.error(
            "Failed to fetch positions for monitor snapshot - connection error, using empty list",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
    except Exception as e:
        # 其他未知错误也需要记录，但不影响快照返回
        logger.error(
            "Unexpected error fetching positions for monitor snapshot, using empty list",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
    
    return service.get_snapshot(
        positions=positions_for_exposure,
        open_orders_count=open_orders_count,
        pending_orders_count=pending_orders_count,
        daily_pnl=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        killswitch_level=killswitch_level,
        killswitch_scope=killswitch_scope,
    )


@router.get("/v1/monitor/alerts", response_model=list[Alert])
async def get_active_alerts() -> list[Alert]:
    """
    获取当前活跃告警列表。
    
    返回所有未过冷却期的告警。
    """
    service = await get_monitor_service()
    return service.get_active_alerts()


@router.post("/v1/monitor/rules", response_model=AlertRule)
async def add_alert_rule(rule: AlertRule) -> AlertRule:
    """
    添加或更新告警规则。
    
    如果规则名已存在，则更新现有规则。
    """
    service = await get_monitor_service()
    service.add_alert_rule(rule)
    return rule


@router.delete("/v1/monitor/rules/{rule_name}")
async def remove_alert_rule(rule_name: str) -> dict:
    """
    移除告警规则。
    """
    service = await get_monitor_service()
    removed = service.remove_alert_rule(rule_name)
    return {"ok": removed, "rule_name": rule_name}


@router.post("/v1/monitor/alerts/{rule_name}/clear")
async def clear_alert(rule_name: str) -> dict:
    """
    清除指定告警规则的触发状态。
    """
    service = await get_monitor_service()
    cleared = service.clear_alert(rule_name)
    return {"ok": cleared, "rule_name": rule_name}


@router.post("/v1/monitor/alerts/clear-all")
async def clear_all_alerts() -> dict:
    """
    清除所有告警触发状态。
    """
    service = await get_monitor_service()
    service.clear_all_alerts()
    return {"ok": True}