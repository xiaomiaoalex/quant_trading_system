"""
FundingOIAgent - 资金结构 Specialist Agent
==========================================

负责资金费率与OI（Open Interest）研究，输出基于资金结构的 SleeveProposal。

研究主线：
- Funding Rate 异常检测
- OI 变化率与价格背离
- 多空比异常
- 资金费率套利
"""

from typing import Any, Dict, List

from insight.committee.specialists.base import BaseSpecialist, SpecialistConfig
from insight.committee.schemas import SpecialistType


class FundingOIAgent(BaseSpecialist):
    """
    资金结构 Specialist Agent
    
    研究方向：
    - Funding Rate Z-Score
    - OI 变化率与价格背离
    - 多空比异常检测
    - 资金费率均值回归
    """
    
    @property
    def specialist_type(self) -> SpecialistType:
        return SpecialistType.FUNDING_OI
    
    def _get_research_domain_description(self) -> str:
        return (
            "Funding/OI based strategies including: "
            "funding rate anomaly detection, OI change rate vs price divergence, "
            "long-short ratio analysis, and funding rate arbitrage."
        )
    
    def _do_research(self, research_request: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行资金结构研究
        """
        request_lower = research_request.lower()
        
        # 确定研究的资金结构类型
        foi_types: List[str] = []
        if "funding" in request_lower or "资金费率" in request_lower:
            foi_types.append("funding_rate")
        if "oi" in request_lower or "open interest" in request_lower or "持仓量" in request_lower:
            foi_types.append("open_interest")
        if "long short" in request_lower or "多空" in request_lower:
            foi_types.append("long_short_ratio")
        
        if not foi_types:
            foi_types = ["funding_rate", "open_interest"]
        
        # 构建假设
        hypothesis = self._build_hypothesis(foi_types)
        
        # 确定适用状态
        regime = self._determine_regime(foi_types)
        
        # 确定失效条件
        failure_modes = self._determine_failure_modes(foi_types)
        
        # 确定需要的特征
        required_features = self._determine_required_features(foi_types)
        
        return {
            "hypothesis": hypothesis,
            "required_features": required_features,
            "regime": regime,
            "failure_modes": failure_modes,
            "evidence_refs": [f"foi_{t}" for t in foi_types],
        }
    
    def _build_hypothesis(self, foi_types: List[str]) -> str:
        """构建核心假设"""
        type_descriptions = {
            "funding_rate": "高资金费率预示多头平仓压力增加",
            "open_interest": "OI下降伴随价格上涨表明空头回补",
            "long_short_ratio": "多空比极端值预示均值回归",
        }
        
        descriptions = [type_descriptions.get(t, t) for t in foi_types]
        combined = "；".join(descriptions)
        
        hypothesis = (
            f"基于资金结构分析({combined})的策略，"
            f"通过监测资金费率、持仓量和多空比来预测市场短期平衡点。"
            f"资金结构失衡时趋势逆转概率增加。"
        )
        
        return hypothesis
    
    def _determine_regime(self, foi_types: List[str]) -> str:
        """确定适用市场状态"""
        if "funding_rate" in foi_types:
            return "high_funding"  # 高资金费率市场
        
        return "oi_imbalance"  # OI 失衡市场
    
    def _determine_failure_modes(self, foi_types: List[str]) -> List[str]:
        """确定失效条件"""
        base_failures = [
            "交易所更改资金费率算法",
            "合约流动性枯竭导致OI数据失真",
            "市场结构变化导致历史规律失效",
            "数据源延迟导致信号滞后",
        ]
        
        if "funding_rate" in foi_types:
            base_failures.extend([
                "资金费率持续为正/负导致均值回归失败",
                "极端资金费率吸引套利者快速平衡",
            ])
        
        if "open_interest" in foi_types:
            base_failures.extend([
                "OI增加但价格不涨表明主力建仓失败",
                "OI下降被误判为平仓而非新开仓",
            ])
        
        return list(set(base_failures))[:self.config.max_failure_modes]
    
    def _determine_required_features(self, foi_types: List[str]) -> List[str]:
        """确定需要的特征"""
        features: List[str] = []
        
        if "funding_rate" in foi_types:
            features.extend([
                "funding_rate",
                "funding_rate_zscore",
                "funding_rate_ma",
            ])
        
        if "open_interest" in foi_types:
            features.extend([
                "open_interest",
                "open_interest_roc",
                "oi_price_divergence",
            ])
        
        if "long_short_ratio" in foi_types:
            features.extend([
                "long_short_ratio",
                "ls_ratio_zscore",
            ])
        
        # 通用资金结构特征
        features.extend([
            "net_funding_flow",
            "funding_rate_std",
        ])
        
        return list(set(features))
