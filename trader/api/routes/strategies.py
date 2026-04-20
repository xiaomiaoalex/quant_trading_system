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
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Path, Query
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
from trader.services import StrategyService
from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    StrategyResourceLimits,
)
from trader.core.application.risk_engine import KillSwitchLevel
from trader.storage.in_memory import get_storage
from trader.services.strategy_runner import (
    StrategyRunner,
    StrategyRuntimeInfo,
)
from trader.services.strategy_runtime_orchestrator import (
    StrategyRuntimeOrchestrator,
    RuntimeContext,
)

router = APIRouter(tags=["Strategies"])
logger = logging.getLogger(__name__)


# 全局策略执行器实例（单例）
# 注意：StrategyRunner 本身是线程安全的，内部使用 asyncio.Lock 管理状态
# 此单例在首次访问时初始化，之后所有访问共享同一实例
_strategy_runner_instance: StrategyRunner | None = None

# 全局策略运行时编排器实例（单例）
_strategy_orchestrator_instance: StrategyRuntimeOrchestrator | None = None

# 全局 Broker 实例（用于 OMS）- 使用 asyncio.Lock 保证初始化安全
_broker_instance: Optional[Any] = None
_broker_lock: asyncio.Lock = asyncio.Lock()

# 全局实盘交易开关（默认关闭）
_live_trading_enabled: Optional[bool] = None


def _is_live_trading_enabled() -> bool:
    """检查是否启用实盘交易

    优先级：
    1. 运行时通过 API 设置的开关（_live_trading_enabled）
    2. 环境变量 LIVE_TRADING_ENABLED
    3. 默认 False（安全优先）
    """
    global _live_trading_enabled
    if _live_trading_enabled is not None:
        return _live_trading_enabled
    env_val = os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower()
    return env_val in ("1", "true", "yes", "on")


def set_live_trading_enabled(enabled: bool) -> None:
    """设置实盘交易开关（Task 14: 安全闸门）"""
    global _live_trading_enabled
    _live_trading_enabled = enabled
    logger.info(f"[SafetyGate] Live trading {'enabled' if enabled else 'disabled'}")


async def _create_broker():
    """创建 Broker 实例（延迟初始化，使用 asyncio.Lock 保证安全）

    根据 BINANCE_ENV 环境变量选择环境：
    - demo / 默认: Binance Spot Demo API
    - testnet / test: Binance Spot Testnet API
    """
    global _broker_instance
    async with _broker_lock:
        if _broker_instance is None:
            from trader.adapters.broker.binance_spot_demo_broker import (
                BinanceSpotDemoBroker,
                BinanceSpotDemoBrokerConfig,
            )
            api_key = os.environ.get("BINANCE_API_KEY", "test_key")
            secret_key = os.environ.get("BINANCE_SECRET_KEY", "test_secret")
            binance_env = os.environ.get("BINANCE_ENV", "demo").lower()

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
            _broker_instance = BinanceSpotDemoBroker(config)
            await _broker_instance.connect()
        return _broker_instance


# 全局 OMSCallbackHandler 实例（用于成交回调）
_oms_handler: Optional[Any] = None
_fill_handler: Optional[Any] = None


async def _get_oms_handler():
    """
    获取全局 OMSCallbackHandler 实例（延迟初始化）
    
    Returns:
        tuple: (oms_callback 函数, fill_handler 函数)
    """
    global _oms_handler, _fill_handler
    if _oms_handler is None:
        broker = await _create_broker()
        from trader.services.oms_callback import create_oms_callback
        
        # 创建 fill_callback 用于接收成交通知
        async def fill_callback(strategy_id: str, order_id: str, symbol: str, side: str, qty: float, price: float):
            """成交回调：调用 runner.on_fill 通知策略"""
            runner = get_strategy_runner()
            try:
                await runner.on_fill(strategy_id, order_id, symbol, side, qty, price)
                logger.info(f"[FillCallback] on_fill called: strategy={strategy_id}, order={order_id}")
            except Exception as e:
                logger.error(f"[FillCallback] on_fill error: {e}")

        oms_cb, fill_h = create_oms_callback(
            broker=broker,
            live_trading_enabled=_is_live_trading_enabled,
            event_callback=_event_callback_dispatcher,
            fill_callback=fill_callback,
        )
        _oms_handler = oms_cb
        _fill_handler = fill_h
    return _oms_handler, _fill_handler


