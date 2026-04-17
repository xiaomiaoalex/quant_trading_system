"""
StrategyLifecycleManager - 策略生命周期管理器
==============================================

整合Phase 4所有组件，提供统一的策略生命周期管理。

核心组件：
- StrategyLifecycle: 单个策略的完整生命周期
- LifecycleStatus: 生命周期状态枚举
- LifecycleEvent: 生命周期事件记录
- StrategyLifecycleManager: 生命周期管理器

生命周期状态转换：
    DRAFT → VALIDATED → BACKTESTED → APPROVED → RUNNING → STOPPED

设计原则：
1. 崩溃隔离：每个策略独立运行，异常不影响其他策略
2. 状态可追溯：所有状态转换记录到事件历史
3. 幂等操作：支持重复调用的幂等性
4. 完整审计：所有操作记录到审计日志

验收标准：
- P99<500ms
- 4个端到端场景覆盖
- 崩溃隔离
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
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
    Sequence,
    runtime_checkable,
)

from trader.core.application.risk_engine import KillSwitchLevel, RiskLevel
from trader.core.application.strategy_protocol import (
    MarketData,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationError,
    ValidationResult,
    ValidationStatus,
)
from trader.core.domain.models.signal import Signal
from trader.services.strategy_evaluator import (
    BacktestConfig,
    BacktestEngine,
    BacktestReport,
    DataQualityIssue,
    DataQualityResult,
    DataQualityStatus,
    EvaluationResult,
    EvaluationStatus,
    StrategyMetrics,
)
from trader.services.strategy_hotswap import (
    SwapResult,
    SwapState,
    StrategyHotSwapper,
    StrategyLoader,
    VersionManager,
)
from insight.ai_strategy_generator import (
    AIStrategyGenerator,
    GeneratedStrategy,
    GenerationConfig,
    LLMBackend,
)
from insight.chat_interface import (
    ChatSession,
    ChatSessionStorePort,
    ChatResponse,
    InMemoryChatSessionStore,
    SessionStatus,
    StrategyChatInterface,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 生命周期状态枚举
# ============================================================================


class LifecycleStatus(Enum):
    """策略生命周期状态"""

    DRAFT = "DRAFT"  # 草稿状态（刚生成或刚创建）
    VALIDATED = "VALIDATED"  # 已通过验证
    BACKTESTED = "BACKTESTED"  # 已完成回测
    APPROVED = "APPROVED"  # 已审批通过
    RUNNING = "RUNNING"  # 运行中
    STOPPED = "STOPPED"  # 已停止
    FAILED = "FAILED"  # 失败状态
    ARCHIVED = "ARCHIVED"  # 已归档


# ============================================================================
# 生命周期事件
# ============================================================================


class LifecycleEventType(Enum):
    """生命周期事件类型"""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    BACKTEST_STARTED = "BACKTEST_STARTED"
    BACKTEST_COMPLETED = "BACKTEST_COMPLETED"
    BACKTEST_FAILED = "BACKTEST_FAILED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STARTED = "STARTED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    HOTSWAP_STARTED = "HOTSWAP_STARTED"
    HOTSWAP_COMPLETED = "HOTSWAP_COMPLETED"
    HOTSWAP_ROLLBACK = "HOTSWAP_ROLLBACK"
    ARCHIVED = "ARCHIVED"
    PARAMS_UPDATED = "PARAMS_UPDATED"


@dataclass(slots=True)
class LifecycleEvent:
    """
    生命周期事件

    属性：
        event_id: 事件ID
        event_type: 事件类型
        timestamp: 事件时间戳
        from_status: 转换前的状态
        to_status: 转换后的状态
        metadata: 扩展元数据
        error: 错误信息（如果事件类型为ERROR）
    """

    event_id: str
    event_type: LifecycleEventType
    timestamp: datetime
    from_status: Optional[LifecycleStatus]
    to_status: LifecycleStatus
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def __post_init__(self):
        if not self.event_id:
            object.__setattr__(self, 'event_id', str(uuid.uuid4()))


# ============================================================================
# 生命周期结果
# ============================================================================


@dataclass(slots=True)
class ValidationOutcome:
    """验证结果"""

    success: bool
    status: LifecycleStatus
    validation_result: Optional[ValidationResult] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass(slots=True)
class BacktestOutcome:
    """回测结果"""

    success: bool
    status: LifecycleStatus
    report: Optional[BacktestReport] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass(slots=True)
class ApprovalOutcome:
    """审批结果"""

    success: bool
    status: LifecycleStatus
    approved_by: Optional[str] = None
    approval_notes: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass(slots=True)
class StartOutcome:
    """启动结果"""

    success: bool
    status: LifecycleStatus
    runtime_info: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass(slots=True)
class StopOutcome:
    """停止结果"""

    success: bool
    status: LifecycleStatus
    final_metrics: Optional[StrategyMetrics] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    resource_unloaded: bool = False  # 是否释放了资源


@dataclass(slots=True)
class SwapOutcome:
    """热插拔结果"""

    success: bool
    old_status: LifecycleStatus
    new_status: LifecycleStatus
    swap_result: Optional[SwapResult] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass(slots=True)
class UpdateParamsOutcome:
    """参数更新结果"""

    success: bool
    status: LifecycleStatus
    updated_config: Optional[Dict[str, Any]] = None
    validation_result: Optional[ValidationResult] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


# ============================================================================
# 策略生命周期
# ============================================================================


@dataclass(slots=True)
class StrategyLifecycle:
    """
    策略生命周期

    管理单个策略的完整生命周期。

    属性：
        strategy_id: 策略ID
        strategy: 策略插件实例
        status: 当前生命周期状态
        version: 策略版本
        history: 生命周期事件历史
        created_at: 创建时间
        updated_at: 更新时间
        backtest_report: 回测报告（如果已回测）
        approval_record: 审批记录（如果已审批）
        current_metrics: 当前性能指标
        metadata: 扩展元数据
    """

    strategy_id: str
    strategy: StrategyPlugin
    status: LifecycleStatus
    version: str = "1.0.0"
    history: List[LifecycleEvent] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    backtest_report: Optional[BacktestReport] = None
    approval_record: Optional[Dict[str, Any]] = None
    current_metrics: Optional[StrategyMetrics] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.created_at, datetime) and self.created_at.tzinfo is None:
            object.__setattr__(self, 'created_at', self.created_at.replace(tzinfo=timezone.utc))
        if isinstance(self.updated_at, datetime) and self.updated_at.tzinfo is None:
            object.__setattr__(self, 'updated_at', self.updated_at.replace(tzinfo=timezone.utc))

    def _add_event(
        self,
        event_type: LifecycleEventType,
        from_status: Optional[LifecycleStatus],
        to_status: LifecycleStatus,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """添加生命周期事件"""
        event = LifecycleEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            from_status=from_status,
            to_status=to_status,
            metadata=metadata or {},
            error=error,
        )
        self.history.append(event)
        self.updated_at = datetime.now(timezone.utc)

    def _transition_to(self, new_status: LifecycleStatus, event_type: LifecycleEventType, **kwargs) -> None:
        """状态转换"""
        self._add_event(event_type, self.status, new_status, **kwargs)
        self.status = new_status

    def can_transition_to(self, target_status: LifecycleStatus) -> bool:
        """检查是否可以转换到目标状态"""
        valid_transitions = {
            LifecycleStatus.DRAFT: [LifecycleStatus.VALIDATED, LifecycleStatus.FAILED],
            LifecycleStatus.VALIDATED: [LifecycleStatus.BACKTESTED, LifecycleStatus.FAILED],
            LifecycleStatus.BACKTESTED: [LifecycleStatus.APPROVED, LifecycleStatus.FAILED],
            LifecycleStatus.APPROVED: [LifecycleStatus.RUNNING, LifecycleStatus.FAILED],
            LifecycleStatus.RUNNING: [LifecycleStatus.STOPPED, LifecycleStatus.FAILED],
            LifecycleStatus.STOPPED: [LifecycleStatus.RUNNING, LifecycleStatus.ARCHIVED],
            LifecycleStatus.FAILED: [LifecycleStatus.DRAFT, LifecycleStatus.ARCHIVED],
            LifecycleStatus.ARCHIVED: [],
        }
        return target_status in valid_transitions.get(self.status, [])

    def get_status_summary(self) -> Dict[str, Any]:
        """获取状态摘要"""
        return {
            "strategy_id": self.strategy_id,
            "status": self.status.value,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "event_count": len(self.history),
            "has_backtest": self.backtest_report is not None,
            "has_approval": self.approval_record is not None,
            "has_metrics": self.current_metrics is not None,
        }


# ============================================================================
# 端口接口
# ============================================================================


@runtime_checkable
class LifecyclePort(Protocol):
    """生命周期管理器端口"""

    async def get_lifecycle(self, strategy_id: str) -> Optional[StrategyLifecycle]:
        """获取策略生命周期"""
        ...

    async def save_lifecycle(self, lifecycle: StrategyLifecycle) -> None:
        """保存策略生命周期"""
        ...

    async def list_lifecycles(self, status: Optional[LifecycleStatus] = None) -> List[StrategyLifecycle]:
        """列出策略生命周期"""
        ...


@runtime_checkable
class RunnerPort(Protocol):
    """策略运行器端口"""

    async def load_strategy(self, strategy_id: str, plugin: StrategyPlugin, config: Dict[str, Any]) -> Any:
        """加载策略"""
        ...

    async def start_strategy(self, strategy_id: str) -> Any:
        """启动策略"""
        ...

    async def stop_strategy(self, strategy_id: str) -> Any:
        """停止策略"""
        ...

    async def unload_strategy(self, strategy_id: str) -> None:
        """卸载策略并释放资源"""
        ...

    async def tick_strategy(self, strategy_id: str, market_data: MarketData) -> Optional[Signal]:
        """驱动策略Tick"""
        ...


# ============================================================================
# 内存存储实现
# ============================================================================


class InMemoryLifecycleStore:
    """内存生命周期存储（用于测试和简单场景）"""

    def __init__(self):
        self._lifecycles: Dict[str, StrategyLifecycle] = {}

    async def get_lifecycle(self, strategy_id: str) -> Optional[StrategyLifecycle]:
        return self._lifecycles.get(strategy_id)

    async def save_lifecycle(self, lifecycle: StrategyLifecycle) -> None:
        self._lifecycles[lifecycle.strategy_id] = lifecycle

    async def list_lifecycles(
        self, status: Optional[LifecycleStatus] = None
    ) -> List[StrategyLifecycle]:
        if status is None:
            return list(self._lifecycles.values())
        return [lc for lc in self._lifecycles.values() if lc.status == status]

    async def delete_lifecycle(self, strategy_id: str) -> None:
        self._lifecycles.pop(strategy_id, None)


# ============================================================================
# 策略生命周期管理器
# ============================================================================


class StrategyLifecycleManager:
    """
    策略生命周期管理器

    整合所有Phase 4组件，提供统一的策略生命周期管理。

    核心功能：
    1. create_strategy: 从代码创建策略
    2. validate_strategy: 验证策略有效性
    3. run_backtest: 运行回测
    4. approve_strategy: 审批策略
    5. start_strategy: 启动策略
    6. stop_strategy: 停止策略
    7. swap_strategy: 热插拔更新

    使用示例：
        manager = StrategyLifecycleManager(
            runner=StrategyRunner(...),
            evaluator=BacktestEngine(...),
            hotswapper=StrategyHotSwapper(...),
        )

        # AI生成并部署
        lifecycle = await manager.create_strategy(code)
        validation = await manager.validate_strategy(lifecycle)
        backtest = await manager.run_backtest(lifecycle)
        approval = await manager.approve_strategy(lifecycle)
        start = await manager.start_strategy(lifecycle)

        # 热插拔更新
        swap = await manager.swap_strategy(old_lifecycle, new_lifecycle)

    架构约束：
    - 属于Service层，可以使用IO
    - 状态转换必须原子性
    - 使用哈希锁保证并发安全
    """

    def __init__(
        self,
        runner: Optional[Any] = None,
        evaluator: Optional[BacktestEngine] = None,
        hotswapper: Optional[StrategyHotSwapper] = None,
        generator: Optional[AIStrategyGenerator] = None,
        chat_interface: Optional[StrategyChatInterface] = None,
        lifecycle_store: Optional[LifecyclePort] = None,
        killswitch_callback: Optional[Callable[[], KillSwitchLevel]] = None,
    ):
        """
        初始化策略生命周期管理器

        Args:
            runner: 策略运行器（可选）
            evaluator: 回测引擎（可选）
            hotswapper: 热插拔管理器（可选）
            generator: AI生成器（可选）
            chat_interface: 聊天接口（可选）
            lifecycle_store: 生命周期存储（可选，默认使用内存存储）
            killswitch_callback: KillSwitch回调（可选）
        """
        self._runner = runner
        self._evaluator = evaluator or BacktestEngine()
        self._hotswapper = hotswapper
        self._generator = generator
        self._chat = chat_interface
        self._store = lifecycle_store or InMemoryLifecycleStore()
        self._killswitch_callback = killswitch_callback

        # 哈希锁字典
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_guard = asyncio.Lock()

        # 性能指标
        self._metrics: Dict[str, List[float]] = {}  # operation -> durations

    async def _get_lock(self, strategy_id: str) -> asyncio.Lock:
        """获取策略对应的锁"""
        async with self._lock_guard:
            if strategy_id not in self._locks:
                self._locks[strategy_id] = asyncio.Lock()
            return self._locks[strategy_id]

    async def _record_duration(self, operation: str, duration_ms: float) -> None:
        """记录操作耗时"""
        if operation not in self._metrics:
            self._metrics[operation] = []
        self._metrics[operation].append(duration_ms)

    def _get_p99(self, operation: str) -> float:
        """获取P99延迟"""
        durations = self._metrics.get(operation, [])
        if not durations:
            return 0.0
        sorted_durations = sorted(durations)
        index = int(len(sorted_durations) * 0.99)
        return sorted_durations[min(index, len(sorted_durations) - 1)]

    # =========================================================================
    # 策略创建
    # =========================================================================

    async def create_strategy(
        self,
        code: str,
        strategy_id: Optional[str] = None,
        name: Optional[str] = None,
        version: str = "1.0.0",
        risk_level: RiskLevel = RiskLevel.LOW,
        config: Optional[Dict[str, Any]] = None,
    ) -> StrategyLifecycle:
        """
        从代码创建策略

        Args:
            code: 策略代码
            strategy_id: 策略ID（可选，默认自动生成）
            name: 策略名称（可选）
            version: 策略版本
            risk_level: 风险等级
            config: 策略配置

        Returns:
            StrategyLifecycle: 策略生命周期对象

        Raises:
            ValueError: 代码无效或策略ID已存在
        """
        start_time = time.monotonic()
        strategy_id = strategy_id or f"strategy_{uuid.uuid4().hex[:8]}"

        lock = await self._get_lock(strategy_id)
        async with lock:
            # 检查是否已存在
            existing = await self._store.get_lifecycle(strategy_id)
            if existing:
                raise ValueError(f"策略已存在: {strategy_id}")

            # 加载策略
            if self._hotswapper and self._hotswapper._loader:
                plugin = await self._hotswapper._loader.load_from_code(
                    code=code,
                    strategy_id=strategy_id,
                    version=version,
                )
            else:
                # 使用简单的代码执行加载
                plugin = await self._load_strategy_from_code(
                    code, strategy_id, version, risk_level
                )

            # 创建生命周期
            lifecycle = StrategyLifecycle(
                strategy_id=strategy_id,
                strategy=plugin,
                status=LifecycleStatus.DRAFT,
                version=version,
                metadata={
                    "name": name or getattr(plugin, 'name', strategy_id),
                    "risk_level": risk_level.value,
                    "code": code,
                    "config": config or {},
                },
            )

            lifecycle._add_event(
                LifecycleEventType.CREATED,
                None,
                LifecycleStatus.DRAFT,
                metadata={"risk_level": risk_level.value},
            )

            await self._store.save_lifecycle(lifecycle)

            duration_ms = (time.monotonic() - start_time) * 1000
            await self._record_duration("create_strategy", duration_ms)

            logger.info(f"策略创建成功: {strategy_id} v{version}")
            return lifecycle

    async def _load_strategy_from_code(
        self,
        code: str,
        strategy_id: str,
        version: str,
        risk_level: RiskLevel,
    ) -> StrategyPlugin:
        """从代码加载策略（简单实现）"""
        namespace: Dict[str, Any] = {
            '__name__': f'strategy_{strategy_id}',
            '__builtins__': __builtins__,
            'RiskLevel': RiskLevel,
            'StrategyResourceLimits': StrategyResourceLimits,
            'Decimal': Decimal,
            'datetime': datetime,
            'Signal': Signal,
            'SignalType': getattr(__import__('trader.core.domain.models.signal', fromlist=['SignalType']), 'SignalType'),
            'MarketData': MarketData,
            'MarketDataType': getattr(__import__('trader.core.application.strategy_protocol', fromlist=['MarketDataType']), 'MarketDataType'),
            'ValidationResult': ValidationResult,
            'ValidationStatus': ValidationStatus,
            'ValidationError': ValidationError,
        }

        try:
            compiled = compile(code, '<string>', 'exec')
            exec(compiled, namespace)

            if 'get_plugin' not in namespace:
                raise ValueError(f"代码缺少 get_plugin() 函数")

            plugin = namespace['get_plugin']()

            # 设置属性
            if hasattr(plugin, 'strategy_id'):
                plugin.strategy_id = strategy_id
            if hasattr(plugin, 'version'):
                plugin.version = version

            return plugin

        except Exception as e:
            logger.error(f"策略代码加载失败: {strategy_id}, 错误: {e}")
            raise

    # =========================================================================
    # 策略验证
    # =========================================================================

    async def validate_strategy(self, lifecycle: StrategyLifecycle) -> ValidationOutcome:
        """
        验证策略有效性

        Args:
            lifecycle: 策略生命周期

        Returns:
            ValidationOutcome: 验证结果

        Raises:
            ValueError: 状态不允许验证
        """
        start_time = time.monotonic()

        if lifecycle.status != LifecycleStatus.DRAFT:
            if lifecycle.status != LifecycleStatus.VALIDATED:
                raise ValueError(f"当前状态不允许验证: {lifecycle.status}")

        lock = await self._get_lock(lifecycle.strategy_id)
        async with lock:
            # 调用策略的validate方法
            validation_result = None
            try:
                if hasattr(lifecycle.strategy, 'validate'):
                    validation_result = lifecycle.strategy.validate()
                else:
                    validation_result = ValidationResult.valid()

                success = validation_result.is_valid

                if success:
                    lifecycle._transition_to(
                        LifecycleStatus.VALIDATED,
                        LifecycleEventType.VALIDATED,
                        metadata={"validation_result": str(validation_result.status)},
                    )
                else:
                    lifecycle._transition_to(
                        LifecycleStatus.FAILED,
                        LifecycleEventType.VALIDATION_FAILED,
                        error=str(validation_result.errors),
                    )

                await self._store.save_lifecycle(lifecycle)

            except Exception as e:
                lifecycle._transition_to(
                    LifecycleStatus.FAILED,
                    LifecycleEventType.VALIDATION_FAILED,
                    error=str(e),
                )
                await self._store.save_lifecycle(lifecycle)
                success = False

            duration_ms = (time.monotonic() - start_time) * 1000
            await self._record_duration("validate_strategy", duration_ms)

            return ValidationOutcome(
                success=success,
                status=lifecycle.status,
                validation_result=validation_result,
                duration_ms=duration_ms,
            )

    # =========================================================================
    # 策略回测
    # =========================================================================

    async def run_backtest(
        self,
        lifecycle: StrategyLifecycle,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        initial_capital: Decimal = Decimal("10000"),
    ) -> BacktestOutcome:
        """
        运行策略回测

        Args:
            lifecycle: 策略生命周期
            start_time: 回测开始时间（可选，默认30天前）
            end_time: 回测结束时间（可选，默认当前时间）
            initial_capital: 初始资金

        Returns:
            BacktestOutcome: 回测结果

        Raises:
            ValueError: 状态不允许回测
        """
        start_time_ms = time.monotonic()

        if lifecycle.status != LifecycleStatus.VALIDATED:
            raise ValueError(f"当前状态不允许回测: {lifecycle.status}")

        lock = await self._get_lock(lifecycle.strategy_id)
        async with lock:
            # 记录回测开始
            lifecycle._transition_to(
                LifecycleStatus.VALIDATED,  # 暂时保持VALIDATED状态
                LifecycleEventType.BACKTEST_STARTED,
                metadata={"start_time": str(start_time), "end_time": str(end_time)},
            )
            await self._store.save_lifecycle(lifecycle)

            try:
                # 配置回测
                config = BacktestConfig(
                    initial_capital=initial_capital,
                    symbols=["BTCUSDT"],
                    interval="1h",
                )

                # 运行回测
                from datetime import datetime as _dt
                backtest_start: _dt
                if start_time is None:
                    backtest_start = _timedelta(days=30)
                else:
                    backtest_start = start_time
                report = await self._evaluator.run_backtest(
                    strategy=lifecycle.strategy,
                    start_time=backtest_start,
                    end_time=end_time or datetime.now(timezone.utc),
                    strategy_id=lifecycle.strategy_id,
                )

                # 保存回测报告
                lifecycle.backtest_report = report
                lifecycle._transition_to(
                    LifecycleStatus.BACKTESTED,
                    LifecycleEventType.BACKTEST_COMPLETED,
                    metadata={
                        "sharpe_ratio": report.metrics.sharpe_ratio,
                        "max_drawdown": float(report.metrics.max_drawdown),
                        "win_rate": report.metrics.win_rate,
                        "return_percent": report.return_percent,
                    },
                )
                await self._store.save_lifecycle(lifecycle)

                duration_ms = (time.monotonic() - start_time_ms) * 1000
                await self._record_duration("run_backtest", duration_ms)

                return BacktestOutcome(
                    success=True,
                    status=lifecycle.status,
                    report=report,
                    duration_ms=duration_ms,
                )

            except Exception as e:
                lifecycle._transition_to(
                    LifecycleStatus.FAILED,
                    LifecycleEventType.BACKTEST_FAILED,
                    error=str(e),
                )
                await self._store.save_lifecycle(lifecycle)

                duration_ms = (time.monotonic() - start_time_ms) * 1000
                await self._record_duration("run_backtest", duration_ms)

                return BacktestOutcome(
                    success=False,
                    status=lifecycle.status,
                    error=str(e),
                    duration_ms=duration_ms,
                )

    # =========================================================================
    # 策略审批
    # =========================================================================

    async def approve_strategy(
        self,
        lifecycle: StrategyLifecycle,
        approved_by: str = "system",
        notes: Optional[str] = None,
        auto_approve: bool = False,
    ) -> ApprovalOutcome:
        """
        审批策略

        Args:
            lifecycle: 策略生命周期
            approved_by: 审批人
            notes: 审批备注
            auto_approve: 是否自动审批（基于回测结果）

        Returns:
            ApprovalOutcome: 审批结果

        Raises:
            ValueError: 状态不允许审批
        """
        start_time = time.monotonic()

        if lifecycle.status != LifecycleStatus.BACKTESTED:
            raise ValueError(f"当前状态不允许审批: {lifecycle.status}")

        lock = await self._get_lock(lifecycle.strategy_id)
        async with lock:
            # 自动审批条件检查
            should_auto_approve = auto_approve
            if auto_approve and lifecycle.backtest_report:
                metrics = lifecycle.backtest_report.metrics
                should_auto_approve = (
                    metrics.sharpe_ratio >= 1.0 and
                    metrics.win_rate >= 0.4 and
                    metrics.max_drawdown < initial_capital * Decimal("0.2")
                )

            if should_auto_approve:
                lifecycle.approval_record = {
                    "approved_by": approved_by,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "notes": notes or "自动审批通过",
                    "auto_approved": True,
                }
                lifecycle._transition_to(
                    LifecycleStatus.APPROVED,
                    LifecycleEventType.APPROVED,
                    metadata={"auto_approved": True},
                )
                success = True
            else:
                lifecycle.approval_record = {
                    "approved_by": approved_by,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "notes": notes,
                    "auto_approved": False,
                    "status": "pending",
                }
                lifecycle._transition_to(
                    LifecycleStatus.BACKTESTED,
                    LifecycleEventType.APPROVAL_REQUESTED,
                    metadata={"approved_by": approved_by},
                )
                success = False

            await self._store.save_lifecycle(lifecycle)

            duration_ms = (time.monotonic() - start_time) * 1000
            await self._record_duration("approve_strategy", duration_ms)

            return ApprovalOutcome(
                success=success,
                status=lifecycle.status,
                approved_by=approved_by,
                approval_notes=notes,
                duration_ms=duration_ms,
            )

    # =========================================================================
    # 策略启动
    # =========================================================================

    async def start_strategy(self, lifecycle: StrategyLifecycle) -> StartOutcome:
        """
        启动策略

        Args:
            lifecycle: 策略生命周期

        Returns:
            StartOutcome: 启动结果

        Raises:
            ValueError: 状态不允许启动
        """
        start_time = time.monotonic()

        if lifecycle.status != LifecycleStatus.APPROVED:
            raise ValueError(f"当前状态不允许启动: {lifecycle.status}")

        # KillSwitch检查
        if self._killswitch_callback:
            ks_level = self._killswitch_callback()
            if ks_level >= KillSwitchLevel.L1_NO_NEW_POSITIONS:
                return StartOutcome(
                    success=False,
                    status=lifecycle.status,
                    error=f"KillSwitchLevel is {ks_level.name}, cannot start strategy",
                    duration_ms=(time.monotonic() - start_time) * 1000,
                )

        lock = await self._get_lock(lifecycle.strategy_id)
        async with lock:
            try:
                if self._runner:
                    # 使用真实运行器
                    config = lifecycle.metadata.get("config", {})
                    await self._runner.load_strategy(
                        strategy_id=lifecycle.strategy_id,
                        plugin=lifecycle.strategy,
                        config=config,
                    )
                    runtime_info = await self._runner.start_strategy(lifecycle.strategy_id)
                else:
                    runtime_info = {"status": "simulated", "strategy_id": lifecycle.strategy_id}

                lifecycle._transition_to(
                    LifecycleStatus.RUNNING,
                    LifecycleEventType.STARTED,
                    metadata={"runtime_info": str(runtime_info)},
                )
                await self._store.save_lifecycle(lifecycle)

                duration_ms = (time.monotonic() - start_time) * 1000
                await self._record_duration("start_strategy", duration_ms)

                return StartOutcome(
                    success=True,
                    status=lifecycle.status,
                    runtime_info=runtime_info,
                    duration_ms=duration_ms,
                )

            except Exception as e:
                lifecycle._transition_to(
                    LifecycleStatus.FAILED,
                    LifecycleEventType.ERROR,
                    error=str(e),
                )
                await self._store.save_lifecycle(lifecycle)

                duration_ms = (time.monotonic() - start_time) * 1000
                await self._record_duration("start_strategy", duration_ms)

                return StartOutcome(
                    success=False,
                    status=lifecycle.status,
                    error=str(e),
                    duration_ms=duration_ms,
                )

    # =========================================================================
    # 策略停止
    # =========================================================================

    async def stop_strategy(
        self,
        lifecycle: StrategyLifecycle,
        unload: bool = False,
    ) -> StopOutcome:
        """
        停止策略

        Args:
            lifecycle: 策略生命周期
            unload: 是否在停止后卸载策略并释放资源（默认False，仅停止执行）

        Returns:
            StopOutcome: 停止结果
        """
        start_time = time.monotonic()

        if lifecycle.status != LifecycleStatus.RUNNING:
            raise ValueError(f"当前状态不允许停止: {lifecycle.status}")

        lock = await self._get_lock(lifecycle.strategy_id)
        async with lock:
            try:
                if self._runner:
                    # 使用真实运行器
                    await self._runner.stop_strategy(lifecycle.strategy_id)

                # 收集最终指标
                final_metrics = lifecycle.current_metrics

                lifecycle._transition_to(
                    LifecycleStatus.STOPPED,
                    LifecycleEventType.STOPPED,
                    metadata={
                        "final_metrics": str(final_metrics) if final_metrics else None,
                        "unload": unload,
                    },
                )
                await self._store.save_lifecycle(lifecycle)

                duration_ms = (time.monotonic() - start_time) * 1000
                await self._record_duration("stop_strategy", duration_ms)

                # 如果需要卸载，释放资源
                resource_unloaded = False
                if unload:
                    try:
                        if self._runner:
                            await self._runner.unload_strategy(lifecycle.strategy_id)
                            resource_unloaded = True
                        logger.info(f"策略已卸载并释放资源: {lifecycle.strategy_id}")
                    except Exception as e:
                        logger.error(f"卸载策略失败: {lifecycle.strategy_id}, 错误: {e}")

                return StopOutcome(
                    success=True,
                    status=lifecycle.status,
                    final_metrics=final_metrics,
                    duration_ms=duration_ms,
                    resource_unloaded=resource_unloaded,
                )

            except Exception as e:
                lifecycle._transition_to(
                    LifecycleStatus.FAILED,
                    LifecycleEventType.ERROR,
                    error=str(e),
                )
                await self._store.save_lifecycle(lifecycle)

                duration_ms = (time.monotonic() - start_time) * 1000
                await self._record_duration("stop_strategy", duration_ms)

                return StopOutcome(
                    success=False,
                    status=lifecycle.status,
                    error=str(e),
                    duration_ms=duration_ms,
                )

    # =========================================================================
    # 策略参数更新
    # =========================================================================

    async def update_strategy_params(
        self,
        lifecycle: StrategyLifecycle,
        new_config: Dict[str, Any],
    ) -> UpdateParamsOutcome:
        """
        更新策略参数

        允许在策略运行期间动态调整参数，无需停止策略。
        参数变更会记录到生命周期事件历史。

        Args:
            lifecycle: 策略生命周期
            new_config: 新的配置参数（部分更新，支持增量更新）

        Returns:
            UpdateParamsOutcome: 更新结果

        Raises:
            ValueError: 状态不允许更新参数
        """
        start_time = time.monotonic()

        # RUNNING 和 STOPPED 状态下都可以更新参数
        if lifecycle.status not in (LifecycleStatus.RUNNING, LifecycleStatus.STOPPED):
            raise ValueError(f"当前状态不允许更新参数: {lifecycle.status}")

        lock = await self._get_lock(lifecycle.strategy_id)
        async with lock:
            try:
                # 获取当前配置并合并新配置
                current_config = lifecycle.metadata.get("config", {})
                merged_config = {**current_config, **new_config}

                # 调用策略的 update_config 方法
                validation_result: ValidationResult | None = None
                if hasattr(lifecycle.strategy, 'update_config'):
                    update_method = getattr(lifecycle.strategy, 'update_config')
                    if asyncio.iscoroutinefunction(update_method):
                        validation_result = await update_method(new_config)
                    else:
                        validation_result = update_method(new_config)
                elif hasattr(lifecycle.strategy, 'initialize'):
                    # 后备：调用 initialize 重新初始化
                    await lifecycle.strategy.initialize(merged_config)
                    validation_result = ValidationResult.valid()

                # 检查验证结果
                if validation_result and not validation_result.is_valid:
                    return UpdateParamsOutcome(
                        success=False,
                        status=lifecycle.status,
                        validation_result=validation_result,
                        error=f"参数验证失败: {[e.message for e in validation_result.errors]}",
                        duration_ms=(time.monotonic() - start_time) * 1000,
                    )

                # 更新元数据中的配置
                lifecycle.metadata["config"] = merged_config

                # 记录生命周期事件
                lifecycle._transition_to(
                    lifecycle.status,  # 状态不变
                    LifecycleEventType.PARAMS_UPDATED,
                    metadata={
                        "updated_keys": list(new_config.keys()),
                        "new_config": new_config,
                    },
                )
                await self._store.save_lifecycle(lifecycle)

                duration_ms = (time.monotonic() - start_time) * 1000
                await self._record_duration("update_strategy_params", duration_ms)

                return UpdateParamsOutcome(
                    success=True,
                    status=lifecycle.status,
                    updated_config=merged_config,
                    validation_result=validation_result,
                    duration_ms=duration_ms,
                )

            except Exception as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                await self._record_duration("update_strategy_params", duration_ms)

                return UpdateParamsOutcome(
                    success=False,
                    status=lifecycle.status,
                    error=str(e),
                    duration_ms=duration_ms,
                )

    # =========================================================================
    # 策略热插拔
    # =========================================================================

    async def swap_strategy(
        self,
        old_lifecycle: StrategyLifecycle,
        new_code: str,
        new_version: Optional[str] = None,
    ) -> SwapOutcome:
        """
        热插拔更新策略

        Args:
            old_lifecycle: 旧策略生命周期
            new_code: 新策略代码
            new_version: 新策略版本

        Returns:
            SwapOutcome: 热插拔结果
        """
        start_time = time.monotonic()

        if old_lifecycle.status != LifecycleStatus.RUNNING:
            raise ValueError(f"当前状态不允许热插拔: {old_lifecycle.status}")

        new_version = new_version or f"{old_lifecycle.version}+1"

        lock = await self._get_lock(old_lifecycle.strategy_id)
        async with lock:
            # 记录热插拔开始
            old_lifecycle._transition_to(
                LifecycleStatus.RUNNING,
                LifecycleEventType.HOTSWAP_STARTED,
                metadata={"new_version": new_version},
            )
            await self._store.save_lifecycle(old_lifecycle)

            try:
                if self._hotswapper:
                    # 使用真实热插拔管理器
                    # TODO: 需要从 new_code 构建 StrategyPlugin
                    # 目前使用模拟策略进行热插拔测试
                    from trader.tests.fakes.fake_strategy import FakeStrategyPlugin
                    mock_strategy = FakeStrategyPlugin()
                    
                    swap_result = await self._hotswapper.swap(
                        new_strategy=mock_strategy,
                        force=True,
                    )

                    if swap_result.success:
                        # 更新旧策略状态
                        old_lifecycle._transition_to(
                            LifecycleStatus.STOPPED,
                            LifecycleEventType.HOTSWAP_COMPLETED,
                            metadata={"swap_result": str(swap_result.state)},
                        )
                        # 创建新策略生命周期
                        new_lifecycle = await self.create_strategy(
                            code=new_code,
                            strategy_id=f"{old_lifecycle.strategy_id}_v{new_version}",
                            version=new_version,
                        )
                        new_lifecycle._transition_to(
                            LifecycleStatus.RUNNING,
                            LifecycleEventType.STARTED,
                            metadata={"swapped_from": old_lifecycle.strategy_id},
                        )

                        await self._store.save_lifecycle(old_lifecycle)
                        await self._store.save_lifecycle(new_lifecycle)

                        duration_ms = (time.monotonic() - start_time) * 1000
                        await self._record_duration("swap_strategy", duration_ms)

                        return SwapOutcome(
                            success=True,
                            old_status=LifecycleStatus.STOPPED,
                            new_status=LifecycleStatus.RUNNING,
                            swap_result=swap_result,
                            duration_ms=duration_ms,
                        )
                    else:
                        # 回滚
                        old_lifecycle._transition_to(
                            LifecycleStatus.RUNNING,
                            LifecycleEventType.HOTSWAP_ROLLBACK,
                            metadata={"error": str(swap_result.error)},
                        )
                        await self._store.save_lifecycle(old_lifecycle)

                        duration_ms = (time.monotonic() - start_time) * 1000
                        await self._record_duration("swap_strategy", duration_ms)

                        return SwapOutcome(
                            success=False,
                            old_status=LifecycleStatus.RUNNING,
                            new_status=LifecycleStatus.RUNNING,
                            swap_result=swap_result,
                            error=str(swap_result.error) if swap_result.error else "Unknown error",
                            duration_ms=duration_ms,
                        )
                else:
                    # 简单模拟热插拔（无热插拔管理器时）
                    old_lifecycle._transition_to(
                        LifecycleStatus.STOPPED,
                        LifecycleEventType.HOTSWAP_COMPLETED,
                        metadata={"simulated": True},
                    )
                    await self._store.save_lifecycle(old_lifecycle)

                    new_lifecycle = await self.create_strategy(
                        code=new_code,
                        strategy_id=f"{old_lifecycle.strategy_id}_v{new_version}",
                        version=new_version,
                    )

                    duration_ms = (time.monotonic() - start_time) * 1000
                    await self._record_duration("swap_strategy", duration_ms)

                    return SwapOutcome(
                        success=True,
                        old_status=LifecycleStatus.STOPPED,
                        new_status=LifecycleStatus.RUNNING,
                        duration_ms=duration_ms,
                    )

            except Exception as e:
                old_lifecycle._transition_to(
                    LifecycleStatus.FAILED,
                    LifecycleEventType.HOTSWAP_ROLLBACK,
                    error=str(e),
                )
                await self._store.save_lifecycle(old_lifecycle)

                duration_ms = (time.monotonic() - start_time) * 1000
                await self._record_duration("swap_strategy", duration_ms)

                return SwapOutcome(
                    success=False,
                    old_status=LifecycleStatus.RUNNING,
                    new_status=LifecycleStatus.RUNNING,
                    error=str(e),
                    duration_ms=duration_ms,
                )

    # =========================================================================
    # 生命周期查询
    # =========================================================================

    async def get_lifecycle(self, strategy_id: str) -> Optional[StrategyLifecycle]:
        """获取策略生命周期"""
        return await self._store.get_lifecycle(strategy_id)

    async def list_lifecycles(
        self, status: Optional[LifecycleStatus] = None
    ) -> List[StrategyLifecycle]:
        """列出策略生命周期"""
        return await self._store.list_lifecycles(status)

    async def get_metrics_summary(self) -> Dict[str, Any]:
        """获取性能指标摘要"""
        return {
            operation: {
                "count": len(durations),
                "p99_ms": self._get_p99(operation),
                "avg_ms": sum(durations) / len(durations) if durations else 0,
                "max_ms": max(durations) if durations else 0,
            }
            for operation, durations in self._metrics.items()
        }


# ============================================================================
# 辅助函数
# ============================================================================


def _timedelta(days: int = 0, hours: int = 0, minutes: int = 0, seconds: int = 0) -> datetime:
    """创建过去的时间点（简单的datetime辅助函数）"""
    from datetime import timedelta as td
    return datetime.now(timezone.utc) - td(days=days, hours=hours, minutes=minutes, seconds=seconds)


# 初始资金常量（用于类型注解）
initial_capital: Decimal = Decimal("10000")
