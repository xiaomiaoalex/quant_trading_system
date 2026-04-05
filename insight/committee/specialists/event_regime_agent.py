"""
EventRegimeAgent - 事件与状态机 Specialist Agent
=================================================

负责事件驱动与市场状态机研究，输出基于事件的 SleeveProposal。

研究主线：
- 公告事件与价格反应
- 市场状态转换
- 事件日历效应
- 宏观事件驱动
"""

from typing import Any, Dict, List

from insight.committee.specialists.base import BaseSpecialist, SpecialistConfig
from insight.committee.schemas import SpecialistType


class EventRegimeAgent(BaseSpecialist):
    """
    事件与状态机 Specialist Agent
    
    研究方向：
    - 公告事件与价格反应
    - 市场状态转换概率
    - 事件日历效应（周末、月末）
    - 宏观事件（CPI、利率等）
    """
    
    @property
    def specialist_type(self) -> SpecialistType:
        return SpecialistType.EVENT_REGIME
    
    def _get_research_domain_description(self) -> str:
        return (
            "Event and regime based strategies including: "
            "announcement events and price reaction, "
            "market regime transitions, calendar effects, "
            "and macro event driven trading."
        )
    
    def _do_research(self, research_request: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行事件与状态机研究
        """
        request_lower = research_request.lower()
        
        # 确定研究的事件类型
        event_types: List[str] = []
        if "announcement" in request_lower or "公告" in request_lower:
            event_types.append("announcement")
        if "regime" in request_lower or "状态" in request_lower:
            event_types.append("regime_transition")
        if "calendar" in request_lower or "效应" in request_lower:
            event_types.append("calendar_effect")
        if "macro" in request_lower or "宏观" in request_lower:
            event_types.append("macro_event")
        
        if not event_types:
            event_types = ["announcement", "regime_transition"]
        
        # 构建假设
        hypothesis = self._build_hypothesis(event_types)
        
        # 确定适用状态
        regime = self._determine_regime(event_types)
        
        # 确定失效条件
        failure_modes = self._determine_failure_modes(event_types)
        
        # 确定需要的特征
        required_features = self._determine_required_features(event_types)
        
        return {
            "hypothesis": hypothesis,
            "required_features": required_features,
            "regime": regime,
            "failure_modes": failure_modes,
            "evidence_refs": [f"event_{t}" for t in event_types],
        }
    
    def _build_hypothesis(self, event_types: List[str]) -> str:
        """构建核心假设"""
        type_descriptions = {
            "announcement": "重要公告发布后价格存在短期趋势性反应",
            "regime_transition": "市场状态转换前有可识别的预警信号",
            "calendar_effect": "特定日期存在统计显著的收益率规律",
            "macro_event": "宏观事件发布前市场波动率上升",
        }
        
        descriptions = [type_descriptions.get(t, t) for t in event_types]
        combined = "；".join(descriptions)
        
        hypothesis = (
            f"基于事件驱动分析({combined})的策略，"
            f"通过识别特定事件模式和状态转换来预测价格走势。"
            f"事件前后波动率特征可用于优化入场时机。"
        )
        
        return hypothesis
    
    def _determine_regime(self, event_types: List[str]) -> str:
        """确定适用市场状态"""
        if "macro_event" in event_types:
            return "high_volatility"  # 高波动市场
        
        if "announcement" in event_types:
            return "pre_event"  # 事件前市场
        
        return "regime_transition"  # 状态转换期
    
    def _determine_failure_modes(self, event_types: List[str]) -> List[str]:
        """确定失效条件"""
        base_failures = [
            "事件实际影响与预期不符",
            "事件被市场提前定价（price in）",
            "多个事件同时发生导致方向不明",
            "事件时间不确定导致无法有效回测",
        ]
        
        if "announcement" in event_types:
            base_failures.extend([
                "公告内容在发布前泄露",
                "交易所延迟或错误发布公告",
                "市场对公告解读分歧导致剧烈波动",
            ])
        
        if "regime_transition" in event_types:
            base_failures.extend([
                "状态转换识别滞后",
                "假突破导致错误状态判断",
                "状态持续时间过短无法获利",
            ])
        
        if "macro_event" in event_types:
            base_failures.extend([
                "宏观数据大幅修正导致反向波动",
                "央行干预超出预期",
                "地缘政治事件无法预测",
            ])
        
        return list(set(base_failures))[:self.config.max_failure_modes]
    
    def _determine_required_features(self, event_types: List[str]) -> List[str]:
        """确定需要的特征"""
        features: List[str] = []
        
        if "announcement" in event_types:
            features.extend([
                "announcement_type",
                "announcement_sentiment",
                "price_reaction_1m",
                "price_reaction_5m",
            ])
        
        if "regime_transition" in event_types:
            features.extend([
                "regime_probability",
                "volatility_regime",
                "liquidity_regime",
            ])
        
        if "calendar_effect" in event_types:
            features.extend([
                "day_of_week",
                "day_of_month",
                "is_month_end",
                "is_quarter_end",
            ])
        
        if "macro_event" in event_types:
            features.extend([
                "event_type",
                "event_timestamp",
                "market_expectation",
                "surprise_factor",
            ])
        
        # 通用事件特征
        features.extend([
            "event_count_24h",
            "event_impact_score",
        ])
        
        return list(set(features))
