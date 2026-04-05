"""
Domain Models - 统一领域模型
============================

此模块定义与存储无关的业务领域模型。

设计原则：
1. 不依赖任何存储实现（内存/PostgreSQL/其他）
2. 不依赖数据库 row 类型或内存字典结构
3. 使用清晰、现代的 Python 类型标注
4. 包含必要的业务语义

主要模型：
- ProposalModel: 核心提案模型（统一 SleeveProposal 和 PortfolioProposal）
- ProposalStatus: 提案状态枚举
"""

from __future__ import annotations

import uuid
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class ProposalStatus(str, Enum):
    """提案状态"""
    PENDING = "pending"           # 等待委员会审查
    IN_REVIEW = "in_review"       # 正在被 specialist 或 red team 审查
    PASSED = "passed"             # 通过审查，进入 backtest
    REJECTED = "rejected"         # 被 red team 否决
    APPROVED = "approved"          # 人工审批通过
    ARCHIVED = "archived"          # 已废弃或被淘汰


class ProposalType(str, Enum):
    """提案类型 - 区分 Sleeve 和 Portfolio 提案"""
    SLEEVE = "sleeve"             # 单策略提案
    PORTFOLIO = "portfolio"       # 组合级提案


@dataclass(slots=True)
class CostAssumptions:
    """成本假设"""
    trading_fee_bps: float = 10.0
    slippage_bps: float = 5.0
    market_impact_bps: float = 2.0
    funding_rate_annual: float = 0.0
    borrow_rate_annual: float = 0.0
    liquidation_risk_bps: float = 0.0
    estimated_turnover_per_day: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trading_fee_bps": self.trading_fee_bps,
            "slippage_bps": self.slippage_bps,
            "market_impact_bps": self.market_impact_bps,
            "funding_rate_annual": self.funding_rate_annual,
            "borrow_rate_annual": self.borrow_rate_annual,
            "liquidation_risk_bps": self.liquidation_risk_bps,
            "estimated_turnover_per_day": self.estimated_turnover_per_day,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CostAssumptions:
        return cls(
            trading_fee_bps=data.get("trading_fee_bps", 10.0),
            slippage_bps=data.get("slippage_bps", 5.0),
            market_impact_bps=data.get("market_impact_bps", 2.0),
            funding_rate_annual=data.get("funding_rate_annual", 0.0),
            borrow_rate_annual=data.get("borrow_rate_annual", 0.0),
            liquidation_risk_bps=data.get("liquidation_risk_bps", 0.0),
            estimated_turnover_per_day=data.get("estimated_turnover_per_day", 1.0),
        )


@dataclass(slots=True)
class SleeveData:
    """
    Sleeve 分配数据
    
    用于 PortfolioProposal 中描述每个 sleeve 的分配参数。
    """
    sleeve_id: str                           # SleeveProposal ID
    capital_cap: Decimal = Decimal("0")       # 资金上限
    weight: float = 0.0                      # 权重 (0.0 - 1.0)
    max_position_size: Decimal = Decimal("0") # 最大持仓
    regime_enabled: bool = True              # 是否启用状态机

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sleeve_id": self.sleeve_id,
            "capital_cap": str(self.capital_cap),
            "weight": self.weight,
            "max_position_size": str(self.max_position_size),
            "regime_enabled": self.regime_enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SleeveData:
        return cls(
            sleeve_id=data["sleeve_id"],
            capital_cap=Decimal(data["capital_cap"]) if isinstance(data["capital_cap"], str) else Decimal(str(data["capital_cap"])),
            weight=data.get("weight", 0.0),
            max_position_size=Decimal(data["max_position_size"]) if isinstance(data["max_position_size"], str) else Decimal(str(data.get("max_position_size", "0"))),
            regime_enabled=data.get("regime_enabled", True),
        )


@dataclass(slots=True)
class RegimeCondition:
    """市场状态启停条件"""
    regime_name: str
    entry_conditions: List[str] = field(default_factory=list)
    exit_conditions: List[str] = field(default_factory=list)
    min_duration_minutes: int = 60
    confidence_threshold: float = 0.7

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime_name": self.regime_name,
            "entry_conditions": self.entry_conditions,
            "exit_conditions": self.exit_conditions,
            "min_duration_minutes": self.min_duration_minutes,
            "confidence_threshold": self.confidence_threshold,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RegimeCondition:
        return cls(
            regime_name=data["regime_name"],
            entry_conditions=data.get("entry_conditions", []),
            exit_conditions=data.get("exit_conditions", []),
            min_duration_minutes=data.get("min_duration_minutes", 60),
            confidence_threshold=data.get("confidence_threshold", 0.7),
        )


