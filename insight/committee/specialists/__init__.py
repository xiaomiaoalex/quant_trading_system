"""
Committee Specialists Package - Specialist Agents 实现
====================================================

此模块包含五个 Specialist Agents，分别负责不同维度的策略研究。

Agent 列表：
- TrendAgent: 趋势信号研究
- PriceVolumeAgent: 量价关系研究
- FundingOIAgent: 资金结构研究
- OnChainAgent: 链上数据研究
- EventRegimeAgent: 事件与状态机研究

设计原则：
1. 每个 Agent 只输出 SleeveProposal，不输出交易指令
2. 所有输出必须包含 trace_id 和版本标签
3. 必须通过 validate_proposal_output 检查
"""

from insight.committee.specialists.base import BaseSpecialist, SpecialistConfig
from insight.committee.specialists.trend_agent import TrendAgent
from insight.committee.specialists.price_volume_agent import PriceVolumeAgent
from insight.committee.specialists.funding_oi_agent import FundingOIAgent
from insight.committee.specialists.onchain_agent import OnChainAgent
from insight.committee.specialists.event_regime_agent import EventRegimeAgent

__all__ = [
    "BaseSpecialist",
    "SpecialistConfig",
    "TrendAgent",
    "PriceVolumeAgent",
    "FundingOIAgent",
    "OnChainAgent",
    "EventRegimeAgent",
]
