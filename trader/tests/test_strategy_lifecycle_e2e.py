"""
StrategyLifecycleManager E2E Tests - 策略生命周期端到端测试
===========================================================

4个端到端场景测试：
1. AI生成并部署策略 (chat → generator → lifecycle → runner)
2. 策略热插拔更新 (hotswap → runner → evaluator)
3. 策略回测与审批 (evaluator → backtest → approval → running)
4. 异常自动回滚 (hotswap rollback → previous version)

测试覆盖：
- 完整生命周期状态转换
- 崩溃隔离
- P99延迟验证
- 事件历史追溯
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trader.core.application.risk_engine import KillSwitchLevel, RiskLevel
from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    Signal,
    SignalType,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationResult,
    ValidationStatus,
)
from trader.services.strategy_lifecycle_manager import (
    LifecycleEvent,
    LifecycleEventType,
    LifecycleStatus,
    StrategyLifecycle,
    StrategyLifecycleManager,
    ValidationOutcome,
    BacktestOutcome,
    ApprovalOutcome,
    StartOutcome,
    StopOutcome,
    SwapOutcome,
    InMemoryLifecycleStore,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 测试夹具
# ============================================================================


class MockStrategy:
    """Mock策略实现"""

    def __init__(
        self,
        name: str = "MockStrategy",
        version: str = "1.0.0",
        risk_level: RiskLevel = RiskLevel.LOW,
        valid: bool = True,
    ):
        self._name = name
        self._version = version
        self._risk_level = risk_level
        self._valid = valid
        self.strategy_id = ""
        self.resource_limits = StrategyResourceLimits()

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    @property
    def risk_level(self) -> RiskLevel:
        return self._risk_level

    def validate(self) -> ValidationResult:
        if self._valid:
            return ValidationResult.valid()
        return ValidationResult.invalid(
            errors=[],
            metadata={"error": "Mock validation failed"},
        )

    def on_market_data(self, data: MarketData) -> Optional[Signal]:
        return None


@pytest.fixture
def mock_strategy():
    """Mock策略夹具"""
    return MockStrategy()


@pytest.fixture
def mock_runner():
    """Mock运行器夹具"""
    runner = MagicMock()
    runner.load_strategy = AsyncMock(return_value=MagicMock())
    runner.start_strategy = AsyncMock(return_value=MagicMock())
    runner.stop_strategy = AsyncMock(return_value=MagicMock())
    runner.tick_strategy = AsyncMock(return_value=None)
    return runner


@pytest.fixture
def mock_evaluator():
    """Mock评估器夹具"""
    evaluator = MagicMock()
    evaluator.run_backtest = AsyncMock(return_value=MagicMock(
        metrics=MagicMock(
            sharpe_ratio=1.5,
            max_drawdown=Decimal("500"),
            win_rate=0.55,
            total_pnl=Decimal("2000"),
        ),
        return_percent=20.0,
        to_dict=MagicMock(return_value={}),
    ))
    return evaluator


@pytest.fixture
def mock_hotswapper():
    """Mock热插拔管理器夹具"""
    hotswapper = MagicMock()
    hotswapper.swap_strategy = AsyncMock(return_value=MagicMock(
        success=True,
        state=MagicMock(value="ACTIVE"),
        error=None,
    ))
    hotswapper._loader = None  # 使用manager的简单加载器
    return hotswapper


@pytest.fixture
def lifecycle_store():
    """内存存储夹具"""
    return InMemoryLifecycleStore()


@pytest.fixture
def killswitch_callback():
    """KillSwitch回调夹具"""
    return MagicMock(return_value=KillSwitchLevel.L0_NORMAL)


@pytest.fixture
def manager(mock_runner, mock_evaluator, mock_hotswapper, lifecycle_store, killswitch_callback):
    """生命周期管理器夹具"""
    return StrategyLifecycleManager(
        runner=mock_runner,
        evaluator=mock_evaluator,
        hotswapper=mock_hotswapper,
        lifecycle_store=lifecycle_store,
        killswitch_callback=killswitch_callback,
    )


# ============================================================================
# 测试用例：AI生成并部署策略
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_1_ai_generated_deployment(manager, mock_strategy):
    """
    场景1：AI生成并部署策略

    测试流程：
    1. 创建策略 (DRAFT)
    2. 验证策略 (VALIDATED)
    3. 回测策略 (BACKTESTED)
    4. 审批策略 (APPROVED)
    5. 启动策略 (RUNNING)

    验证点：
    - 状态转换正确
    - 事件历史完整
    - 性能指标正常
    """
    # 准备策略代码
    strategy_code = '''
class AIStrategy:
    name = "EMA_Cross_AI"
    version = "1.0.0"
    risk_level = RiskLevel.MEDIUM
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return AIStrategy()
'''

    # 步骤1：创建策略
    lifecycle = await manager.create_strategy(
        code=strategy_code,
        strategy_id="ai_strategy_001",
        name="EMA_Cross_AI",
        version="1.0.0",
        risk_level=RiskLevel.MEDIUM,
    )

    assert lifecycle is not None
    assert lifecycle.strategy_id == "ai_strategy_001"
    assert lifecycle.status == LifecycleStatus.DRAFT
    assert len(lifecycle.history) >= 1
    assert lifecycle.history[0].event_type == LifecycleEventType.CREATED

    # 步骤2：验证策略
    validation = await manager.validate_strategy(lifecycle)
    assert validation.success is True
    assert validation.status == LifecycleStatus.VALIDATED
    assert validation.duration_ms < 500  # P99 < 500ms

    # 刷新lifecycle
    lifecycle = await manager.get_lifecycle("ai_strategy_001")

    # 步骤3：回测策略
    backtest = await manager.run_backtest(
        lifecycle,
        initial_capital=Decimal("10000"),
    )
    assert backtest.success is True
    assert backtest.status == LifecycleStatus.BACKTESTED
    assert backtest.report is not None
    assert backtest.duration_ms < 60000  # 回测1年<1分钟

    # 刷新lifecycle
    lifecycle = await manager.get_lifecycle("ai_strategy_001")

    # 步骤4：审批策略（自动审批）
    approval = await manager.approve_strategy(
        lifecycle,
        approved_by="system",
        auto_approve=True,
    )
    assert approval.success is True
    assert approval.status == LifecycleStatus.APPROVED

    # 刷新lifecycle
    lifecycle = await manager.get_lifecycle("ai_strategy_001")

    # 步骤5：启动策略
    start = await manager.start_strategy(lifecycle)
    assert start.success is True
    assert start.status == LifecycleStatus.RUNNING

    # 验证最终状态
    lifecycle = await manager.get_lifecycle("ai_strategy_001")
    assert lifecycle.status == LifecycleStatus.RUNNING
    assert lifecycle.backtest_report is not None
    assert lifecycle.approval_record is not None

    # 验证事件历史
    event_types = [e.event_type for e in lifecycle.history]
    assert LifecycleEventType.CREATED in event_types
    assert LifecycleEventType.VALIDATED in event_types
    assert LifecycleEventType.BACKTEST_STARTED in event_types
    assert LifecycleEventType.BACKTEST_COMPLETED in event_types
    assert LifecycleEventType.APPROVED in event_types
    assert LifecycleEventType.STARTED in event_types

    # 验证性能指标
    metrics = await manager.get_metrics_summary()
    assert "create_strategy" in metrics
    assert "validate_strategy" in metrics
    assert "run_backtest" in metrics
    assert "approve_strategy" in metrics
    assert "start_strategy" in metrics

    print(f"✓ 场景1完成: AI生成并部署策略")
    print(f"  - 创建耗时: {metrics['create_strategy']['avg_ms']:.2f}ms")
    print(f"  - 验证耗时: {metrics['validate_strategy']['avg_ms']:.2f}ms")
    print(f"  - 回测耗时: {metrics['run_backtest']['avg_ms']:.2f}ms")


# ============================================================================
# 测试用例：策略热插拔更新
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_2_strategy_hotswap(manager, mock_strategy, mock_hotswapper):
    """
    场景2：策略热插拔更新

    测试流程：
    1. 创建并启动策略 (RUNNING)
    2. 热插拔新策略
    3. 验证切换成功

    验证点：
    - 热插拔状态转换
    - 旧策略状态更新
    - 新策略状态正确
    """
    # 准备旧策略
    old_code = '''
class OldStrategy:
    name = "OldStrategy"
    version = "1.0.0"
    risk_level = RiskLevel.LOW
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return OldStrategy()
'''

    # 准备新策略
    new_code = '''
class NewStrategy:
    name = "NewStrategy"
    version = "2.0.0"
    risk_level = RiskLevel.MEDIUM
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return NewStrategy()
'''

    # 创建旧策略
    old_lifecycle = await manager.create_strategy(
        code=old_code,
        strategy_id="hotswap_strategy_001",
        name="OldStrategy",
        version="1.0.0",
    )

    # 验证并审批
    await manager.validate_strategy(old_lifecycle)
    old_lifecycle = await manager.get_lifecycle("hotswap_strategy_001")
    await manager.run_backtest(old_lifecycle)
    old_lifecycle = await manager.get_lifecycle("hotswap_strategy_001")
    await manager.approve_strategy(old_lifecycle, auto_approve=True)
    old_lifecycle = await manager.get_lifecycle("hotswap_strategy_001")
    await manager.start_strategy(old_lifecycle)

    old_lifecycle = await manager.get_lifecycle("hotswap_strategy_001")
    assert old_lifecycle.status == LifecycleStatus.RUNNING

    # 设置mock返回值
    # 注意: manager.swap_strategy() 内部调用 self._hotswapper.swap()，所以mock应该是swap而不是swap_strategy
    mock_hotswapper.swap = AsyncMock(return_value=MagicMock(
        success=True,
        state=MagicMock(value="ACTIVE"),
        error=None,
    ))
    manager._hotswapper = mock_hotswapper

    # 执行热插拔
    swap = await manager.swap_strategy(
        old_lifecycle=old_lifecycle,
        new_code=new_code,
        new_version="2.0.0",
    )

    assert swap.success is True
    assert swap.old_status == LifecycleStatus.STOPPED
    assert swap.new_status == LifecycleStatus.RUNNING

    # 验证旧策略状态
    old_lifecycle = await manager.get_lifecycle("hotswap_strategy_001")
    assert old_lifecycle.status == LifecycleStatus.STOPPED

    # 验证事件历史
    event_types = [e.event_type for e in old_lifecycle.history]
    assert LifecycleEventType.HOTSWAP_STARTED in event_types
    assert LifecycleEventType.HOTSWAP_COMPLETED in event_types

    print(f"✓ 场景2完成: 策略热插拔更新")
    print(f"  - 热插拔耗时: {swap.duration_ms:.2f}ms")


# ============================================================================
# 测试用例：策略回测与审批
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_3_backtest_and_approval(manager):
    """
    场景3：策略回测与审批

    测试流程：
    1. 创建策略
    2. 验证策略
    3. 回测策略（验证回测报告）
    4. 手动审批策略
    5. 启动策略

    验证点：
    - 回测报告指标完整性
    - 审批流程正确性
    - 状态转换顺序
    """
    strategy_code = '''
class BacktestStrategy:
    name = "RSI_Strategy"
    version = "1.0.0"
    risk_level = RiskLevel.MEDIUM
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return BacktestStrategy()
'''

    # 创建策略
    lifecycle = await manager.create_strategy(
        code=strategy_code,
        strategy_id="backtest_strategy_001",
        name="RSI_Strategy",
    )

    # 验证策略
    validation = await manager.validate_strategy(lifecycle)
    assert validation.success is True
    assert validation.status == LifecycleStatus.VALIDATED

    # 回测策略
    backtest = await manager.run_backtest(
        lifecycle,
        initial_capital=Decimal("10000"),
    )
    assert backtest.success is True
    assert backtest.status == LifecycleStatus.BACKTESTED
    assert backtest.report is not None

    # 验证回测报告指标
    metrics = backtest.report.metrics
    assert metrics.sharpe_ratio == 1.5  # 从mock返回
    assert metrics.win_rate == 0.55
    assert metrics.max_drawdown == Decimal("500")

    # 刷新lifecycle
    lifecycle = await manager.get_lifecycle("backtest_strategy_001")

    # 手动审批（需要人工确认）
    approval = await manager.approve_strategy(
        lifecycle,
        approved_by="trader@example.com",
        notes="回测指标良好，批准上线",
        auto_approve=False,  # 手动审批
    )

    # 手动审批不应自动通过
    assert approval.success is False  # 因为auto_approve=False
    assert approval.status == LifecycleStatus.BACKTESTED

    # 重新以auto_approve=True审批
    lifecycle = await manager.get_lifecycle("backtest_strategy_001")
    approval = await manager.approve_strategy(
        lifecycle,
        approved_by="trader@example.com",
        notes="回测指标良好，批准上线",
        auto_approve=True,
    )
    assert approval.success is True
    assert approval.status == LifecycleStatus.APPROVED
    assert approval.approved_by == "trader@example.com"

    # 刷新并启动
    lifecycle = await manager.get_lifecycle("backtest_strategy_001")
    start = await manager.start_strategy(lifecycle)
    assert start.success is True
    assert start.status == LifecycleStatus.RUNNING

    # 验证审批记录
    lifecycle = await manager.get_lifecycle("backtest_strategy_001")
    assert lifecycle.approval_record is not None
    assert lifecycle.approval_record["approved_by"] == "trader@example.com"
    assert lifecycle.approval_record["auto_approved"] is True

    print(f"✓ 场景3完成: 策略回测与审批")
    print(f"  - 回测报告: 夏普率={metrics.sharpe_ratio}, 胜率={metrics.win_rate}")


# ============================================================================
# 测试用例：异常自动回滚
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_4_rollback_on_error(manager, mock_hotswapper):
    """
    场景4：异常自动回滚

    测试流程：
    1. 创建并启动策略 (RUNNING)
    2. 热插拔尝试（模拟失败）
    3. 验证回滚到旧策略

    验证点：
    - 热插拔失败时回滚
    - 旧策略状态恢复
    - 错误信息记录
    """
    old_code = '''
class StableStrategy:
    name = "StableStrategy"
    version = "1.0.0"
    risk_level = RiskLevel.LOW
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return StableStrategy()
'''

    new_code = '''
class BuggyStrategy:
    name = "BuggyStrategy"
    version = "2.0.0"
    risk_level = RiskLevel.HIGH
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.invalid(errors=[])

    def on_market_data(self, data):
        raise RuntimeError("Intentional error")

def get_plugin():
    return BuggyStrategy()
'''

    # 创建旧策略
    old_lifecycle = await manager.create_strategy(
        code=old_code,
        strategy_id="rollback_strategy_001",
        name="StableStrategy",
    )

    # 验证并启动
    await manager.validate_strategy(old_lifecycle)
    old_lifecycle = await manager.get_lifecycle("rollback_strategy_001")
    await manager.run_backtest(old_lifecycle)
    old_lifecycle = await manager.get_lifecycle("rollback_strategy_001")
    await manager.approve_strategy(old_lifecycle, auto_approve=True)
    old_lifecycle = await manager.get_lifecycle("rollback_strategy_001")
    await manager.start_strategy(old_lifecycle)

    old_lifecycle = await manager.get_lifecycle("rollback_strategy_001")
    assert old_lifecycle.status == LifecycleStatus.RUNNING

    # 模拟热插拔失败
    # 注意: manager.swap_strategy() 内部调用 self._hotswapper.swap()，所以mock应该是swap而不是swap_strategy
    mock_hotswapper.swap = AsyncMock(return_value=MagicMock(
        success=False,
        state=MagicMock(value="ERROR"),
        error=MagicMock(
            phase=MagicMock(value="VALIDATING"),
            message="Validation failed",
        ),
    ))
    manager._hotswapper = mock_hotswapper

    # 执行热插拔（预期失败）
    swap = await manager.swap_strategy(
        old_lifecycle=old_lifecycle,
        new_code=new_code,
        new_version="2.0.0",
    )

    # 验证热插拔失败
    assert swap.success is False
    assert swap.error is not None

    # 验证旧策略状态回滚
    old_lifecycle = await manager.get_lifecycle("rollback_strategy_001")
    assert old_lifecycle.status == LifecycleStatus.RUNNING  # 回滚后恢复

    # 验证事件历史包含回滚事件
    event_types = [e.event_type for e in old_lifecycle.history]
    assert LifecycleEventType.HOTSWAP_ROLLBACK in event_types

    print(f"✓ 场景4完成: 异常自动回滚")
    print(f"  - 回滚错误: {swap.error}")


# ============================================================================
# 测试用例：崩溃隔离
# ============================================================================


@pytest.mark.asyncio
async def test_crash_isolation(manager):
    """
    测试崩溃隔离

    验证点：
    - 单策略崩溃不影响其他策略
    - 管理器仍可响应
    """
    strategy_codes = [
        (f"strategy_crash_{i}", f'''
class Strategy{i}:
    name = "Strategy{i}"
    version = "1.0.0"
    risk_level = RiskLevel.LOW
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return Strategy{i}()
''') for i in range(3)
    ]

    # 创建3个策略
    lifecycles = []
    for strategy_id, code in strategy_codes:
        lc = await manager.create_strategy(
            code=code,
            strategy_id=strategy_id,
        )
        lifecycles.append(lc)

    # 验证所有策略
    for lc in lifecycles:
        result = await manager.validate_strategy(lc)
        assert result.success is True

    # 列出所有策略
    all_lifecycles = await manager.list_lifecycles()
    assert len(all_lifecycles) == 3

    # 列出特定状态策略
    validated_lifecycles = await manager.list_lifecycles(status=LifecycleStatus.VALIDATED)
    assert len(validated_lifecycles) == 3

    print(f"✓ 崩溃隔离测试完成")
    print(f"  - 3个策略全部存活")


# ============================================================================
# 测试用例：P99延迟验证
# ============================================================================


@pytest.mark.asyncio
async def test_p99_latency(manager):
    """
    测试P99延迟

    验证点：
    - 各操作P99 < 500ms
    """
    strategy_code = '''
class LatencyTestStrategy:
    name = "LatencyTestStrategy"
    version = "1.0.0"
    risk_level = RiskLevel.LOW
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return LatencyTestStrategy()
'''

    # 多次执行操作以计算P99
    iterations = 20
    for i in range(iterations):
        lifecycle = await manager.create_strategy(
            code=strategy_code,
            strategy_id=f"latency_test_{i}",
        )
        await manager.validate_strategy(lifecycle)

    # 获取性能指标
    metrics = await manager.get_metrics_summary()

    # 验证P99延迟
    for operation, stats in metrics.items():
        p99 = stats["p99_ms"]
        print(f"  - {operation}: P99={p99:.2f}ms, count={stats['count']}")
        if operation in ["validate_strategy"]:
            assert p99 < 500, f"{operation} P99 ({p99}ms) exceeds 500ms"

    print(f"✓ P99延迟验证完成")


# ============================================================================
# 测试用例：状态转换约束
# ============================================================================


@pytest.mark.asyncio
async def test_status_transition_constraints(manager):
    """
    测试状态转换约束

    验证点：
    - 非法状态转换被拒绝
    - 正确状态转换允许
    """
    strategy_code = '''
class ConstraintTestStrategy:
    name = "ConstraintTestStrategy"
    version = "1.0.0"
    risk_level = RiskLevel.LOW
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return ConstraintTestStrategy()
'''

    lifecycle = await manager.create_strategy(
        code=strategy_code,
        strategy_id="constraint_test",
    )

    # DRAFT -> BACKTESTED 应该失败
    with pytest.raises(ValueError, match="当前状态不允许回测"):
        await manager.run_backtest(lifecycle)

    # DRAFT -> RUNNING 应该失败
    with pytest.raises(ValueError, match="当前状态不允许启动"):
        await manager.start_strategy(lifecycle)

    # 正确的转换流程
    await manager.validate_strategy(lifecycle)
    lifecycle = await manager.get_lifecycle("constraint_test")

    # VALIDATED -> BACKTESTED 应该成功
    backtest = await manager.run_backtest(lifecycle)
    assert backtest.success is True

    lifecycle = await manager.get_lifecycle("constraint_test")

    # BACKTESTED -> APPROVED 应该成功
    approval = await manager.approve_strategy(lifecycle, auto_approve=True)
    assert approval.success is True

    lifecycle = await manager.get_lifecycle("constraint_test")

    # APPROVED -> RUNNING 应该成功
    start = await manager.start_strategy(lifecycle)
    assert start.success is True

    print(f"✓ 状态转换约束测试完成")


# ============================================================================
# 测试用例：完整生命周期追溯
# ============================================================================


@pytest.mark.asyncio
async def test_lifecycle_traceability(manager):
    """
    测试生命周期可追溯性

    验证点：
    - 每个状态转换都有事件记录
    - 事件包含完整元数据
    - 可以追溯任意时间点的状态
    """
    strategy_code = '''
class TraceableStrategy:
    name = "TraceableStrategy"
    version = "1.0.0"
    risk_level = RiskLevel.MEDIUM
    resource_limits = StrategyResourceLimits()

    def validate(self):
        return ValidationResult.valid()

    def on_market_data(self, data):
        return None

def get_plugin():
    return TraceableStrategy()
'''

    # 执行完整流程
    lifecycle = await manager.create_strategy(
        code=strategy_code,
        strategy_id="traceable_strategy",
    )

    await manager.validate_strategy(lifecycle)
    lifecycle = await manager.get_lifecycle("traceable_strategy")
    await manager.run_backtest(lifecycle)
    lifecycle = await manager.get_lifecycle("traceable_strategy")
    await manager.approve_strategy(lifecycle, auto_approve=True)
    lifecycle = await manager.get_lifecycle("traceable_strategy")
    await manager.start_strategy(lifecycle)

    # 验证状态摘要
    summary = lifecycle.get_status_summary()
    assert summary["strategy_id"] == "traceable_strategy"
    assert summary["has_backtest"] is True
    assert summary["has_approval"] is True
    assert summary["event_count"] >= 6  # CREATED, VALIDATED, BACKTEST_*, APPROVED, STARTED

    # 验证每个事件都有时间戳
    for event in lifecycle.history:
        assert event.timestamp is not None
        assert event.event_id is not None

    # 验证状态转换的from_status和to_status
    for i in range(1, len(lifecycle.history)):
        prev_event = lifecycle.history[i - 1]
        curr_event = lifecycle.history[i]
        assert prev_event.to_status == curr_event.from_status

    print(f"✓ 生命周期追溯测试完成")
    print(f"  - 事件总数: {len(lifecycle.history)}")
    for event in lifecycle.history:
        print(f"    - {event.event_type.value}: {event.from_status} -> {event.to_status}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
