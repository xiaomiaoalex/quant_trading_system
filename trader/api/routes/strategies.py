"""
 Strategy API Routes
==================
Strategy registry, version management, and runner control endpoints.
"""
import asyncio
import hashlib
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from trader.api.models.schemas import (
    Strategy, StrategyRegisterRequest,
    StrategyVersion, StrategyVersionCreateRequest,
    VersionedConfig, VersionedConfigUpsertRequest,
    StrategyCodeVersion,
    StrategyCodeCreateRequest,
    StrategyCodeDebugRequest,
    StrategyCodeDebugResponse,
)
from trader.api.env_config import get_binance_recv_window
from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    StrategyResourceLimits,
)
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal
from trader.storage.in_memory import get_storage
from trader.services import StrategyService
from trader.services.strategy_runner import (
    StrategyRunner,
    StrategyRuntimeInfo,
)

router = APIRouter(tags=["Strategies"])
logger = logging.getLogger(__name__)


# 全局策略执行器实例（单例）
# 使用 threading.Lock 实现线程安全的懒加载初始化
# 注意：每个策略在独立的 asyncio.Task 中运行，实现异常隔离。
_strategy_runner_instance: StrategyRunner | None = None
_runner_lock: threading.Lock = threading.Lock()
_live_broker = None
_live_broker_lock = asyncio.Lock()
_last_order_results: Dict[str, Dict[str, Any]] = {}