@dataclass(slots=True)
class ConflictResolution:
    """冲突优先级解决方案"""
    conflict_type: str
    higher_priority_sleeve: str
    lower_priority_sleeve: str
    resolution_rule: str
    capital_adjustment: Optional[Decimal] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_type": self.conflict_type,
            "higher_priority_sleeve": self.higher_priority_sleeve,
            "lower_priority_sleeve": self.lower_priority_sleeve,
            "resolution_rule": self.resolution_rule,
            "capital_adjustment": str(self.capital_adjustment) if self.capital_adjustment else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConflictResolution:
        cap_adj = data.get("capital_adjustment")
        return cls(
            conflict_type=data["conflict_type"],
            higher_priority_sleeve=data["higher_priority_sleeve"],
            lower_priority_sleeve=data["lower_priority_sleeve"],
            resolution_rule=data["resolution_rule"],
            capital_adjustment=Decimal(cap_adj) if cap_adj else None,
        )


@dataclass(slots=True)
class ProposalModel:
    """
    统一提案模型
    
    此模型统一了 SleeveProposal 和 PortfolioProposal 的核心字段，
    通过 `proposal_type` 字段区分类型。
    
    设计决策：
    - 使用 JSON payload 存储类型-specific 的数据，保持模型扩展性
    - created_at/updated_at 支持审计追踪
    - content_hash 支持去重和版本控制
    """
    # 身份字段
    proposal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    proposal_type: ProposalType = ProposalType.SLEEVE
    
    # 类型字段（冗余存储，方便查询）
    specialist_type: str = ""    # 仅 SLEEVE 类型使用
    
    # 核心内容（JSON 存储，保持灵活性）
    payload: Dict[str, Any] = field(default_factory=dict)
    
    # 状态
    status: ProposalStatus = ProposalStatus.PENDING
    
    # 版本追踪
    feature_version: str = ""
    prompt_version: str = ""
    
    # 可追踪性
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # 审计字段
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # 内容哈希（用于去重）
    _content_hash: Optional[str] = field(default=None, repr=False)
    
    @property
    def content_hash(self) -> str:
        """生成内容哈希"""
        if self._content_hash is not None:
            return self._content_hash
        
        content = {
            "proposal_type": self.proposal_type.value,
            "specialist_type": self.specialist_type,
            "payload": self.payload,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
        }
        content_str = json.dumps(content, sort_keys=True, default=str)
        self._content_hash = hashlib.sha256(content_str.encode()).hexdigest()[:16]
        return self._content_hash
    
    def save(self) -> None:
        """保存前调用，更新 updated_at 和重新计算 hash"""
        self.updated_at = datetime.now(timezone.utc)
        self._content_hash = None  # invalidate cache
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        return {
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type.value,
            "specialist_type": self.specialist_type,
            "payload": self.payload,
            "status": self.status.value,
            "feature_version": self.feature_version,
            "prompt_version": self.prompt_version,
            "trace_id": self.trace_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "content_hash": self.content_hash,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProposalModel:
        """
        从字典创建模型（用于反序列化）
        
        Args:
            data: 包含 proposal 数据的字典
            
        Returns:
            ProposalModel 实例
        """
        # 解析 proposal_type
        proposal_type_str = data.get("proposal_type", "sleeve")
        if isinstance(proposal_type_str, str):
            proposal_type = ProposalType(proposal_type_str)
        else:
            proposal_type = ProposalType.SLEEVE
        
        # 解析 status
        status_str = data.get("status", "pending")
        if isinstance(status_str, str):
            status = ProposalStatus(status_str)
        else:
            status = ProposalStatus.PENDING
        
        # 解析时间字段
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        elif created_at is None:
            created_at = datetime.now(timezone.utc)
        
        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        elif updated_at is None:
            updated_at = datetime.now(timezone.utc)
        
        return cls(
            proposal_id=data.get("proposal_id", str(uuid.uuid4())),
            proposal_type=proposal_type,
            specialist_type=data.get("specialist_type", ""),
            payload=data.get("payload", {}),
            status=status,
            feature_version=data.get("feature_version", ""),
            prompt_version=data.get("prompt_version", ""),
            trace_id=data.get("trace_id", str(uuid.uuid4())),
            created_at=created_at,
            updated_at=updated_at,
            _content_hash=data.get("content_hash"),
        )
    
    # =========================================================================
    # 便捷构造函数（从原有类型转换）
    # =========================================================================
    
    @classmethod
    def create_sleeve(
        cls,
        specialist_type: str,
        hypothesis: str,
        required_features: List[str],
        regime: str = "",
        failure_modes: Optional[List[str]] = None,
        cost_assumptions: Optional[CostAssumptions] = None,
        evidence_refs: Optional[List[str]] = None,
        **kwargs
    ) -> ProposalModel:
        """
        创建 Sleeve 提案
        
        Args:
            specialist_type: Specialist 类型
            hypothesis: 核心假设
            required_features: 依赖的特征列表
            regime: 适用市场状态
            failure_modes: 已知失效条件
            cost_assumptions: 成本假设
            evidence_refs: 证据引用
            **kwargs: 其他字段（feature_version, prompt_version, trace_id 等）
        """
        cost = cost_assumptions or CostAssumptions()
        failure_modes = failure_modes or []
        evidence_refs = evidence_refs or []
        
        payload = {
            "hypothesis": hypothesis,
            "required_features": required_features,
            "regime": regime,
            "failure_modes": failure_modes,
            "cost_assumptions": cost.to_dict(),
            "evidence_refs": evidence_refs,
        }
        
        return cls(
            proposal_type=ProposalType.SLEEVE,
            specialist_type=specialist_type,
            payload=payload,
            **kwargs
        )
    
    @classmethod
    def create_portfolio(
        cls,
        sleeves: List[SleeveData],
        capital_caps: Dict[str, Decimal],
        regime_conditions: Optional[Dict[str, RegimeCondition]] = None,
        conflict_priorities: Optional[List[ConflictResolution]] = None,
        risk_explanation: str = "",
        evaluation_task_id: str = "",
        **kwargs
    ) -> ProposalModel:
        """
        创建 Portfolio 提案
        
        Args:
            sleeves: Sleeve 分配列表
            capital_caps: 每个 sleeve 的资金上限
            regime_conditions: 市场状态条件
            conflict_priorities: 冲突解决优先级
            risk_explanation: 风险说明
            evaluation_task_id: 评估任务 ID
            **kwargs: 其他字段
        """
        regime_conditions = regime_conditions or {}
        conflict_priorities = conflict_priorities or []
        
        payload = {
            "sleeves": [s.to_dict() for s in sleeves],
            "capital_caps": {k: str(v) for k, v in capital_caps.items()},
            "regime_conditions": {k: v.to_dict() for k, v in regime_conditions.items()},
            "conflict_priorities": [c.to_dict() for c in conflict_priorities],
            "risk_explanation": risk_explanation,
            "evaluation_task_id": evaluation_task_id,
        }
        
        return cls(
            proposal_type=ProposalType.PORTFOLIO,
            payload=payload,
            **kwargs
        )
    
    # =========================================================================
    # 便捷访问器
    # =========================================================================
    
    @property
    def hypothesis(self) -> str:
        """获取假设（仅 SLEEVE 类型）"""
        return self.payload.get("hypothesis", "")
    
    @property
    def required_features(self) -> List[str]:
        """获取依赖特征（仅 SLEEVE 类型）"""
        return self.payload.get("required_features", [])
    
    @property
    def regime(self) -> str:
        """获取市场状态（仅 SLEEVE 类型）"""
        return self.payload.get("regime", "")
    
    @property
    def failure_modes(self) -> List[str]:
        """获取失效模式（仅 SLEEVE 类型）"""
        return self.payload.get("failure_modes", [])
    
    @property
    def cost_assumptions(self) -> CostAssumptions:
        """获取成本假设（仅 SLEEVE 类型）"""
        data = self.payload.get("cost_assumptions", {})
        return CostAssumptions.from_dict(data) if data else CostAssumptions()
    
    @property
    def evidence_refs(self) -> List[str]:
        """获取证据引用（仅 SLEEVE 类型）"""
        return self.payload.get("evidence_refs", [])
    
    @property
    def portfolio_sleeves(self) -> List[SleeveData]:
        """获取 Portfolio 的 sleeves（仅 PORTFOLIO 类型）"""
        sleeves_data = self.payload.get("sleeves", [])
        return [SleeveData.from_dict(s) for s in sleeves_data]
    
    @property
    def portfolio_capital_caps(self) -> Dict[str, Decimal]:
        """获取资金上限（仅 PORTFOLIO 类型）"""
        caps = self.payload.get("capital_caps", {})
        return {k: Decimal(v) if isinstance(v, str) else Decimal(str(v)) for k, v in caps.items()}
    
    @property
    def portfolio_regime_conditions(self) -> Dict[str, RegimeCondition]:
        """获取市场状态条件（仅 PORTFOLIO 类型）"""
        conditions = self.payload.get("regime_conditions", {})
        return {k: RegimeCondition.from_dict(v) for k, v in conditions.items()}
    
    @property
    def portfolio_conflict_priorities(self) -> List[ConflictResolution]:
        """获取冲突优先级（仅 PORTFOLIO 类型）"""
        conflicts = self.payload.get("conflict_priorities", [])
        return [ConflictResolution.from_dict(c) for c in conflicts]
    
    @property
    def risk_explanation(self) -> str:
        """获取风险说明（仅 PORTFOLIO 类型）"""
        return self.payload.get("risk_explanation", "")
    
    @property
    def evaluation_task_id(self) -> str:
        """获取评估任务 ID（仅 PORTFOLIO 类型）"""
        return self.payload.get("evaluation_task_id", "")