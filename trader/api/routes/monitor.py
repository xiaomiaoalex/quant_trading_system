"""
Monitor API Routes
=================
System monitoring and alerting endpoints.
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

from trader.api.models.schemas import (
    MonitorSnapshot,
    Alert,
    AlertRule,
    PositionView,
    PositionDetail,
    MonitorAlertsResponse,
)
from trader.services import MonitorService, PortfolioService, OrderService, KillSwitchService


router = APIRouter(tags=["Monitor"])


# Singleton instances with proper synchronization
_monitor_service: MonitorService | None = None
_portfolio_service: PortfolioService | None = None
_init_lock: asyncio.Lock = asyncio.Lock()  # Single lock for initialization


async def _fetch_account_balances_with_prices_from_broker() -> tuple[List[Dict[str, Any]], Dict[str, Decimal]]:
    """
    从 Broker 获取真实账户余额和实时价格。
    
    Returns:
        tuple: (balances, prices)
            - balances: List of dicts with keys: symbol, asset, quantity, free, locked
            - prices: Dict[str, Decimal] mapping symbol -> current price
    """
    try:
        from trader.adapters.broker.binance_spot_demo_broker import (
            BinanceSpotDemoBroker,
            BinanceSpotDemoBrokerConfig,
        )
        
        api_key = os.environ.get("BINANCE_API_KEY", "test_key")
        secret_key = os.environ.get("BINANCE_SECRET_KEY", "test_secret")
        binance_env = os.environ.get("BINANCE_ENV", "demo").strip().lower()
        
        if binance_env in ("testnet", "test"):
            config = BinanceSpotDemoBrokerConfig.for_testnet(
                api_key=api_key,
                secret_key=secret_key,
            )
        else:
            config = BinanceSpotDemoBrokerConfig.for_demo(
                api_key=api_key,
                secret_key=secret_key,
            )
        
        broker = BinanceSpotDemoBroker(config)
        await broker.connect()
        
        try:
            # Fetch account info which includes balances
            account = await broker._fetch_account()
            balances = []
            symbols_to_fetch = []
            
            for bal in account.get("balances", []):
                asset = str(bal.get("asset", ""))
                free = Decimal(str(bal.get("free", "0")))
                locked = Decimal(str(bal.get("locked", "0")))
                total = free + locked
                
                if total <= 0:
                    continue
                
                # Skip quote assets (USDT, USDC, etc.) - they show as "balances" but not positions
                # Only include actual trading assets (BTC, ETH, etc.)
                if asset in {"USDT", "USDC", "BUSD", "FDUSD", "USD", "TUSD", "PAX", "EUR", "GBP"}:
                    continue
                
                symbol = f"{asset}USDT"
                balances.append({
                    "symbol": symbol,
                    "asset": asset,
                    "quantity": str(total),
                    "free": str(free),
                    "locked": str(locked),
                })
                symbols_to_fetch.append(symbol)
            
            # Fetch current prices for all symbols
            prices = {}
            if symbols_to_fetch:
                prices = await broker.get_ticker_prices(symbols_to_fetch)
            
            return balances, prices
        finally:
            await broker.disconnect()
            
    except Exception as e:
        logger.warning(f"Failed to fetch account balances with prices: {e}")
        return [], {}


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
    open_orders_count: int | None = None,
    pending_orders_count: int | None = None,
    daily_pnl: str | None = None,
    daily_pnl_pct: str | None = None,
    realized_pnl: str | None = None,
    unrealized_pnl: str | None = None,
    killswitch_level: int | None = None,
    killswitch_scope: str | None = None,
) -> MonitorSnapshot:
    """
    获取系统监控快照（真聚合版本 - Task 9.2）。
    
    后端内部聚合 orders/pnl/killswitch/adapters 数据，无需前端传入。
    返回完整的系统状态快照，包括：
    - 持仓信息（从 PortfolioService 获取）
    - 订单信息（从 OrderService 聚合）
    - PnL 信息（从 PortfolioService 获取）
    - KillSwitch 状态（从 KillSwitchService 获取）
    - 适配器健康状态
    - 活跃告警列表
    
    注意：query 参数仅用于测试覆盖，生产环境应省略参数以获取真实数据。
    """
    from datetime import datetime, timezone
    
    service = await get_monitor_service()
    portfolio_svc = await get_portfolio_service()
    order_svc = OrderService()
    killswitch_svc = KillSwitchService()
    
    # 从 Broker 获取真实账户余额和实时价格用于 Position Details
    # 这显示的是交易所账户中实际持有的资产
    positions_for_exposure: List[PositionForExposure] = []
    positions_detail: List[PositionDetail] = []
    
    # 策略：
    # 1. 首先尝试从 Broker 获取真实账户余额（有当前价格，但无 avg_cost）
    # 2. 同时获取 OMS 跟踪的持仓数据（有 avg_cost 和历史 unrealized_pnl）
    # 3. 合并两者：用 Broker 余额更新 OMS 持仓的当前价格，计算实时 unrealized_pnl
    # 4. 如果 Broker 获取失败，回退到 OMS 持仓
    
    oms_positions: Dict[str, Dict[str, Any]] = {}
    try:
        raw_oms_positions: List[PositionView] = portfolio_svc.list_positions()
        for p in raw_oms_positions:
            symbol = p.instrument
            has_qty = p.qty and p.qty.strip()
            has_avg_cost = p.avg_cost and p.avg_cost.strip()
            has_mark_price = p.mark_price and p.mark_price.strip()
            has_unrealized = p.unrealized_pnl and p.unrealized_pnl.strip()
            
            qty_dec = Decimal(p.qty.strip()) if has_qty else Decimal("0")
            avg_cost_dec = Decimal(p.avg_cost.strip()) if has_avg_cost else None
            
            oms_positions[symbol.upper()] = {
                "symbol": symbol,
                "qty": qty_dec,
                "avg_cost": avg_cost_dec,
                "has_oms_data": has_qty and has_avg_cost,
            }
    except Exception as e:
        logger.warning(f"Failed to fetch OMS positions: {e}")
    
    # 从 Broker 获取真实账户余额和实时价格
    try:
        account_balances, prices = await _fetch_account_balances_with_prices_from_broker()
        for bal in account_balances:
            asset = bal.get("asset", "")
            symbol = bal.get("symbol", f"{asset}USDT")
            symbol_upper = symbol.upper()
            qty_dec = Decimal(bal.get("quantity", "0"))
            current_price = prices.get(symbol_upper, Decimal("0"))
            
            if qty_dec <= 0:
                continue
            
            # 检查 OMS 是否有该交易对的持仓数据（用于获取 avg_cost）
            oms_data = oms_positions.get(symbol_upper, {})
            has_oms_data = oms_data.get("has_oms_data", False)
            avg_cost = oms_data.get("avg_cost") if has_oms_data else None
            
            # 计算 unrealized_pnl
            unrealized_pnl = None
            if avg_cost is not None and current_price > 0:
                unrealized_pnl = qty_dec * (current_price - avg_cost)
            
            # 计算 exposure
            exposure_val = str(qty_dec * current_price) if current_price > 0 else None
            
            positions_for_exposure.append(PositionForExposure(
                quantity=float(qty_dec),
                current_price=float(current_price),
            ))
            positions_detail.append(PositionDetail(
                symbol=symbol,
                quantity=bal.get("quantity", "0"),
                avg_cost=str(avg_cost) if avg_cost is not None else None,
                current_price=str(current_price) if current_price > 0 else None,
                unrealized_pnl=str(unrealized_pnl) if unrealized_pnl is not None else None,
                exposure=exposure_val,
            ))
        
        # 如果 Broker 没有返回余额但 OMS 有持仓数据，使用 OMS 数据
        if not account_balances and oms_positions:
            for symbol_upper, oms_data in oms_positions.items():
                if not oms_data.get("has_oms_data"):
                    continue
                qty_dec = oms_data.get("qty", Decimal("0"))
                if qty_dec <= 0:
                    continue
                    
                # 获取当前价格
                current_price = prices.get(symbol_upper, Decimal("0"))
                avg_cost = oms_data.get("avg_cost")
                
                unrealized_pnl = None
                if avg_cost is not None and current_price > 0:
                    unrealized_pnl = qty_dec * (current_price - avg_cost)
                
                exposure_val = str(qty_dec * current_price) if current_price > 0 else None
                
                positions_for_exposure.append(PositionForExposure(
                    quantity=float(qty_dec),
                    current_price=float(current_price),
                ))
                positions_detail.append(PositionDetail(
                    symbol=oms_data.get("symbol", symbol_upper),
                    quantity=str(qty_dec),
                    avg_cost=str(avg_cost) if avg_cost is not None else None,
                    current_price=str(current_price) if current_price > 0 else None,
                    unrealized_pnl=str(unrealized_pnl) if unrealized_pnl is not None else None,
                    exposure=exposure_val,
                ))
    except Exception as e:
        logger.warning(f"Failed to fetch account balances: {e}")
        
        # 回退到纯 OMS 持仓数据
        if not positions_detail and oms_positions:
            for symbol_upper, oms_data in oms_positions.items():
                if not oms_data.get("has_oms_data"):
                    continue
                qty_dec = oms_data.get("qty", Decimal("0"))
                avg_cost = oms_data.get("avg_cost")
                if qty_dec <= 0:
                    continue
                    
                positions_for_exposure.append(PositionForExposure(
                    quantity=float(qty_dec),
                    current_price=0.0,
                ))
                positions_detail.append(PositionDetail(
                    symbol=oms_data.get("symbol", symbol_upper),
                    quantity=str(qty_dec),
                    avg_cost=str(avg_cost) if avg_cost is not None else None,
                    current_price=None,
                    unrealized_pnl=None,
                    exposure=None,
                ))
    
    # 如果测试传入了参数，使用测试参数；否则从服务聚合真实数据
    if open_orders_count is None or pending_orders_count is None:
        # 从 OrderService 聚合订单统计
        open_orders_count = 0
        pending_orders_count = 0
        try:
            all_orders = order_svc.list_orders(limit=10000)
            # NEW = 已提交但未成交
            # SUBMITTED = 已提交
            # PARTIALLY_FILLED = 部分成交
            open_orders_count = sum(
                1 for o in all_orders 
                if o.status in ("NEW", "SUBMITTED", "PARTIALLY_FILLED")
            )
            pending_orders_count = sum(
                1 for o in all_orders 
                if o.status in ("PENDING", "CREATED")
            )
        except Exception as e:
            logger.error(
                "Failed to aggregate order counts for monitor snapshot",
                extra={"error": str(e), "error_type": type(e).__name__}
            )
    
    # 如果测试传入了 PnL 参数，使用测试参数；否则从服务获取真实数据
    if daily_pnl is None or unrealized_pnl is None:
        daily_pnl = "0"
        daily_pnl_pct = "0"
        realized_pnl = "0"
        unrealized_pnl = "0"
        try:
            pnl = portfolio_svc.get_pnl()
            realized_pnl = pnl.realized_pnl
            unrealized_pnl = pnl.unrealized_pnl
            total_pnl = float(pnl.total_pnl) if pnl.total_pnl else 0.0
            daily_pnl = pnl.total_pnl
            
            # 计算百分比：PnL / 总敞口 * 100
            if positions_for_exposure and len(positions_for_exposure) > 0:
                total_exposure_val = sum(
                    p.quantity * p.current_price for p in positions_for_exposure
                )
                if total_exposure_val > 0:
                    daily_pnl_pct = str(round((total_pnl / total_exposure_val) * 100, 2))
                else:
                    daily_pnl_pct = "0"
            else:
                daily_pnl_pct = "0"  # 无敞口时无法计算百分比
        except Exception as e:
            logger.error(
                "Failed to fetch PnL for monitor snapshot",
                extra={"error": str(e), "error_type": type(e).__name__}
            )
    
    # 如果测试传入了 killswitch 参数，使用测试参数；否则从服务获取真实状态
    if killswitch_level is None or killswitch_scope is None:
        killswitch_level = 0
        killswitch_scope = "GLOBAL"
        try:
            ks_state = killswitch_svc.get_state("GLOBAL")
            killswitch_level = ks_state.level
            killswitch_scope = ks_state.scope
        except Exception as e:
            logger.error(
                "Failed to fetch killswitch state for monitor snapshot",
                extra={"error": str(e), "error_type": type(e).__name__}
            )
    
    # 从 BrokerService 获取适配器健康状态
    try:
        from trader.services.broker import BrokerService
        broker_service = BrokerService()
        brokers = broker_service.list_brokers()
        for broker in brokers:
            # 每个注册的 broker 都被视为一个 adapter
            # 健康状态从 broker_service 获取
            try:
                status = broker_service.get_status(broker.account_id)
                service.update_adapter_health(
                    adapter_name=f"broker:{broker.account_id}",
                    status="HEALTHY" if status and status.connected else "DOWN",
                    last_heartbeat_ts_ms=status.last_heartbeat_ts_ms if status else None,
                    error_count=1 if status and status.last_error else 0,
                    message=status.last_error if status else "No status available",
                )
            except Exception as broker_err:
                # 单个 broker 状态获取失败不影响其他 broker
                logger.warning(
                    f"Failed to get status for broker {broker.account_id}",
                    extra={"error": str(broker_err)}
                )
                service.update_adapter_health(
                    adapter_name=f"broker:{broker.account_id}",
                    status="DOWN",
                    last_heartbeat_ts_ms=None,
                    error_count=1,
                    message=f"Status check failed: {broker_err}",
                )
    except Exception as e:
        # 降低日志级别：无 broker 注册是正常情况
        logger.warning(
            "No brokers registered or failed to fetch adapter health for monitor snapshot",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
    
    # Task 19: 从 OMSCallbackHandler 获取可观测性指标
    try:
        from trader.api.routes.strategies import get_oms_metrics
        oms_metrics = get_oms_metrics()
        if oms_metrics:
            snapshot = service.get_snapshot(
                positions=positions_for_exposure,
                open_orders_count=open_orders_count or 0,
                pending_orders_count=pending_orders_count or 0,
                daily_pnl=daily_pnl or "0",
                daily_pnl_pct=daily_pnl_pct or "0",
                realized_pnl=realized_pnl or "0",
                unrealized_pnl=unrealized_pnl or "0",
                killswitch_level=killswitch_level or 0,
                killswitch_scope=killswitch_scope or "GLOBAL",
            )
            # Task 19: 填充 OMS 可观测性指标到快照
            snapshot.order_submit_ok = oms_metrics.get("order_submit_ok", 0)
            snapshot.order_submit_reject = oms_metrics.get("order_submit_reject", 0)
            snapshot.order_submit_error = oms_metrics.get("order_submit_error", 0)
            snapshot.reject_reason_counts = oms_metrics.get("reject_reason_counts", {})
            snapshot.fill_latency_ms_avg = oms_metrics.get("fill_latency_ms_avg")
            snapshot.fill_latency_count = oms_metrics.get("fill_latency_count", 0)
            snapshot.cl_ord_id_dedup_hits = oms_metrics.get("cl_ord_id_dedup_hits", 0)
            snapshot.exec_dedup_hits = oms_metrics.get("exec_dedup_hits", 0)
        else:
            snapshot = service.get_snapshot(
                positions=positions_for_exposure,
                open_orders_count=open_orders_count or 0,
                pending_orders_count=pending_orders_count or 0,
                daily_pnl=daily_pnl or "0",
                daily_pnl_pct=daily_pnl_pct or "0",
                realized_pnl=realized_pnl or "0",
                unrealized_pnl=unrealized_pnl or "0",
                killswitch_level=killswitch_level or 0,
                killswitch_scope=killswitch_scope or "GLOBAL",
            )
    except Exception as e:
        logger.error(
            "Failed to fetch OMS metrics for monitor snapshot",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
        snapshot = service.get_snapshot(
            positions=positions_for_exposure,
            open_orders_count=open_orders_count or 0,
            pending_orders_count=pending_orders_count or 0,
            daily_pnl=daily_pnl or "0",
            daily_pnl_pct=daily_pnl_pct or "0",
            realized_pnl=realized_pnl or "0",
            unrealized_pnl=unrealized_pnl or "0",
            killswitch_level=killswitch_level or 0,
            killswitch_scope=killswitch_scope or "GLOBAL",
        )
    
    # 添加元信息 — timestamp 与 freshness 同步，前端 stale 检查依赖 timestamp
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    snapshot.timestamp = now_iso
    snapshot.freshness = now_iso
    snapshot.snapshot_source = "aggregated"

    # 添加详细持仓信息
    snapshot.positions = positions_detail

    return snapshot


@router.get("/v1/monitor/alerts", response_model=MonitorAlertsResponse)
async def get_active_alerts() -> MonitorAlertsResponse:
    """
    获取当前活跃告警列表。

    返回所有未过冷却期的告警，包装在标准响应对象中。
    """
    service = await get_monitor_service()
    alerts = service.get_active_alerts()
    return MonitorAlertsResponse(alerts=alerts, total_count=len(alerts))


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