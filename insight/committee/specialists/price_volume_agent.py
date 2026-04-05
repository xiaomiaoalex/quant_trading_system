"""
PriceVolumeAgent - 量价关系 Specialist Agent
=============================================

负责量价关系研究，输出基于量价分析的 SleeveProposal。

研究主线：
- 成交量扩张与收缩
- 波动率压缩与突破
- 价量背离
- 持仓量变化
"""

from typing import Any, Dict, List

from insight.committee.specialists.base import BaseSpecialist, SpecialistConfig
from insight.committee.schemas import SpecialistType


class PriceVolumeAgent(BaseSpecialist):
    """
    量价关系 Specialist Agent
    
    研究方向：
    - 成交量异常放大/萎缩
    - 波动率压缩（VIX, ATR）
    - 价量背离/同步
    - 持仓量（Open Interest）变化
    """
    
    @property
    def specialist_type(self) -> SpecialistType:
        return SpecialistType.PRICE_VOLUME
    
    def _get_research_domain_description(self) -> str:
        return (
            "Price-volume relationship strategies including: "
            "volume expansion/contraction, volatility compression/breakout, "
            "price-volume divergence, and open interest analysis."
        )
    
    def _do_research(self, research_request: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行量价关系研究
        """
        request_lower = research_request.lower()
        
        # 确定研究的量价类型
        pv_types: List[str] = []
        if "volume" in request_lower or "成交量" in request_lower:
            pv_types.append("volume_pattern")
        if "volatility" in request_lower or "波动率" in request_lower:
            pv_types.append("volatility")
        if "divergence" in request_lower or "背离" in request_lower:
            pv_types.append("divergence")
        if "open interest" in request_lower or "持仓量" in request_lower:
            pv_types.append("open_interest")
        
        if not pv_types:
            pv_types = ["volume_pattern", "volatility"]
        
        # 构建假设
        hypothesis = self._build_hypothesis(pv_types)
        
        # 确定适用状态
        regime = self._determine_regime(pv_types)
        
        # 确定失效条件
        failure_modes = self._determine_failure_modes(pv_types)
        
        # 确定需要的特征
        required_features = self._determine_required_features(pv_types)
        
        return {
            "hypothesis": hypothesis,
            "required_features": required_features,
            "regime": regime,
            "failure_modes": failure_modes,
            "evidence_refs": [f"pv_{t}" for t in pv_types],
        }
    
    def _build_hypothesis(self, pv_types: List[str]) -> str:
        """构建核心假设"""
        type_descriptions = {
            "volume_pattern": "成交量异常放大/萎缩先于价格反转",
            "volatility": "波动率收缩后突破方向可预测",
            "divergence": "价格与成交量背离预示趋势衰竭",
            "open_interest": "持仓量增加确认趋势延续",
        }
        
        descriptions = [type_descriptions.get(t, t) for t in pv_types]
        combined = "；".join(descriptions)
        
        hypothesis = (
            f"基于量价分析({combined})的策略，"
            f"通过监测成交量和波动率变化来预测价格短期走势。"
            f"量价配合时信号强度更高。"
        )
        
        return hypothesis
    
    def _determine_regime(self, pv_types: List[str]) -> str:
        """确定适用市场状态"""
        if "volatility" in pv_types:
            return "low_volatility"  # 低波动率市场（波动率压缩后突破）
        
        return "high_volume"  # 高成交量市场
    
    def _determine_failure_modes(self, pv_types: List[str]) -> List[str]:
        """确定失效条件"""
        base_failures = [
            "市场流动性突然枯竭导致量价信号失真",
            "交易所数据延迟导致成交量统计不准",
            "市场操纵导致成交量异常",
            "极端波动事件导致量价关系失效",
        ]
        
        if "divergence" in pv_types:
            base_failures.extend([
                "背离信号过于主观，难以量化",
                "背离后延迟很久才发生反转",
            ])
        
        if "volatility" in pv_types:
            base_failures.extend([
                "低波动率持续时间不确定",
                "波动率突破方向不可预测",
            ])
        
        return list(set(base_failures))[:self.config.max_failure_modes]
    
    def _determine_required_features(self, pv_types: List[str]) -> List[str]:
        """确定需要的特征"""
        features: List[str] = []
        
        if "volume_pattern" in pv_types:
            features.extend([
                "volume_roc",
                "volume_ma_ratio",
                "volume_spike",
            ])
        
        if "volatility" in pv_types:
            features.extend([
                "atr",
                "volatility_ratio",
                "bb_width",
            ])
        
        if "divergence" in pv_types:
            features.extend([
                "price_roc",
                "volume_roc",
                "divergence_signal",
            ])
        
        if "open_interest" in pv_types:
            features.extend([
                "open_interest",
                "open_interest_roc",
            ])
        
        # 通用量价特征
        features.extend([
            "volume_price_correlation",
            "turnover",
        ])
        
        return list(set(features))