async def shutdown_strategy_runtime_resources() -> None:
    """关闭策略路由层持有的 Broker/OMS 资源，避免 reload 场景 session 泄漏。"""
    global _broker_instance, _oms_handler, _fill_handler
    async with _broker_lock:
        if _broker_instance is not None:
            try:
                await _broker_instance.disconnect()
            except Exception as e:
                logger.warning(f"[Strategies] broker disconnect failed during shutdown: {e}")
            _broker_instance = None

    _oms_handler = None
    _fill_handler = None


async def shutdown_strategy_runtime() -> None:
    """向后兼容旧调用名。"""
    await shutdown_strategy_runtime_resources()


def get_fill_handler():
    """获取已注册的 fill_handler（供 connector 注册用）"""
    global _fill_handler
    return _fill_handler


async def ensure_fill_handler_ready():
    """确保 fill_handler 已初始化并返回。"""
    global _fill_handler
    if _fill_handler is None:
        await _get_oms_handler()
    return _fill_handler


def get_strategy_runner() -> StrategyRunner:
    """
    获取全局策略执行器实例。
    
    返回应用级单例，确保所有策略在同一个执行器中运行。
    
    注意：
    - StrategyRunner 本身是线程安全的
    - OMS 回调通过事件循环异步初始化
    - Task 18: runtime_state_storage 用于策略运行时状态持久化
    """
    global _strategy_runner_instance
    if _strategy_runner_instance is None:
        from trader.storage.in_memory import get_storage
        storage = get_storage()
        _strategy_runner_instance = StrategyRunner(
            oms_callback=_oms_callback_dispatcher,
            killswitch_callback=_killswitch_callback,
            event_callback=_event_callback_dispatcher,
            runtime_state_storage=storage,  # Task 18
        )
    return _strategy_runner_instance


async def _oms_callback_dispatcher(strategy_id: str, signal) -> Optional[Dict]:
    """
    OMS 回调调度器（异步）

    实际下单逻辑在事件循环中执行，避免阻塞。
    """
    try:
        oms_cb, _ = await _get_oms_handler()
        return await oms_cb(strategy_id, signal)
    except Exception as e:
        logger.error(f"[OMSCallback] Dispatcher error: {e}")
        return None


def _killswitch_callback(strategy_id: str) -> KillSwitchLevel:
    """KillSwitch 查询回调"""
    storage = get_storage()
    state = storage.get_kill_switch(scope="STRATEGY")
    return KillSwitchLevel(state.get("level", 0))


def _event_callback_dispatcher(strategy_id: str, event_type: str, payload: Dict) -> None:
    """事件发布回调"""
    try:
        storage = get_storage()
        storage.append_event({
            "stream_key": f"strategy:{strategy_id}",
            "event_type": event_type,
            "ts_ms": int(time.time() * 1000),
            "data": payload,
        })
    except Exception as e:
        logger.error(f"[EventCallback] Dispatcher error: {e}")


def get_strategy_orchestrator() -> StrategyRuntimeOrchestrator:
    """
    获取全局策略运行时编排器实例。

    返回应用级单例，负责：
    1. 管理每个策略的运行时上下文
    2. 订阅实时行情并转换为 MarketData
    3. 调用 runner.tick() 驱动策略

    注意：
    - 编排器依赖于 runner 和 connector
    - connector 通过 set_strategy_orchestrator_connector 注入
    """
    global _strategy_orchestrator_instance
    if _strategy_orchestrator_instance is None:
        runner = get_strategy_runner()
        _strategy_orchestrator_instance = StrategyRuntimeOrchestrator(runner=runner)
    return _strategy_orchestrator_instance


def set_strategy_orchestrator_connector(connector) -> None:
    """
    注入 BinanceConnector 到编排器（由 lifespan 调用）

    Args:
        connector: BinanceConnector 实例
    """
    orchestrator = get_strategy_orchestrator()
    orchestrator.set_connector(connector)
    logger.info("[Orchestrator] BinanceConnector injected")


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


