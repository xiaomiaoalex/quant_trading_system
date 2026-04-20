"""
StrategyRunner - 策略执行器
===========================

负责动态加载策略代码、管理策略生命周期、驱动策略Tick循环。

架构约束：
- StrategyRunner 属于 Service/Adapter 层，可以有IO
- 策略代码（StrategyPlugin）运行在 Core Plane 边界，应尽量无IO
- 每个策略运行在独立 asyncio.Task 中，异常隔离

增强功能（Phase 4 Task 4.1）：
- StrategyResourceLimits 集成：资源限制检查（订单频率、持仓大小、日亏损）
- KillSwitch 对接：当 KillSwitch 升级时自动停止策略
- OMS 对接：策略信号通过 OMS 执行订单
"""
import asyncio
import importlib
import logging
import time
import traceback
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

from trader.core.application.risk_engine import KillSwitchLevel
from trader.core.application.strategy_protocol import (
    MarketData,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationResult,
    validate_strategy_plugin,
)
from trader.core.domain.models.signal import Signal

logger = logging.getLogger(__name__)


# ============================================================================
# 策略状态枚举
# ============================================================================


class StrategyStatus(Enum):
    """策略运行状态"""

    IDLE = "IDLE"  # 未加载
    LOADED = "LOADED"  # 已加载，未启动
    RUNNING = "RUNNING"  # 运行中
    PAUSED = "PAUSED"  # 暂停
    STOPPED = "STOPPED"  # 已停止
    ERROR = "ERROR"  # 异常


# ============================================================================
# 策略状态信息
# ============================================================================


@dataclass(slots=True)
class StrategyRuntimeInfo:
    """策略运行时信息"""

    strategy_id: str
    version: str
    status: StrategyStatus
    loaded_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    last_tick_at: Optional[datetime] = None
    tick_count: int = 0
    signal_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    resource_limits: Optional[StrategyResourceLimits] = None
    # 资源使用统计
    order_count_last_minute: int = 0
    last_order_times: List[float] = field(default_factory=list)  # 时间戳列表，用于滑动窗口计数
    # 注意：daily_loss 追踪需要 OMS 提供账户余额信息，暂时移除
    # TODO: 未来在 on_fill 回调中实现每日亏损更新
    # daily_loss: Decimal = Decimal("0")
    # 阻塞原因
    blocked_reason: Optional[str] = None


# ============================================================================
# 策略执行器
# ============================================================================


