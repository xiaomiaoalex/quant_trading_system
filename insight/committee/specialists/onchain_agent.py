"""
OnChainAgent - 链上数据 Specialist Agent
=========================================

负责链上数据分析，输出基于链上指标的 SleeveProposal。

研究主线：
- 交易所净流量
- 爆仓数据
- 持币地址变化
- 链上活跃度
"""

from typing import Any, Dict, List

from insight.committee.specialists.base import BaseSpecialist, SpecialistConfig
from insight.committee.schemas import SpecialistType


class OnChainAgent(BaseSpecialist):
    """
    链上数据 Specialist Agent
    
    研究方向：
    - 交易所净流量（Exchange Netflow）
    - 爆仓/清算数据（Liquidation）
    - 持币地址分布
    - 链上活跃度指标
    """
    
    @property
    def specialist_type(self) -> SpecialistType:
        return SpecialistType.ONCHAIN
    
    def _get_research_domain_description(self) -> str:
        return (
            "On-chain data strategies including: "
            "exchange netflow, liquidation patterns, "
            "holder address changes, and on-chain activity metrics."
        )
    
    def _do_research(self, research_request: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行链上数据研究
        """
        request_lower = research_request.lower()
        
        # 确定研究的链上数据类型
        onchain_types: List[str] = []
        if "exchange" in request_lower or "交易所" in request_lower:
            onchain_types.append("exchange_netflow")
        if "liquidation" in request_lower or "爆仓" in request_lower:
            onchain_types.append("liquidation")
        if "holder" in request_lower or "持币" in request_lower:
            onchain_types.append("holder_distribution")
        if "active" in request_lower or "活跃" in request_lower:
            onchain_types.append("onchain_activity")
        
        if not onchain_types:
            onchain_types = ["exchange_netflow", "liquidation"]
        
        # 构建假设
        hypothesis = self._build_hypothesis(onchain_types)
        
        # 确定适用状态
        regime = self._determine_regime(onchain_types)
        
        # 确定失效条件
        failure_modes = self._determine_failure_modes(onchain_types)
        
        # 确定需要的特征
        required_features = self._determine_required_features(onchain_types)
        
        return {
            "hypothesis": hypothesis,
            "required_features": required_features,
            "regime": regime,
            "failure_modes": failure_modes,
            "evidence_refs": [f"onchain_{t}" for t in onchain_types],
        }
    
    def _build_hypothesis(self, onchain_types: List[str]) -> str:
        """构建核心假设"""
        type_descriptions = {
            "exchange_netflow": "资金流入交易所预示抛压增加",
            "liquidation": "爆仓数据极端值预示短期底部/顶部",
            "holder_distribution": "持币地址集中度变化预示趋势转变",
            "onchain_activity": "链上活跃度下降预示趋势延续",
        }
        
        descriptions = [type_descriptions.get(t, t) for t in onchain_types]
        combined = "；".join(descriptions)
        
        hypothesis = (
            f"基于链上数据分析({combined})的策略，"
            f"通过监测链上资金流向和持仓变化来预测市场短期走势。"
            f"链上信号领先链下价格变化。"
        )
        
        return hypothesis
    
    def _determine_regime(self, onchain_types: List[str]) -> str:
        """确定适用市场状态"""
        if "liquidation" in onchain_types:
            return "high_liquidation"  # 高爆仓市场
        
        return "netflow_imbalance"  # 净流量失衡市场
    
    def _determine_failure_modes(self, onchain_types: List[str]) -> List[str]:
        """确定失效条件"""
        base_failures = [
            "链上数据源API限制或延迟",
            "交易所钱包地址标识不准确",
            "跨链资产流动导致数据失真",
            "数据源覆盖不全（如新 DEX）",
        ]
        
        if "liquidation" in onchain_types:
            base_failures.extend([
                "交易所更改清算引擎",
                "高波动导致即时爆仓数据滞后",
                "保险基金介入改变清算路径",
            ])
        
        if "exchange_netflow" in onchain_types:
            base_failures.extend([
                "交易所冷钱包转账被误认为交易流量",
                "做市商内部对冲导致净流量失真",
            ])
        
        return list(set(base_failures))[:self.config.max_failure_modes]
    
    def _determine_required_features(self, onchain_types: List[str]) -> List[str]:
        """确定需要的特征"""
        features: List[str] = []
        
        if "exchange_netflow" in onchain_types:
            features.extend([
                "exchange_inflow",
                "exchange_outflow",
                "netflow",
            ])
        
        if "liquidation" in onchain_types:
            features.extend([
                "liquidation_long",
                "liquidation_short",
                "liquidation_total",
            ])
        
        if "holder_distribution" in onchain_types:
            features.extend([
                "holder_count",
                "holder_balance_distribution",
                "whale_ratio",
            ])
        
        if "onchain_activity" in onchain_types:
            features.extend([
                "tx_count",
                "active_addresses",
                "gas_used",
            ])
        
        # 通用链上特征
        features.extend([
            "onchain_volume",
            "network_value",
        ])
        
        return list(set(features))