class RuntimeContextResponse(BaseModel):
    """策略运行时上下文响应"""

    strategy_id: str
    symbol: str
    status: str
    started_at: Optional[str] = None
    last_tick_at: Optional[str] = None
    tick_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    stop_reason: Optional[str] = None


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

    Unloads strategy and releases all resources including market data subscription.
    """
    runner = get_strategy_runner()
    orchestrator = get_strategy_orchestrator()

    try:
        info = runner.get_status(strategy_id)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not loaded")

        # First, unload from orchestrator (stops tick loop and cleans up subscription)
        await orchestrator.unload_strategy(strategy_id)

        # Then, unload the strategy from runner
        await runner.unload_strategy(strategy_id)
        return _info_to_response(info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/v1/strategies/{strategy_id}/start",
    response_model=RuntimeContextResponse,
)
async def start_strategy(
    strategy_id: str = Path(..., description="Strategy ID"),
    symbol: Optional[str] = Query("BTCUSDT", description="Trading symbol (e.g., BTCUSDT)"),
):
    """
    Start strategy execution with real-time market data.

    Starts the strategy's tick loop and begins receiving real-time market data
    from the Binance public stream.

    The symbol determines which market data the strategy receives.
    """
    runner = get_strategy_runner()
    orchestrator = get_strategy_orchestrator()

    try:
        # First, start the strategy in runner
        info = await runner.start(strategy_id)
        _info_to_response(info)  # Validate it works

        # Then start the orchestrator for this strategy
        ctx = await orchestrator.start_strategy(strategy_id, symbol)

        return RuntimeContextResponse(
            strategy_id=ctx.strategy_id,
            symbol=ctx.symbol,
            status=ctx.status,
            started_at=ctx.started_at.isoformat() if ctx.started_at else None,
            last_tick_at=ctx.last_tick_at.isoformat() if ctx.last_tick_at else None,
            tick_count=ctx.tick_count,
            error_count=ctx.error_count,
            last_error=ctx.last_error,
            stop_reason=ctx.stop_reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/v1/strategies/{strategy_id}/stop",
    response_model=RuntimeContextResponse,
)
async def stop_strategy(
    strategy_id: str = Path(..., description="Strategy ID"),
    reason: Optional[str] = Query(None, description="Stop reason"),
):
    """
    Stop strategy execution.

    Stops the strategy's tick loop and market data subscription,
    but keeps the strategy loaded.
    """
    runner = get_strategy_runner()
    orchestrator = get_strategy_orchestrator()

    try:
        # First, stop the orchestrator
        ctx = await orchestrator.stop_strategy(strategy_id, reason)

        # Then, stop the strategy in runner
        await runner.stop(strategy_id)

        return RuntimeContextResponse(
            strategy_id=ctx.strategy_id,
            symbol=ctx.symbol,
            status=ctx.status,
            started_at=ctx.started_at.isoformat() if ctx.started_at else None,
            last_tick_at=ctx.last_tick_at.isoformat() if ctx.last_tick_at else None,
            tick_count=ctx.tick_count,
            error_count=ctx.error_count,
            last_error=ctx.last_error,
            stop_reason=ctx.stop_reason,
        )
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
    response_model=RuntimeContextResponse,
)
async def get_strategy_status(
    strategy_id: str = Path(..., description="Strategy ID"),
):
    """
    Get strategy runtime status.

    Returns the current runtime status including orchestrator context
    (tick count, last tick time, etc.).
    """
    runner = get_strategy_runner()
    orchestrator = get_strategy_orchestrator()

    info = runner.get_status(strategy_id)
    if info is None:
        raise HTTPException(
            status_code=404, detail=f"Strategy {strategy_id} not loaded"
        )

    # Get orchestrator context if available
    ctx = orchestrator.get_context(strategy_id)

    if ctx is not None:
        return RuntimeContextResponse(
            strategy_id=ctx.strategy_id,
            symbol=ctx.symbol,
            status=ctx.status,
            started_at=ctx.started_at.isoformat() if ctx.started_at else None,
            last_tick_at=ctx.last_tick_at.isoformat() if ctx.last_tick_at else None,
            tick_count=ctx.tick_count,
            error_count=ctx.error_count,
            last_error=ctx.last_error,
            stop_reason=ctx.stop_reason,
        )

    # Fallback to runner info if no orchestrator context
    return RuntimeContextResponse(
        strategy_id=info.strategy_id,
        symbol="",
        status=info.status.value,
        started_at=info.started_at.isoformat() if info.started_at else None,
        last_tick_at=info.last_tick_at.isoformat() if info.last_tick_at else None,
        tick_count=info.tick_count,
        error_count=info.error_count,
        last_error=info.last_error,
        stop_reason=None,
    )


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


# ============================================================================
# Strategy Events Endpoints (Task 13)
# ============================================================================


class StrategyEventResponse(BaseModel):
    """策略事件响应"""
    event_id: int
    stream_key: str
    event_type: str
    ts_ms: int
    payload: Dict[str, Any]


@router.get(
    "/v1/strategies/{strategy_id}/events",
    response_model=List[StrategyEventResponse],
)
async def get_strategy_events(
    strategy_id: str = Path(..., description="Strategy ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    limit: int = Query(200, ge=1, le=2000, description="Max events to return"),
):
    """
    Get strategy events.

    Returns strategy-related events such as signals, orders, fills, and errors.
    """
    storage = get_storage()

    # Query events for this strategy
    events = storage.list_events(
        stream_key=f"strategy:{strategy_id}",
        event_type=event_type,
        limit=limit,
    )

    return [
        StrategyEventResponse(
            event_id=e.get("event_id", 0),
            stream_key=e.get("stream_key", ""),
            event_type=e.get("event_type", ""),
            ts_ms=e.get("ts_ms", 0),
            payload=e.get("data", {}),
        )
        for e in events
    ]


@router.get(
    "/v1/strategies/{strategy_id}/events/signals",
    response_model=List[StrategyEventResponse],
)
async def get_strategy_signals(
    strategy_id: str = Path(..., description="Strategy ID"),
    limit: int = Query(100, ge=1, le=1000, description="Max signals to return"),
):
    """
    Get strategy signals.

    Returns only signal events generated by the strategy.
    """
    storage = get_storage()

    # Query signal events for this strategy
    events = storage.list_events(
        stream_key=f"strategy:{strategy_id}",
        event_type="strategy.signal",
        limit=limit,
    )

    return [
        StrategyEventResponse(
            event_id=e.get("event_id", 0),
            stream_key=e.get("stream_key", ""),
            event_type=e.get("event_type", ""),
            ts_ms=e.get("ts_ms", 0),
            payload=e.get("data", {}),
        )
        for e in events
    ]


@router.get(
    "/v1/strategies/{strategy_id}/events/errors",
    response_model=List[StrategyEventResponse],
)
async def get_strategy_errors(
    strategy_id: str = Path(..., description="Strategy ID"),
    limit: int = Query(100, ge=1, le=1000, description="Max errors to return"),
):
    """
    Get strategy errors.

    Returns only error events related to the strategy.
    """
    storage = get_storage()

    # Query error events for this strategy
    events = storage.list_events(
        stream_key=f"strategy:{strategy_id}",
        event_type=None,  # We filter manually for error types
        limit=limit * 3,  # Get more since we filter
    )

    # Filter for error-related event types
    error_event_types = {
        "strategy.error",
        "strategy.order.rejected",
        "strategy.tick.error",
    }
    filtered_events = [
        e for e in events
        if e.get("event_type") in error_event_types
    ][:limit]

    return [
        StrategyEventResponse(
            event_id=e.get("event_id", 0),
            stream_key=e.get("stream_key", ""),
            event_type=e.get("event_type", ""),
            ts_ms=e.get("ts_ms", 0),
            payload=e.get("data", {}),
        )
        for e in filtered_events
    ]


# ============================================================================
# Safety Gate Endpoints (Task 14)
# ============================================================================


class SafetyGateStatusResponse(BaseModel):
    """安全闸门状态响应"""
    live_trading_enabled: bool
    killswitch_level: int
    killswitch_reason: Optional[str] = None


class SafetyGateEnableRequest(BaseModel):
    """启用实盘交易请求"""
    enabled: bool = Field(..., description="是否启用实盘交易")
    confirmed: bool = Field(False, description="确认启用（必须为 true）")


@router.get(
    "/v1/safety-gate/status",
    response_model=SafetyGateStatusResponse,
)
async def get_safety_gate_status():
    """
    Get safety gate status.

    Returns the current status of the trading safety gate including:
    - Whether live trading is enabled
    - Current KillSwitch level
    """
    storage = get_storage()
    ks_state = storage.get_kill_switch(scope="GLOBAL")

    return SafetyGateStatusResponse(
        live_trading_enabled=_is_live_trading_enabled(),
        killswitch_level=ks_state.get("level", 0),
        killswitch_reason=ks_state.get("reason"),
    )


@router.post(
    "/v1/safety-gate/enable",
    response_model=SafetyGateStatusResponse,
)
async def enable_live_trading(request: SafetyGateEnableRequest):
    """
    Enable or disable live trading.

    This is the safety gate for automated trading. Live trading is disabled by default.

    To enable live trading:
    1. Must set enabled=true AND confirmed=true
    2. KillSwitch must be at L0 or L1

    When live trading is disabled (default), all strategy signals are rejected
    without placing real orders.
    """
    if request.enabled and not request.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Must set confirmed=true to enable live trading"
        )

    storage = get_storage()
    ks_state = storage.get_kill_switch(scope="GLOBAL")
    ks_level = ks_state.get("level", 0)

    if request.enabled and ks_level >= KillSwitchLevel.L2_CANCEL_ALL_AND_HALT:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot enable live trading while KillSwitch is at L{ks_level}"
        )

    set_live_trading_enabled(request.enabled)

    return SafetyGateStatusResponse(
        live_trading_enabled=_is_live_trading_enabled(),
        killswitch_level=ks_level,
        killswitch_reason=ks_state.get("reason"),
    )
