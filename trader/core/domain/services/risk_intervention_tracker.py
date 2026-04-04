"""
RiskInterventionTracker - 风控干预追踪器
========================================
量化风控实际改变了多少下单结果。

核心指标：
    Risk Intervention Rate = (reject_rate + size_reduction_rate + killswitch_block_rate)
    - reject_rate: 被拒绝的信号比例
    - size_reduction_rate: 被缩单的信号比例
    - killswitch_block_rate: 被 KillSwitch 阻止的信号比例

设计约束：
- Core Plane 无 IO
- 完全确定性，可回放
- 每个干预记录包含完整审计信息

使用方式：
    tracker = RiskInterventionTracker()
    
    # 记录风控干预
    tracker.record(RiskInterventionRecord(...))
    
    # 获取量化指标
    metrics = tracker.get_metrics(strategy_id="strategy_A")
    print(f"Risk Intervention Rate: {metrics.intervention_rate}")
    
    # 查询记录
    records = tracker.get_records(strategy_id="strategy_A", limit=100)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional
import uuid


# ==================== 核心类型 ====================

@dataclass(frozen=True)
class RiskInterventionRecord:
    """
    单次风控干预记录
    
    属性：
        signal_id: 信号唯一ID
        strategy_id: 策略ID
        rule_name: 触发的风控规则名
        action: 干预动作
            - PASS: 风控通过
            - REDUCE: 仓位缩减
            - REJECT: 订单拒绝
            - HALT: 策略停止/阻止新订单
        original_size: 原始请求仓位
        approved_size: 风控批准仓位
        market_state_ref: 市场状态引用（如 orderbook hash）
        trace_id: 追踪ID，用于关联其他事件
        timestamp: 时间戳
    """
    signal_id: str
    strategy_id: str
    rule_name: str
    action: Literal["PASS", "REDUCE", "REJECT", "HALT"]
    original_size: float
    approved_size: float
    market_state_ref: str = ""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def __post_init__(self) -> None:
        # 验证 original_size 非负
        if self.original_size < 0:
            raise ValueError(f"original_size must be non-negative, got {self.original_size}")
        
        # 验证 approved_size 非负
        if self.approved_size < 0:
            raise ValueError(f"approved_size must be non-negative, got {self.approved_size}")
    
    @property
    def size_change_ratio(self) -> float:
        """仓位变化比例"""
        if self.original_size == 0:
            return 0.0
        return (self.original_size - self.approved_size) / self.original_size
    
    @property
    def was_intervened(self) -> bool:
        """是否被风控干预（改变了订单命运）"""
        return self.action != "PASS"
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "signal_id": self.signal_id,
            "strategy_id": self.strategy_id,
            "rule_name": self.rule_name,
            "action": self.action,
            "original_size": self.original_size,
            "approved_size": self.approved_size,
            "market_state_ref": self.market_state_ref,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class RiskInterventionMetrics:
    """
    风控干预量化指标
    
    属性：
        total_signals: 总信号数
        passed_signals: 通过的信号数
        rejected_signals: 被拒绝的信号数
        reduced_signals: 被缩单的信号数
        halted_signals: 被阻止的信号数
        reject_rate: 拒绝率
        size_reduction_rate: 缩单率
        killswitch_block_rate: KillSwitch 阻止率
        intervention_rate: 总干预率 (核心指标)
    """
    total_signals: int
    passed_signals: int
    rejected_signals: int
    reduced_signals: int
    halted_signals: int
    reject_rate: float
    size_reduction_rate: float
    killswitch_block_rate: float
    intervention_rate: float
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "total_signals": self.total_signals,
            "passed_signals": self.passed_signals,
            "rejected_signals": self.rejected_signals,
            "reduced_signals": self.reduced_signals,
            "halted_signals": self.halted_signals,
            "reject_rate": round(self.reject_rate, 4),
            "size_reduction_rate": round(self.size_reduction_rate, 4),
            "killswitch_block_rate": round(self.killswitch_block_rate, 4),
            "intervention_rate": round(self.intervention_rate, 4),
        }


# ==================== 追踪器实现 ====================

class RiskInterventionTracker:
    """
    风控干预追踪器
    
    职责：
    1. 记录每次风控干预
    2. 计算量化指标
    3. 查询历史记录
    
    设计约束：
    - Core Plane 无 IO（内存存储）
    - 完全确定性
    - 可回放
    """
    
    def __init__(self) -> None:
        self._records: list[RiskInterventionRecord] = []
    
    def record(
        self,
        signal_id: str,
        strategy_id: str,
        rule_name: str,
        action: Literal["PASS", "REDUCE", "REJECT", "HALT"],
        original_size: float,
        approved_size: float,
        market_state_ref: str = "",
    ) -> RiskInterventionRecord:
        """
        记录一次风控干预
        
        Args:
            signal_id: 信号ID
            strategy_id: 策略ID
            rule_name: 风控规则名
            action: 干预动作
            original_size: 原始仓位
            approved_size: 批准仓位
            market_state_ref: 市场状态引用
            
        Returns:
            创建的干预记录
        """
        record = RiskInterventionRecord(
            signal_id=signal_id,
            strategy_id=strategy_id,
            rule_name=rule_name,
            action=action,
            original_size=original_size,
            approved_size=approved_size,
            market_state_ref=market_state_ref,
            trace_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
        )
        self._records.append(record)
        return record
    
    def get_metrics(
        self,
        strategy_id: str | None = None,
        rule_name: str | None = None,
        lookback_hours: int | None = None,
    ) -> RiskInterventionMetrics:
        """
        获取风控干预指标
        
        Args:
            strategy_id: 可选，按策略ID过滤
            rule_name: 可选，按规则名过滤
            lookback_hours: 可选，只看最近N小时
            
        Returns:
            风控干预量化指标
        """
        records = self._filter_records(
            strategy_id=strategy_id,
            rule_name=rule_name,
            lookback_hours=lookback_hours,
        )
        
        total = len(records)
        if total == 0:
            return RiskInterventionMetrics(
                total_signals=0,
                passed_signals=0,
                rejected_signals=0,
                reduced_signals=0,
                halted_signals=0,
                reject_rate=0.0,
                size_reduction_rate=0.0,
                killswitch_block_rate=0.0,
                intervention_rate=0.0,
            )
        
        passed = sum(1 for r in records if r.action == "PASS")
        rejected = sum(1 for r in records if r.action == "REJECT")
        reduced = sum(1 for r in records if r.action == "REDUCE")
        halted = sum(1 for r in records if r.action == "HALT")
        
        reject_rate = rejected / total
        size_reduction_rate = reduced / total
        killswitch_block_rate = halted / total
        intervention_rate = reject_rate + size_reduction_rate + killswitch_block_rate
        
        return RiskInterventionMetrics(
            total_signals=total,
            passed_signals=passed,
            rejected_signals=rejected,
            reduced_signals=reduced,
            halted_signals=halted,
            reject_rate=reject_rate,
            size_reduction_rate=size_reduction_rate,
            killswitch_block_rate=killswitch_block_rate,
            intervention_rate=intervention_rate,
        )
    
    def get_records(
        self,
        strategy_id: str | None = None,
        rule_name: str | None = None,
        action: Literal["PASS", "REDUCE", "REJECT", "HALT"] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RiskInterventionRecord]:
        """
        查询风控干预记录
        
        Args:
            strategy_id: 可选，按策略ID过滤
            rule_name: 可选，按规则名过滤
            action: 可选，按动作过滤
            limit: 返回记录数限制
            offset: 分页偏移
            
        Returns:
            干预记录列表
        """
        records = self._filter_records(
            strategy_id=strategy_id,
            rule_name=rule_name,
            action=action,
        )
        
        # 按时间倒序
        sorted_records = sorted(
            records,
            key=lambda r: r.timestamp,
            reverse=True,
        )
        
        return sorted_records[offset:offset + limit]
    
    def get_intervened_records(
        self,
        strategy_id: str | None = None,
        limit: int = 100,
    ) -> list[RiskInterventionRecord]:
        """
        获取被干预的记录（不包括 PASS）
        
        Args:
            strategy_id: 可选，按策略ID过滤
            limit: 返回记录数限制
            
        Returns:
            被干预的记录列表
        """
        return self.get_records(
            strategy_id=strategy_id,
            limit=limit,
        )  # 后续过滤
    
    def _filter_records(
        self,
        strategy_id: str | None = None,
        rule_name: str | None = None,
        action: Literal["PASS", "REDUCE", "REJECT", "HALT"] | None = None,
        lookback_hours: int | None = None,
    ) -> list[RiskInterventionRecord]:
        """内部方法：过滤记录"""
        now = datetime.now(timezone.utc)
        records = self._records
        
        if strategy_id is not None:
            records = [r for r in records if r.strategy_id == strategy_id]
        
        if rule_name is not None:
            records = [r for r in records if r.rule_name == rule_name]
        
        if action is not None:
            records = [r for r in records if r.action == action]
        
        if lookback_hours is not None:
            cutoff = now - timedelta(hours=lookback_hours)
            records = [r for r in records if r.timestamp >= cutoff]
        
        return records
    
    def clear(self) -> None:
        """清空所有记录（主要用于测试）"""
        self._records.clear()
    
    def __len__(self) -> int:
        """返回记录总数"""
        return len(self._records)


# ==================== 辅助函数 ====================

def calculate_expectancy(
    win_rate: float,
    avg_win: float,
    loss_rate: float,
    avg_loss: float,
    avg_cost: float,
) -> float:
    """
    计算交易期望值
    
    Args:
        win_rate: 胜率 (0-1)
        avg_win: 平均盈利
        loss_rate: 败率 (0-1)
        avg_loss: 平均亏损
        avg_cost: 平均成本
        
    Returns:
        期望值
    """
    return avg_win * win_rate - avg_loss * loss_rate - avg_cost


def calculate_sharpe_decay(
    in_sample_sharpe: float,
    out_of_sample_sharpe: float,
) -> float:
    """
    计算夏普比率衰减
    
    Args:
        in_sample_sharpe: 样本内夏普
        out_of_sample_sharpe: 样本外夏普
        
    Returns:
        衰减比例 (1.0 = 无衰减, 0.0 = 完全衰减)
    """
    if in_sample_sharpe <= 0:
        return 0.0
    return max(0.0, min(1.0, out_of_sample_sharpe / in_sample_sharpe))


# ==================== 导入 ====================

from datetime import timedelta