def _create_event_callback():
    """
    创建 StrategyRunner 事件回调：
    - 将策略信号/下单事件写入控制面事件流，便于 /v1/events 可观测
    """
    storage = get_storage()

    def _event_callback(strategy_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        storage.append_event(
            {
                "stream_key": f"strategy.{strategy_id}",
                "event_type": event_type,
                "trace_id": str(uuid.uuid4()),
                "ts_ms": ts_ms,
                "payload": {
                    "strategy_id": strategy_id,
                    **payload,
                },
            }
        )

    return _event_callback


def _is_live_order_strategy_allowed(strategy_id: str) -> bool:
    raw = os.environ.get("LIVE_ORDER_STRATEGIES", "fire_test")
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    return strategy_id in allowed


def _build_binance_broker_config():
    from trader.adapters.broker.binance_spot_demo_broker import BinanceSpotDemoBrokerConfig

    api_key = os.environ.get("BINANCE_API_KEY")
    secret_key = os.environ.get("BINANCE_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("缺少 BINANCE_API_KEY 或 BINANCE_SECRET_KEY，无法执行真实下单")

    recv_window = get_binance_recv_window()
    mode = os.environ.get("BINANCE_BROKER_ENV", "demo").strip().lower()
    if mode == "testnet":
        return BinanceSpotDemoBrokerConfig.for_testnet(
            api_key=api_key,
            secret_key=secret_key,
            recv_window=recv_window,
        )
    return BinanceSpotDemoBrokerConfig.for_demo(
        api_key=api_key,
        secret_key=secret_key,
        recv_window=recv_window,
    )


async def _get_live_broker():
    from trader.adapters.broker.binance_spot_demo_broker import BinanceSpotDemoBroker

    global _live_broker
    async with _live_broker_lock:
        if _live_broker is None:
            _live_broker = BinanceSpotDemoBroker(_build_binance_broker_config())
            await _live_broker.connect()
            return _live_broker

        if not await _live_broker.is_connected():
            await _live_broker.connect()
        return _live_broker


async def _submit_live_order(strategy_id: str, signal: Signal) -> Dict[str, Any] | None:
    """
    策略信号 -> 真实下单桥接：
    - 默认仅允许 LIVE_ORDER_STRATEGIES（默认 fire_test）
    - 成功后写入控制面 order/execution 视图，便于 API 查询与对账
    """
    if not _is_live_order_strategy_allowed(strategy_id):
        return None

    if signal.signal_type.value == "NONE":
        return None

    if signal.quantity <= 0:
        raise ValueError(f"非法下单数量: {signal.quantity}")

    broker = await _get_live_broker()
    client_order_id = f"{strategy_id}_{signal.signal_id.replace('-', '')[:18]}"
    order = await broker.place_order(
        symbol=signal.symbol,
        side=signal.get_order_side(),
        order_type=OrderType.MARKET,
        quantity=signal.quantity,
        client_order_id=client_order_id,
    )

    storage = get_storage()
    storage.create_order(
        {
            "cl_ord_id": order.client_order_id or client_order_id,
            "trace_id": signal.signal_id,
            "account_id": os.environ.get("TRADING_ACCOUNT_ID", "binance_spot_demo"),
            "strategy_id": strategy_id,
            "deployment_id": None,
            "venue": broker.broker_name.upper(),
            "instrument": order.symbol,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "qty": str(order.quantity),
            "limit_price": None,
            "tif": "GTC",
            "status": order.status.value,
            "broker_order_id": order.broker_order_id,
            "filled_qty": str(order.filled_quantity),
            "avg_price": str(order.average_price) if order.average_price is not None else None,
        }
    )

    if order.filled_quantity > Decimal("0"):
        storage.create_execution(
            {
                "cl_ord_id": order.client_order_id or client_order_id,
                "exec_id": order.broker_order_id or str(uuid.uuid4()),
                "ts_ms": int(order.created_at.timestamp() * 1000),
                "fill_qty": str(order.filled_quantity),
                "fill_price": str(order.average_price),
            }
        )

    result = {
        "client_order_id": order.client_order_id or client_order_id,
        "broker_order_id": order.broker_order_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "status": order.status.value,
        "filled_quantity": str(order.filled_quantity),
        "avg_price": str(order.average_price),
    }
    _last_order_results[strategy_id] = result
    return result


async def shutdown_strategy_runtime() -> None:
    """
    应用关闭时清理策略运行时资源，避免连接泄漏。
    """
    global _strategy_runner_instance, _live_broker

    runner = _strategy_runner_instance
    _strategy_runner_instance = None
    if runner is not None:
        await runner.shutdown()

    async with _live_broker_lock:
        broker = _live_broker
        _live_broker = None
        if broker is not None:
            await broker.disconnect()

    _last_order_results.clear()


def get_strategy_runner() -> StrategyRunner:
    """
    获取全局策略执行器实例。
    
    返回应用级单例，确保所有策略在同一个执行器中运行。
    使用 threading.Lock 保证在多线程环境下的线程安全初始化。
    
    注意：
    - 策略之间共享执行器状态
    - 每个策略运行在独立的 asyncio.Task 中
    - 异常隔离：单策略崩溃不会影响其他策略
    - 内部状态由 StrategyRunner 自己管理（非 asyncio.Lock，因为
    - get_strategy_runner 本身是同步函数，无法使用 asyncio.Lock）
    """
    global _strategy_runner_instance
    if _strategy_runner_instance is None:
        with _runner_lock:
            # 双重检查锁定模式
            if _strategy_runner_instance is None:
                _strategy_runner_instance = StrategyRunner(
                    oms_callback=_submit_live_order,
                    event_callback=_create_event_callback()
                )
    return _strategy_runner_instance


def _signal_to_dict(signal) -> Dict:
    """Convert Signal model to serializable dict for debug endpoint."""
    return {
        "strategy_name": signal.strategy_name,
        "signal_type": signal.signal_type.value if hasattr(signal.signal_type, "value") else str(signal.signal_type),
        "symbol": signal.symbol,
        "price": float(signal.price) if signal.price is not None else None,
        "quantity": float(signal.quantity) if signal.quantity is not None else None,
        "confidence": float(signal.confidence) if signal.confidence is not None else None,
        "reason": signal.reason,
    }


def _build_debug_market_data(raw: Dict, fallback_symbol: str = "BTCUSDT") -> MarketData:
    """Build MarketData from payload dict (debug use)."""
    price = Decimal(str(raw.get("price", "0")))
    return MarketData(
        symbol=str(raw.get("symbol", fallback_symbol)),
        data_type=MarketDataType.KLINE,
        price=price,
        volume=Decimal(str(raw.get("volume", "1"))),
        kline_open=Decimal(str(raw.get("open", price))),
        kline_high=Decimal(str(raw.get("high", price))),
        kline_low=Decimal(str(raw.get("low", price))),
        kline_close=Decimal(str(raw.get("close", price))),
        kline_interval=str(raw.get("interval", "1m")),
        metadata=dict(raw.get("metadata", {})),
    )


@router.get("/v1/strategies/registry", response_model=List[Strategy])
async def list_strategies():
    """
    List registered strategies.

    Returns a list of all registered strategies.
    """
    service = StrategyService()
    return service.list_strategies()


@router.post("/v1/strategies/registry", response_model=Strategy, status_code=201)
async def register_strategy(request: StrategyRegisterRequest):
    """
    Register a new strategy.

    Registers a strategy with metadata and entrypoint.
    """
    service = StrategyService()
    return service.register_strategy(request)


@router.get("/v1/strategies/registry/{strategy_id}", response_model=Strategy)
async def get_strategy(strategy_id: str = Path(..., description="Strategy ID")):
    """
    Get strategy metadata.

    Returns the strategy metadata by ID.
    """
    service = StrategyService()
    strategy = service.get_strategy(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    return strategy


@router.post("/v1/strategies/code", response_model=StrategyCodeVersion, status_code=201)
async def create_strategy_code(request: StrategyCodeCreateRequest):
    """
    Create or update strategy code (new code version).

    Supports auto registry creation so frontend can complete
    code edit -> register -> load -> run chain in one flow.
    """
    storage = get_storage()
    service = StrategyService()

    strategy = service.get_strategy(request.strategy_id)
    if strategy is None:
        if not request.register_if_missing:
            raise HTTPException(status_code=404, detail=f"Strategy {request.strategy_id} not found")
        service.register_strategy(
            StrategyRegisterRequest(
                strategy_id=request.strategy_id,
                name=request.name or request.strategy_id,
                description=request.description,
                entrypoint=f"dynamic:{request.strategy_id}",
                language="python",
            )
        )

    entry = storage.create_strategy_code(
        request.strategy_id,
        {
            "code": request.code,
            "created_by": request.created_by,
            "notes": request.notes,
        },
    )
    return StrategyCodeVersion(**entry)


@router.get("/v1/strategies/{strategy_id}/code/latest", response_model=StrategyCodeVersion)
async def get_latest_strategy_code(strategy_id: str = Path(..., description="Strategy ID")):
    """Get latest strategy code version."""
    storage = get_storage()
    entry = storage.get_latest_strategy_code(strategy_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No code found for strategy {strategy_id}")
    return StrategyCodeVersion(**entry)


@router.get("/v1/strategies/{strategy_id}/code/{code_version}", response_model=StrategyCodeVersion)
async def get_strategy_code_version(
    strategy_id: str = Path(..., description="Strategy ID"),
    code_version: int = Path(..., ge=1, description="Code version"),
):
    """Get strategy code by version."""
    storage = get_storage()
    entry = storage.get_strategy_code_version(strategy_id, code_version)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Code version {code_version} not found")
    return StrategyCodeVersion(**entry)


@router.post("/v1/strategies/code/debug", response_model=StrategyCodeDebugResponse)
async def debug_strategy_code(request: StrategyCodeDebugRequest):
    """
    Debug strategy code with compile + protocol + dry-run tick checks.
    """
    runner = get_strategy_runner()
    debug_id = request.strategy_id or f"debug_{uuid.uuid4().hex[:10]}"
    checksum = hashlib.sha256(request.code.encode("utf-8")).hexdigest()
    loaded = False
    errors: List[str] = []
    warnings: List[str] = []
    signals: List[Dict] = []
    validation_status: Optional[str] = None

    try:
        info = await runner.load_strategy_from_code(
            strategy_id=debug_id,
            version=f"debug-{int(time.time())}",
            code=request.code,
            config=request.config,
        )
        loaded = True
        await runner.start(debug_id)
        plugin = runner.get_plugin(debug_id)
        if plugin is not None and hasattr(plugin, "validate"):
            try:
                result = plugin.validate()
                validation_status = result.status.value if hasattr(result.status, "value") else str(result.status)
                warnings = list(getattr(result, "warnings", []) or [])
                if hasattr(result, "errors") and result.errors:
                    errors.extend([getattr(e, "message", str(e)) for e in result.errors])
            except Exception as e:
                errors.append(f"validate() failed: {e}")

        samples = request.sample_market_data or [
            {"symbol": "BTCUSDT", "price": 50000, "open": 49900, "high": 50100, "low": 49800, "close": 50000},
            {"symbol": "BTCUSDT", "price": 50200, "open": 50000, "high": 50300, "low": 49950, "close": 50200},
            {"symbol": "BTCUSDT", "price": 49800, "open": 50200, "high": 50350, "low": 49700, "close": 49800},
        ]
        for raw in samples:
            md = _build_debug_market_data(raw)
            signal = await runner.tick(debug_id, md)
            if signal is not None:
                signals.append(_signal_to_dict(signal))

        return StrategyCodeDebugResponse(
            ok=len(errors) == 0,
            syntax_ok=True,
            protocol_ok=True,
            validation_status=validation_status,
            checksum=checksum,
            signals=signals,
            errors=errors,
            warnings=warnings,
        )
    except SyntaxError as e:
        return StrategyCodeDebugResponse(
            ok=False,
            syntax_ok=False,
            protocol_ok=False,
            checksum=checksum,
            errors=[f"SyntaxError: {e}"],
            warnings=warnings,
        )
    except TypeError as e:
        return StrategyCodeDebugResponse(
            ok=False,
            syntax_ok=True,
            protocol_ok=False,
            checksum=checksum,
            errors=[f"ProtocolError: {e}"],
            warnings=warnings,
        )
    except Exception as e:
        return StrategyCodeDebugResponse(
            ok=False,
            syntax_ok=False,
            protocol_ok=False,
            checksum=checksum,
            errors=[str(e)],
            warnings=warnings,
        )
    finally:
        if loaded:
            try:
                await runner.stop(debug_id)
            except Exception as e:
                logger.warning("Debug strategy stop failed for %s: %s", debug_id, e)
            try:
                await runner.unload_strategy(debug_id)
            except Exception as e:
                logger.warning("Debug strategy unload failed for %s: %s", debug_id, e)


@router.get("/v1/strategies/{strategy_id}/versions", response_model=List[StrategyVersion])
async def list_strategy_versions(strategy_id: str = Path(..., description="Strategy ID")):
    """
    List strategy versions.

    Returns all versions of a strategy.
    """
    service = StrategyService()
    return service.list_versions(strategy_id)


@router.post("/v1/strategies/{strategy_id}/versions", response_model=StrategyVersion, status_code=201)
async def create_strategy_version(
    strategy_id: str = Path(..., description="Strategy ID"),
    request: StrategyVersionCreateRequest | None = None,
):
    """
    Create a new strategy version.

    Creates a new version with code reference and parameter schema.
    """
    if request is None:
        request = StrategyVersionCreateRequest(
            version=1,
            code_ref="git:initial",
            param_schema={}
        )
    service = StrategyService()
    return service.create_version(strategy_id, request)


@router.get("/v1/strategies/{strategy_id}/versions/{version}", response_model=StrategyVersion)
async def get_strategy_version(
    strategy_id: str = Path(..., description="Strategy ID"),
    version: int = Path(..., description="Version number"),
):
    """
    Get strategy version details.

    Returns a specific version of a strategy.
    """
    service = StrategyService()
    version_obj = service.get_version(strategy_id, version)
    if not version_obj:
        raise HTTPException(
            status_code=404,
            detail=f"Version {version} of strategy {strategy_id} not found"
        )
    return version_obj


@router.get("/v1/strategies/{strategy_id}/params", response_model=Optional[VersionedConfig])
async def get_strategy_params(strategy_id: str = Path(..., description="Strategy ID")):
    """
    Get latest strategy params.

    Returns the latest parameter configuration for a strategy.
    """
    service = StrategyService()
    return service.get_latest_params(strategy_id)


@router.post("/v1/strategies/{strategy_id}/params", response_model=VersionedConfig)
async def create_strategy_params(
    strategy_id: str = Path(..., description="Strategy ID"),
    request: VersionedConfigUpsertRequest | None = None,
):
    """
    Create new strategy params version.

    Creates a new version of strategy parameters.
    """
    if request is None:
        request = VersionedConfigUpsertRequest(
            scope=strategy_id,
            config={},
            created_by="system"
        )
    service = StrategyService()
    return service.create_params(strategy_id, request)


class UpdateStrategyParamsRequest(BaseModel):
    """更新策略参数请求"""

    config: Dict = Field(..., description="新的配置参数（部分更新，支持增量更新）")
    validate_only: bool = Field(default=False, description="仅验证参数，不实际更新")


class UpdateStrategyParamsResponse(BaseModel):
    """更新策略参数响应"""

    success: bool
    strategy_id: str
    updated_config: Optional[Dict] = None
    validation_result: Optional[Dict] = None
    error: Optional[str] = None


@router.put("/v1/strategies/{strategy_id}/params", response_model=UpdateStrategyParamsResponse)
async def update_strategy_params(
    strategy_id: str = Path(..., description="Strategy ID"),
    request: UpdateStrategyParamsRequest | None = None,
):
    """
    Update strategy parameters.

    Dynamically updates strategy parameters without requiring restart.
    Supports partial updates (incremental updates).
    """
    if request is None:
        raise HTTPException(status_code=400, detail="Request body is required")

    runner = get_strategy_runner()

    try:
        # 获取策略当前状态
        info = runner.get_status(strategy_id)
        if info is None:
            raise HTTPException(
                status_code=404,
                detail=f"Strategy {strategy_id} not loaded"
            )

        # 如果仅验证模式
        if request.validate_only:
            # 使用 runner 的公共方法验证配置
            try:
                validation_result = await runner.validate_strategy_config(strategy_id, request.config)
                return UpdateStrategyParamsResponse(
                    success=validation_result.is_valid,
                    strategy_id=strategy_id,
                    validation_result={
                        "status": validation_result.status.value,
                        "errors": [{"field": e.field, "message": e.message, "code": e.code}
                                   for e in validation_result.errors],
                        "warnings": list(validation_result.warnings),
                    } if validation_result else None,
                )
            except Exception as e:
                return UpdateStrategyParamsResponse(
                    success=False,
                    strategy_id=strategy_id,
                    error=f"验证失败: {e}",
                )

        # 实际更新参数
        info = await runner.update_strategy_config(strategy_id, request.config)
        return UpdateStrategyParamsResponse(
            success=True,
            strategy_id=strategy_id,
            updated_config=info.config,
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update strategy params: {e}")


# ============================================================================
# Strategy Runner 端点
# ============================================================================


class LoadStrategyRequest(BaseModel):
    """加载策略请求"""

    module_path: Optional[str] = Field(default=None, description="策略模块路径，如 'trader.strategies.ema_cross_btc'")
    code: Optional[str] = Field(default=None, description="可选：直接加载代码字符串")
    code_version: Optional[int] = Field(default=None, ge=1, description="可选：加载已保存代码版本")
    version: str = Field(default="v1", description="策略版本")
    config: Dict = Field(default_factory=dict, description="策略配置参数")
    # 资源限制
    max_position_size: Optional[float] = Field(default=1.0, description="最大持仓数量")
    max_daily_loss: Optional[float] = Field(default=100.0, description="最大日亏损金额")
    max_orders_per_minute: Optional[int] = Field(default=10, description="最大每分钟订单数")
    timeout_seconds: Optional[float] = Field(default=5.0, description="策略执行超时时间")


class StrategyStatusResponse(BaseModel):
    """策略状态响应"""

    strategy_id: str
    version: str
    status: str
    loaded_at: Optional[str] = None
    started_at: Optional[str] = None
    last_tick_at: Optional[str] = None
    tick_count: int = 0
    signal_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    config: Dict = Field(default_factory=dict)
    blocked_reason: Optional[str] = None


class StrategyTickRequest(BaseModel):
    """手动驱动策略 Tick 请求"""

    symbol: str = Field(default="BTCUSDT", description="交易对")
    price: Decimal = Field(..., description="当前价格")
    volume: Decimal = Field(default=Decimal("0"), description="成交量")
    data_type: str = Field(default="TICKER", description="MarketDataType 名称")
    timestamp: Optional[datetime] = Field(default=None, description="数据时间（UTC）；为空则用当前时间")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="扩展字段")


class StrategyTickResponse(BaseModel):
    """手动驱动策略 Tick 响应"""

    strategy_id: str
    signal_generated: bool
    signal_type: Optional[str] = None
    signal_reason: Optional[str] = None
    order_submitted: bool = False
    order_result: Optional[Dict[str, Any]] = None


def _info_to_response(info: StrategyRuntimeInfo) -> StrategyStatusResponse:
    """转换运行时信息为响应模型"""
    return StrategyStatusResponse(
        strategy_id=info.strategy_id,
        version=info.version,
        status=info.status.value,
        loaded_at=info.loaded_at.isoformat() if info.loaded_at else None,
        started_at=info.started_at.isoformat() if info.started_at else None,
        last_tick_at=info.last_tick_at.isoformat() if info.last_tick_at else None,
        tick_count=info.tick_count,
        signal_count=info.signal_count,
        error_count=info.error_count,
        last_error=info.last_error,
        config=info.config,
        blocked_reason=info.blocked_reason,
    )


@router.post(
    "/v1/strategies/{strategy_id}/load",
    response_model=StrategyStatusResponse,
    status_code=200,
)
async def load_strategy(
    strategy_id: str = Path(..., description="Strategy ID"),
    request: LoadStrategyRequest | None = None,
):
    """
    Load strategy code.

    Dynamically loads strategy code from module path.
    Supports resource limits configuration.
    """
    if request is None:
        raise HTTPException(status_code=400, detail="Request body is required")

    runner = get_strategy_runner()

    # 构建资源限制配置
    resource_limits = None
    if any([
        request.max_position_size is not None,
        request.max_daily_loss is not None,
        request.max_orders_per_minute is not None,
        request.timeout_seconds is not None,
    ]):
        resource_limits = StrategyResourceLimits(
            max_position_size=Decimal(str(request.max_position_size or 1.0)),
            max_daily_loss=Decimal(str(request.max_daily_loss or 100.0)),
            max_orders_per_minute=request.max_orders_per_minute or 10,
            timeout_seconds=request.timeout_seconds or 5.0,
        )

    try:
        storage = get_storage()
        module_path = request.module_path

        if request.code:
            info = await runner.load_strategy_from_code(
                strategy_id=strategy_id,
                version=request.version,
                code=request.code,
                config=request.config,
                resource_limits=resource_limits,
            )
            return _info_to_response(info)

        code_entry = None
        if request.code_version is not None:
            code_entry = storage.get_strategy_code_version(strategy_id, request.code_version)
            if code_entry is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Code version {request.code_version} of strategy {strategy_id} not found",
                )

        if code_entry is None and (module_path is None or module_path.startswith("dynamic:")):
            code_entry = storage.get_latest_strategy_code(strategy_id)

        if code_entry is not None:
            info = await runner.load_strategy_from_code(
                strategy_id=strategy_id,
                version=request.version,
                code=code_entry["code"],
                config=request.config,
                resource_limits=resource_limits,
            )
            return _info_to_response(info)

        if module_path is None:
            strategy = StrategyService().get_strategy(strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
            module_path = strategy.entrypoint

        if module_path.startswith("dynamic:"):
            raise HTTPException(
                status_code=400,
                detail="Strategy uses dynamic code entrypoint, but no code version was found",
            )

        info = await runner.load_strategy(
            strategy_id=strategy_id,
            version=request.version,
            module_path=module_path,
            config=request.config,
            resource_limits=resource_limits,
        )
        return _info_to_response(info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TypeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load strategy: {e}")


@router.post(
    "/v1/strategies/{strategy_id}/unload",
    response_model=StrategyStatusResponse,
)
async def unload_strategy(
    strategy_id: str = Path(..., description="Strategy ID"),
):
    """
    Unload strategy.

    Unloads strategy and releases resources.
    """
    runner = get_strategy_runner()

    try:
        info = runner.get_status(strategy_id)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not loaded")

        await runner.unload_strategy(strategy_id)
        return _info_to_response(info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/v1/strategies/{strategy_id}/start",
    response_model=StrategyStatusResponse,
)
async def start_strategy(
    strategy_id: str = Path(..., description="Strategy ID"),
):
    """
    Start strategy execution.

    Starts the strategy's tick loop.
    """
    runner = get_strategy_runner()

    try:
        info = await runner.start(strategy_id)
        return _info_to_response(info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/v1/strategies/{strategy_id}/tick",
    response_model=StrategyTickResponse,
)
async def tick_strategy_once(
    strategy_id: str = Path(..., description="Strategy ID"),
    request: StrategyTickRequest | None = None,
):
    """
    手动向策略注入一次行情 Tick。

    这是 fire_test 的验证入口：
    1. 策略产出 BUY/SELL 信号
    2. StrategyRunner 调用 OMS 回调
    3. OMS 回调通过 BinanceSpotDemoBroker 发真实订单
    """
    if request is None:
        raise HTTPException(status_code=400, detail="Request body is required")

    if _is_live_order_strategy_allowed(strategy_id):
        api_key = os.environ.get("BINANCE_API_KEY")
        secret_key = os.environ.get("BINANCE_SECRET_KEY")
        if not api_key or not secret_key:
            raise HTTPException(
                status_code=400,
                detail=(
                    "策略已配置为真实下单，但未检测到 BINANCE_API_KEY/BINANCE_SECRET_KEY。"
                    "请先配置环境变量。"
                ),
            )

    runner = get_strategy_runner()
    try:
        data_type = MarketDataType[request.data_type.upper()]
    except KeyError:
        valid_types = [item.name for item in MarketDataType]
        raise HTTPException(
            status_code=422,
            detail=f"data_type 非法: {request.data_type}，可选值: {valid_types}",
        )

    timestamp = request.timestamp or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    market_data = MarketData(
        symbol=request.symbol.upper(),
        data_type=data_type,
        price=request.price,
        volume=request.volume,
        timestamp=timestamp,
        metadata=request.metadata,
    )

    try:
        signal = await runner.tick(strategy_id, market_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tick failed: {e}")

    order_result = _last_order_results.pop(strategy_id, None)
    return StrategyTickResponse(
        strategy_id=strategy_id,
        signal_generated=signal is not None,
        signal_type=signal.signal_type.value if signal else None,
        signal_reason=signal.reason if signal else None,
        order_submitted=order_result is not None,
        order_result=order_result,
    )


@router.post(
    "/v1/strategies/{strategy_id}/stop",
    response_model=StrategyStatusResponse,
)
async def stop_strategy(
    strategy_id: str = Path(..., description="Strategy ID"),
):
    """
    Stop strategy execution.

    Stops the strategy but keeps it loaded.
    """
    runner = get_strategy_runner()

    try:
        info = await runner.stop(strategy_id)
        return _info_to_response(info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/v1/strategies/{strategy_id}/pause",
    response_model=StrategyStatusResponse,
)
async def pause_strategy(
    strategy_id: str = Path(..., description="Strategy ID"),
):
    """
    Pause strategy execution.

    Strategy will not receive tick data but remains loaded.
    """
    runner = get_strategy_runner()

    try:
        info = await runner.pause(strategy_id)
        return _info_to_response(info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/v1/strategies/{strategy_id}/resume",
    response_model=StrategyStatusResponse,
)
async def resume_strategy(
    strategy_id: str = Path(..., description="Strategy ID"),
):
    """
    Resume strategy execution.

    Resumes a paused strategy.
    """
    runner = get_strategy_runner()

    try:
        info = await runner.resume(strategy_id)
        return _info_to_response(info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/v1/strategies/{strategy_id}/status",
    response_model=StrategyStatusResponse,
)
async def get_strategy_status(
    strategy_id: str = Path(..., description="Strategy ID"),
):
    """
    Get strategy runtime status.

    Returns the current runtime status of a loaded strategy.
    """
    runner = get_strategy_runner()
    info = runner.get_status(strategy_id)

    if info is None:
        raise HTTPException(
            status_code=404, detail=f"Strategy {strategy_id} not loaded"
        )

    return _info_to_response(info)


@router.get(
    "/v1/strategies/loaded",
    response_model=List[StrategyStatusResponse],
)
async def list_loaded_strategies():
    """
    List all loaded strategies (Task 9.8 - rename from /running to /loaded).

    Returns a list of all loaded strategies with their runtime status.
    Note: This returns loaded strategies, not just RUNNING ones.
    """
    runner = get_strategy_runner()
    infos = runner.list_strategies()
    return [_info_to_response(info) for info in infos]