class StrategyRunner:
    """
    策略执行器

    职责：
    1. 动态加载策略代码（从模块路径）
    2. 管理策略生命周期（启动/停止/暂停/恢复）
    3. 驱动策略Tick循环
    4. 异常隔离（单策略崩溃不影响其他策略）
    5. 资源限制检查（StrategyResourceLimits）
    6. KillSwitch 对接（自动停止策略）
    7. OMS 对接（信号转订单执行）

    使用示例：
        runner = StrategyRunner(
            oms=oms_instance,
            killswitch_callback=get_killswitch_level,
        )

        # 加载策略
        await runner.load_strategy(
            strategy_id="ema_cross",
            version="v1",
            module_path="strategies.ema_cross",
            config={"fast_period": 12, "slow_period": 26},
            resource_limits=StrategyResourceLimits(max_orders_per_minute=10),
        )

        # 启动策略
        await runner.start("ema_cross")

        # 驱动Tick
        signals = await runner.tick("ema_cross", market_data)

        # 停止策略
        await runner.stop("ema_cross")
    """

    def __init__(
        self,
        signal_callback: Optional[Callable[[str, Signal], Any]] = None,
        oms_callback: Optional[Callable[[str, Signal], Any]] = None,
        killswitch_callback: Optional[Callable[[str], KillSwitchLevel]] = None,
        event_callback: Optional[Callable[[str, str, Dict[str, Any]], Any]] = None,
        max_errors_before_error_state: int = 10,
    ):
        """
        初始化策略执行器

        Args:
            signal_callback: 信号回调函数，接收 (strategy_id, signal)
            oms_callback: OMS 执行回调，接收 (strategy_id, signal)，返回订单
            killswitch_callback: KillSwitch 查询回调，返回当前 KillSwitch 级别
            event_callback: 事件发布回调，接收 (strategy_id, event_type, payload)
            max_errors_before_error_state: 错误次数阈值，超过此值策略进入ERROR状态
        """
        self._plugins: Dict[str, StrategyPlugin] = {}
        self._dynamic_modules: Dict[str, types.ModuleType] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._infos: Dict[str, StrategyRuntimeInfo] = {}
        self._signal_callback = signal_callback
        self._oms_callback = oms_callback
        self._killswitch_callback = killswitch_callback
        self._event_callback = event_callback
        self._max_errors_before_error_state = max_errors_before_error_state
        self._running = True
        # 策略级别的锁，用于保护并发更新
        self._strategy_locks: Dict[str, asyncio.Lock] = {}

    async def load_strategy(
        self,
        strategy_id: str,
        version: str,
        module_path: str,
        config: Optional[Dict[str, Any]] = None,
        resource_limits: Optional[StrategyResourceLimits] = None,
    ) -> StrategyRuntimeInfo:
        """
        动态加载策略代码

        Args:
            strategy_id: 策略ID
            version: 策略版本
            module_path: 模块路径（如 "strategies.ema_cross"）
            config: 策略配置参数
            resource_limits: 资源限制配置（可选）

        Returns:
            StrategyRuntimeInfo: 策略运行时信息

        Raises:
            ValueError: 策略已加载或模块加载失败
            TypeError: 模块未实现 StrategyPlugin 协议
        """
        if strategy_id in self._plugins:
            raise ValueError(f"策略已加载: {strategy_id}")

        try:
            # 动态导入模块
            module = importlib.import_module(module_path)

            # 获取策略实例
            if not hasattr(module, "get_plugin"):
                raise ValueError(f"模块 {module_path} 缺少 get_plugin() 函数")

            plugin = module.get_plugin()

            # 验证协议（使用手动检查，因为 @runtime_checkable 与 async 方法不完全兼容）
            is_valid, error_msg = validate_strategy_plugin(plugin)
            if not is_valid:
                raise TypeError(
                    f"模块 {module_path} 返回的对象未实现 StrategyPlugin 协议: {error_msg}"
                )

            # 设置策略版本（如果插件支持）
            # 注意：name 属性是只读的，不能被外部修改
            # strategy_id 是 Runner 内部的标识符，存储在 _plugins 字典的 key 中
            if hasattr(plugin, 'version'):
                plugin.version = version

            # 初始化策略
            await plugin.initialize(config or {})

            # 注册策略
            self._plugins[strategy_id] = plugin
            self._infos[strategy_id] = StrategyRuntimeInfo(
                strategy_id=strategy_id,
                version=version,
                status=StrategyStatus.LOADED,
                loaded_at=datetime.now(timezone.utc),
                config=config or {},
                resource_limits=resource_limits,
            )

            logger.info(f"策略加载成功: {strategy_id} v{version}")
            return self._infos[strategy_id]

        except Exception as e:
            logger.error(f"策略加载失败: {strategy_id}, 错误: {e}")
            raise

    async def load_strategy_from_code(
        self,
        strategy_id: str,
        version: str,
        code: str,
        config: Optional[Dict[str, Any]] = None,
        resource_limits: Optional[StrategyResourceLimits] = None,
    ) -> StrategyRuntimeInfo:
        """
        从代码字符串动态加载策略。

        Args:
            strategy_id: 策略ID
            version: 策略版本
            code: 策略代码字符串（必须定义 get_plugin()）
            config: 初始化配置
            resource_limits: 资源限制

        Returns:
            StrategyRuntimeInfo: 策略运行时信息
        """
        if strategy_id in self._plugins:
            raise ValueError(f"策略已加载: {strategy_id}")

        module_name = f"dynamic_strategy_{strategy_id}_{int(time.time() * 1000)}"
        module = types.ModuleType(module_name)
        module.__dict__["__builtins__"] = __builtins__

        try:
            compiled = compile(code, f"<{module_name}>", "exec")
            exec(compiled, module.__dict__)

            if "get_plugin" not in module.__dict__:
                raise ValueError("代码缺少 get_plugin() 函数")

            plugin = module.__dict__["get_plugin"]()
            is_valid, error_msg = validate_strategy_plugin(plugin)
            if not is_valid:
                raise TypeError(f"动态策略未实现 StrategyPlugin 协议: {error_msg}")

            if hasattr(plugin, "version"):
                plugin.version = version

            await plugin.initialize(config or {})

            self._plugins[strategy_id] = plugin
            self._dynamic_modules[strategy_id] = module
            self._infos[strategy_id] = StrategyRuntimeInfo(
                strategy_id=strategy_id,
                version=version,
                status=StrategyStatus.LOADED,
                loaded_at=datetime.now(timezone.utc),
                config=config or {},
                resource_limits=resource_limits,
            )
            logger.info(f"策略加载成功（代码）: {strategy_id} v{version}")
            return self._infos[strategy_id]
        except Exception as e:
            logger.error(f"策略加载失败（代码）: {strategy_id}, 错误: {e}")
            raise

    async def unload_strategy(self, strategy_id: str) -> None:
        """
        卸载策略

        Args:
            strategy_id: 策略ID

        Raises:
            ValueError: 策略未加载或正在运行
        """
        if strategy_id not in self._plugins:
            raise ValueError(f"策略未加载: {strategy_id}")

        info = self._infos.get(strategy_id)
        if info and info.status == StrategyStatus.RUNNING:
            raise ValueError(f"策略正在运行，请先停止: {strategy_id}")

        plugin = self._plugins.get(strategy_id)

        # 清理资源（即使失败也要记录严重错误，因为可能存在资源泄漏）
        shutdown_error = None
        try:
            if plugin:
                await plugin.shutdown()
        except Exception as e:
            shutdown_error = str(e)
            logger.error(
                f"策略卸载时清理资源失败: {strategy_id}, 错误: {e}\n"
                f"{traceback.format_exc()}"
            )
        
        # 即使shutdown失败，也要从字典中移除
        # 使用 pop 安全移除，不会在失败时重复移除
        self._plugins.pop(strategy_id, None)
        self._dynamic_modules.pop(strategy_id, None)
        self._infos.pop(strategy_id, None)
        self._strategy_locks.pop(strategy_id, None)  # 清理策略锁

        # 即使shutdown失败，也记录完整的策略卸载信息
        if shutdown_error:
            logger.error(f"策略已卸载但存在资源泄漏风险: {strategy_id}, shutdown错误: {shutdown_error}")
        else:
            logger.info(f"策略卸载成功: {strategy_id}")

    async def start(self, strategy_id: str) -> StrategyRuntimeInfo:
        """
        启动策略

        Args:
            strategy_id: 策略ID

        Returns:
            StrategyRuntimeInfo: 更新后的策略运行时信息

        Raises:
            ValueError: 策略未加载或已在运行
        """
        if strategy_id not in self._plugins:
            raise ValueError(f"策略未加载: {strategy_id}")

        info = self._infos[strategy_id]
        if info.status == StrategyStatus.RUNNING:
            raise ValueError(f"策略已在运行: {strategy_id}")

        # 更新状态
        info.status = StrategyStatus.RUNNING
        info.started_at = datetime.now(timezone.utc)
        info.error_count = 0
        info.last_error = None

        logger.info(f"策略启动成功: {strategy_id}")
        return info

    async def stop(self, strategy_id: str) -> StrategyRuntimeInfo:
        """
        停止策略

        Args:
            strategy_id: 策略ID

        Returns:
            StrategyRuntimeInfo: 更新后的策略运行时信息

        Raises:
            ValueError: 策略未加载
        """
        if strategy_id not in self._plugins:
            raise ValueError(f"策略未加载: {strategy_id}")

        info = self._infos[strategy_id]

        # 取消后台任务
        task = self._tasks.pop(strategy_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 更新状态
        info.status = StrategyStatus.STOPPED

        logger.info(f"策略停止成功: {strategy_id}")
        return info

    async def pause(self, strategy_id: str) -> StrategyRuntimeInfo:
        """
        暂停策略

        策略暂停后不会接收Tick数据，但保持加载状态。

        Args:
            strategy_id: 策略ID

        Returns:
            StrategyRuntimeInfo: 更新后的策略运行时信息
        """
        if strategy_id not in self._plugins:
            raise ValueError(f"策略未加载: {strategy_id}")

        info = self._infos[strategy_id]
        if info.status != StrategyStatus.RUNNING:
            raise ValueError(f"策略未在运行，无法暂停: {strategy_id}")

        info.status = StrategyStatus.PAUSED
        logger.info(f"策略暂停成功: {strategy_id}")
        return info

    async def resume(self, strategy_id: str) -> StrategyRuntimeInfo:
        """
        恢复策略

        Args:
            strategy_id: 策略ID

        Returns:
            StrategyRuntimeInfo: 更新后的策略运行时信息
        """
        if strategy_id not in self._plugins:
            raise ValueError(f"策略未加载: {strategy_id}")

        info = self._infos[strategy_id]
        if info.status != StrategyStatus.PAUSED:
            raise ValueError(f"策略未暂停，无法恢复: {strategy_id}")

        info.status = StrategyStatus.RUNNING
        logger.info(f"策略恢复成功: {strategy_id}")
        return info

    async def tick(
        self, strategy_id: str, market_data: MarketData
    ) -> Optional[Signal]:
        """
        驱动策略Tick

        向指定策略传递市场数据，获取交易信号。

        增强功能（Phase 4 Task 4.1）：
        1. KillSwitch 检查：如果 KillSwitch >= L1，阻止新订单
        2. 资源限制检查：订单频率限制
        3. OMS 对接：信号通过 OMS 执行

        Args:
            strategy_id: 策略ID
            market_data: 市场数据

        Returns:
            Signal 或 None

        Raises:
            ValueError: 策略未加载或未运行
        """
        if strategy_id not in self._plugins:
            raise ValueError(f"策略未加载: {strategy_id}")

        info = self._infos[strategy_id]
        if info.status not in (StrategyStatus.RUNNING, StrategyStatus.PAUSED):
            raise ValueError(f"策略未运行: {strategy_id}")

        # 暂停状态不处理Tick
        if info.status == StrategyStatus.PAUSED:
            return None

        plugin = self._plugins[strategy_id]

        # ==================== KillSwitch 检查 ====================
        if self._killswitch_callback:
            try:
                ks_level = self._killswitch_callback(strategy_id)
                if ks_level >= KillSwitchLevel.L1_NO_NEW_POSITIONS:
                    info.blocked_reason = f"KillSwitch L{ks_level} active"
                    logger.warning(
                        f"策略 {strategy_id} 被 KillSwitch L{ks_level} 阻止"
                    )
                    # L2+ 需要完全停止策略
                    if ks_level >= KillSwitchLevel.L2_CANCEL_ALL_AND_HALT:
                        await self.stop(strategy_id)
                        info.blocked_reason = f"KillSwitch L{ks_level} - strategy stopped"
                    return None
            except Exception as e:
                logger.error(f"KillSwitch 检查失败: {e}")

        # 清除阻塞原因（如果之前有的话）
        info.blocked_reason = None

        try:
            # 更新Tick计数
            info.tick_count += 1
            info.last_tick_at = datetime.now(timezone.utc)

            # 调用策略（带超时控制）
            limits = info.resource_limits
            timeout_seconds = limits.timeout_seconds if limits else 0.0
            if timeout_seconds > 0:
                signal = await asyncio.wait_for(
                    plugin.on_market_data(market_data),
                    timeout=timeout_seconds
                )
            else:
                signal = await plugin.on_market_data(market_data)

            # 处理信号
            if signal is not None:
                info.signal_count += 1

                # 确保信号包含策略信息
                if not signal.strategy_name:
                    signal.strategy_name = strategy_id

                # 发布信号事件
                if self._event_callback:
                    try:
                        direction = signal.signal_type.value if signal.signal_type else None
                        self._event_callback(strategy_id, "strategy.signal", {
                            "symbol": signal.symbol,
                            "direction": direction,
                            "signal_type": signal.signal_type.value if signal.signal_type else None,
                            "quantity": str(signal.quantity) if signal.quantity else None,
                            "price": str(signal.price) if signal.price else None,
                            "reason": signal.reason,
                        })
                    except Exception as e:
                        logger.error(f"事件发布失败: {strategy_id}, 错误: {e}")

                # ==================== 资源限制检查 ====================
                if limits:
                    # 检查订单频率限制（使用滑动窗口）
                    current_time = time.time()
                    # 清理超过60秒的旧记录
                    while info.last_order_times and info.last_order_times[0] < current_time - 60:
                        info.last_order_times.pop(0)

                    if len(info.last_order_times) >= limits.max_orders_per_minute:
                        logger.warning(
                            f"策略 {strategy_id} 订单频率超限: "
                            f"{len(info.last_order_times)}/{limits.max_orders_per_minute}"
                        )
                        info.blocked_reason = "Order rate limit exceeded"
                        signal = None
                    else:
                        # 记录订单时间（仅当真正下单时）
                        if self._oms_callback:
                            info.last_order_times.append(current_time)

                # ==================== OMS 执行 ====================
                if signal is not None and self._oms_callback:
                    try:
                        order_result = await self._oms_callback(strategy_id, signal)
                        # 发布订单提交事件
                        if self._event_callback and order_result:
                            side = signal.get_order_side().value if signal.signal_type else None
                            self._event_callback(strategy_id, "strategy.order.submitted", {
                                "symbol": signal.symbol,
                                "side": side,
                                "quantity": str(signal.quantity) if signal.quantity else None,
                                "price": str(signal.price) if signal.price else None,
                            })
                    except Exception as e:
                        logger.error(f"OMS 执行失败: {strategy_id}, 错误: {e}")
                        signal = None

                # 触发信号回调（仅当信号未被阻止时）
                if signal is not None and self._signal_callback:
                    try:
                        await self._signal_callback(strategy_id, signal)
                    except Exception as e:
                        logger.error(
                            f"信号回调异常: {strategy_id}, 错误: {e}\n"
                            f"{traceback.format_exc()}"
                        )

            return signal

        except asyncio.TimeoutError:
            info.error_count += 1
            info.last_error = f"策略执行超时: {timeout_seconds}s"
            logger.error(
                f"策略执行超时: {strategy_id}, 超时: {timeout_seconds}s"
            )
            return None

        except Exception as e:
            # 异常隔离：记录错误但不崩溃
            info.error_count += 1
            info.last_error = str(e)
            logger.error(
                f"策略Tick异常: {strategy_id}, 错误: {e}\n"
                f"{traceback.format_exc()}"
            )

            # 错误次数过多，标记为ERROR状态
            if info.error_count >= self._max_errors_before_error_state:
                info.status = StrategyStatus.ERROR
                logger.error(f"策略错误次数过多，标记为ERROR: {strategy_id}")

            return None

    async def on_fill(
        self,
        strategy_id: str,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> None:
        """
        通知策略订单成交 (协议方法).

        Args:
            strategy_id: 策略ID
            order_id: 订单ID
            symbol: 交易对
            side: 买卖方向
            quantity: 成交数量
            price: 成交价格
        """
        if strategy_id not in self._plugins:
            logger.warning(f"策略未加载，无法通知成交: {strategy_id}")
            return

        plugin = self._plugins[strategy_id]
        try:
            await plugin.on_fill(order_id, symbol, side, quantity, price)
        except Exception as e:
            logger.error(f"策略成交回调异常: {strategy_id}, 错误: {e}")

    async def notify_fill(
        self,
        strategy_id: str,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> None:
        """
        通知策略订单成交 (别名方法, 兼容旧代码).

        Args:
            strategy_id: 策略ID
            order_id: 订单ID
            symbol: 交易对
            side: 买卖方向
            quantity: 成交数量
            price: 成交价格
        """
        await self.on_fill(strategy_id, order_id, symbol, side, quantity, price)

    async def on_cancel(self, strategy_id: str, order_id: str, reason: str) -> None:
        """
        通知策略订单取消 (协议方法).

        Args:
            strategy_id: 策略ID
            order_id: 订单ID
            reason: 取消原因
        """
        if strategy_id not in self._plugins:
            logger.warning(f"策略未加载，无法通知取消: {strategy_id}")
            return

        plugin = self._plugins[strategy_id]
        try:
            await plugin.on_cancel(order_id, reason)
        except Exception as e:
            logger.error(f"策略取消回调异常: {strategy_id}, 错误: {e}")

    async def notify_cancel(self, strategy_id: str, order_id: str, reason: str) -> None:
        """
        通知策略订单取消 (别名方法, 兼容旧代码).

        Args:
            strategy_id: 策略ID
            order_id: 订单ID
            reason: 取消原因
        """
        await self.on_cancel(strategy_id, order_id, reason)

    def _get_strategy_lock(self, strategy_id: str) -> asyncio.Lock:
        """获取策略级别的锁，如果不存在则创建"""
        if strategy_id not in self._strategy_locks:
            self._strategy_locks[strategy_id] = asyncio.Lock()
        return self._strategy_locks[strategy_id]

    async def update_strategy_config(
        self,
        strategy_id: str,
        config: Dict[str, Any],
    ) -> StrategyRuntimeInfo:
        """
        更新策略配置参数

        允许在策略运行期间动态调整参数，无需停止策略。
        参数变更会触发策略的 update_config 方法进行验证。
        使用策略级别的锁保证并发安全。

        Args:
            strategy_id: 策略ID
            config: 新的配置参数（部分更新，支持增量更新）

        Returns:
            StrategyRuntimeInfo: 更新后的策略运行时信息

        Raises:
            ValueError: 策略未加载
            ValueError: 策略正在运行且不允许动态调整
        """
        if strategy_id not in self._plugins:
            raise ValueError(f"策略未加载: {strategy_id}")

        # 使用策略级别的锁保证并发安全
        lock = self._get_strategy_lock(strategy_id)
        async with lock:
            info = self._infos[strategy_id]
            plugin = self._plugins[strategy_id]

            # 调用策略的 update_config 方法
            # 如果插件没有实现 update_config，则使用 initialize 作为后备
            try:
                if hasattr(plugin, 'update_config'):
                    update_method = getattr(plugin, 'update_config')
                    if asyncio.iscoroutinefunction(update_method):
                        validation_result: ValidationResult = await update_method(config)
                    else:
                        validation_result = update_method(config)
                    if not validation_result.is_valid:
                        raise ValueError(
                            f"参数验证失败: {[e.message for e in validation_result.errors]}"
                        )
                else:
                    # 如果插件不支持 update_config，合并配置后调用 initialize
                    merged_config = {**info.config, **config}
                    await plugin.initialize(merged_config)
            except Exception as e:
                logger.error(f"策略配置更新失败: {strategy_id}, 错误: {e}")
                raise

            # 更新存储的配置
            info.config = {**info.config, **config}

            logger.info(f"策略配置更新成功: {strategy_id}, 新配置: {config}")
            return info

    def get_status(self, strategy_id: str) -> Optional[StrategyRuntimeInfo]:
        """
        获取策略运行状态

        Args:
            strategy_id: 策略ID

        Returns:
            StrategyRuntimeInfo 或 None（策略未加载）
        """
        return self._infos.get(strategy_id)

    def get_plugin(self, strategy_id: str) -> Optional[Any]:
        """
        获取策略插件实例

        Args:
            strategy_id: 策略ID

        Returns:
            策略插件实例或 None（策略未加载）
        """
        return self._plugins.get(strategy_id)

    async def validate_strategy_config(
        self,
        strategy_id: str,
        config: Dict[str, Any],
    ) -> ValidationResult:
        """
        验证策略配置参数

        在不实际更新的情况下验证配置参数的有效性。
        使用策略级别的锁保证并发安全。

        Args:
            strategy_id: 策略ID
            config: 待验证的配置参数

        Returns:
            ValidationResult: 验证结果

        Raises:
            ValueError: 策略未加载
        """
        if strategy_id not in self._plugins:
            raise ValueError(f"策略未加载: {strategy_id}")

        # 使用策略级别的锁保证并发安全
        lock = self._get_strategy_lock(strategy_id)
        async with lock:
            plugin = self._plugins[strategy_id]

            # 如果插件支持 update_config，使用它进行验证
            if hasattr(plugin, 'update_config'):
                update_method = getattr(plugin, 'update_config')
                if asyncio.iscoroutinefunction(update_method):
                    return await update_method(config)
                else:
                    return update_method(config)
            else:
                # 如果插件不支持 update_config，返回警告
                from trader.core.application.strategy_protocol import ValidationResult, ValidationStatus
                return ValidationResult(
                    status=ValidationStatus.VALID,
                    warnings=["Plugin does not support update_config, will use initialize"],
                )

    def list_strategies(self) -> List[StrategyRuntimeInfo]:
        """
        列出所有已加载的策略

        Returns:
            List[StrategyRuntimeInfo]: 策略运行时信息列表
        """
        return list(self._infos.values())

    async def shutdown(self) -> None:
        """
        关闭所有策略

        停止所有运行中的策略并清理资源。
        """
        self._running = False

        # 停止所有策略
        for strategy_id in list(self._plugins.keys()):
            try:
                info = self._infos.get(strategy_id)
                if info and info.status == StrategyStatus.RUNNING:
                    await self.stop(strategy_id)
            except Exception as e:
                logger.error(f"关闭策略失败: {strategy_id}, 错误: {e}")

        # 卸载所有策略
        for strategy_id in list(self._plugins.keys()):
            try:
                await self.unload_strategy(strategy_id)
            except Exception as e:
                logger.error(f"卸载策略失败: {strategy_id}, 错误: {e}")

        logger.info("所有策略已关闭")
