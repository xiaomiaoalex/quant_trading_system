"""
TrendAgent - 趋势信号 Specialist Agent
========================================

负责趋势信号研究，输出基于趋势的 SleeveProposal。

研究主线：
- EMA 交叉
- 价格动量
- 布林带突破
- 趋势强度指标
"""

from typing import Any, Dict, List

from insight.committee.specialists.base import BaseSpecialist, SpecialistConfig
from insight.committee.schemas import SpecialistType


class TrendAgent(BaseSpecialist):
    """
    趋势信号 Specialist Agent
    
    研究方向：
    - 移动平均线交叉（EMA/SMA）
    - 价格动量（ROC, RSI）
    - 趋势强度（ADX）
    - 布林带突破
    - 趋势线分析
    """
    
    @property
    def specialist_type(self) -> SpecialistType:
        return SpecialistType.TREND
    
    def _get_research_domain_description(self) -> str:
        return (
            "Trend following strategies using technical indicators including: "
            "EMA crosses, price momentum (ROC, RSI), trend strength (ADX), "
            "Bollinger Band breakouts, and trendline analysis."
        )
    
    def _do_research(self, research_request: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行趋势信号研究
        
        基于研究请求和上下文，生成趋势相关的假设和提案。
        """
        # 分析研究请求中的关键词
        request_lower = research_request.lower()
        
        # 确定研究的趋势类型
        trend_types: List[str] = []
        if "ema" in request_lower or "moving average" in request_lower:
            trend_types.append("ema_cross")
        if "momentum" in request_lower or "roc" in request_lower:
            trend_types.append("momentum")
        if "bollinger" in request_lower or "band" in request_lower:
            trend_types.append("bollinger_breakout")
        if "adx" in request_lower or "trend strength" in request_lower:
            trend_types.append("trend_strength")
        
        # 如果没有明确指定，选择默认趋势类型
        if not trend_types:
            trend_types = ["ema_cross", "momentum"]
        
        # 构建假设
        hypothesis = self._build_hypothesis(trend_types)
        
        # 确定适用状态
        regime = self._determine_regime(trend_types)
        
        # 确定失效条件
        failure_modes = self._determine_failure_modes(trend_types)
        
        # 确定需要的特征
        required_features = self._determine_required_features(trend_types)
        
        return {
            "hypothesis": hypothesis,
            "required_features": required_features,
            "regime": regime,
            "failure_modes": failure_modes,
            "evidence_refs": [
                f"trend_{t}" for t in trend_types
            ],
        }
    
    def _build_hypothesis(self, trend_types: List[str]) -> str:
        """构建核心假设"""
        type_descriptions = {
            "ema_cross": "快速EMA穿越慢速EMA时产生趋势信号",
            "momentum": "价格动量持续时趋势延续概率更高",
            "bollinger_breakout": "价格突破布林带上轨时趋势加速概率增加",
            "trend_strength": "ADX高于阈值时趋势策略胜率更高",
        }
        
        descriptions = [type_descriptions.get(t, t) for t in trend_types]
        combined = "；".join(descriptions)
        
        hypothesis = (
            f"基于趋势信号({combined})的策略，"
            f"在趋势明确的市场中预期能捕捉到价格延续的收益。"
            f"趋势强度越高，策略预期表现越好。"
        )
        
        return hypothesis
    
    def _determine_regime(self, trend_types: List[str]) -> str:
        """确定适用市场状态"""
        if "trend_strength" in trend_types:
            return "strong_trend"  # 强趋势市场
        
        return "any_trend"  # 通用趋势市场（需配合动量确认）
    
    def _determine_failure_modes(self, trend_types: List[str]) -> List[str]:
        """确定失效条件"""
        base_failures = [
            "市场处于横盘整理状态，趋势频繁反转",
            "趋势突然反转，未设置有效止损",
            "波动率急剧下降，趋势信号频繁失效",
            "极端事件导致趋势信号失效",
        ]
        
        if "ema_cross" in trend_types:
            base_failures.extend([
                "EMA 参数设置不当导致频繁假信号",
                "快速EMA对噪音过于敏感",
            ])
        
        if "bollinger_breakout" in trend_types:
            base_failures.extend([
                "布林带收窄后剧烈波动导致假突破",
                "突破后迅速回撤导致亏损",
            ])
        
        return list(set(base_failures))[:self.config.max_failure_modes]
    
    def _determine_required_features(self, trend_types: List[str]) -> List[str]:
        """确定需要的特征"""
        features: List[str] = []
        
        if "ema_cross" in trend_types:
            features.extend([
                "ema_fast",
                "ema_slow", 
                "ema_signal",
            ])
        
        if "momentum" in trend_types:
            features.extend([
                "price_roc",
                "volume_roc",
            ])
        
        if "bollinger_breakout" in trend_types:
            features.extend([
                "bb_upper",
                "bb_middle",
                "bb_lower",
                "bb_width",
            ])
        
        if "trend_strength" in trend_types:
            features.append("adx")
        
        # 通用趋势特征
        features.extend([
            "trend_direction",
            "trend_confidence",
        ])
        
        return list(set(features))
