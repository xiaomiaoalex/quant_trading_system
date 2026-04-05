"""
Test Committee Specialists - Specialist Agents 单元测试
=====================================================

测试覆盖：
1. TrendAgent - 趋势信号研究
2. PriceVolumeAgent - 量价关系研究
3. FundingOIAgent - 资金结构研究
4. OnChainAgent - 链上数据研究
5. EventRegimeAgent - 事件与状态机研究
6. CommitteeRouter - 路由逻辑
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from insight.committee.schemas import (
    SpecialistType,
    ValidationResult,
    Violation,
    ViolationType,
)
from insight.committee.specialists import (
    TrendAgent,
    PriceVolumeAgent,
    FundingOIAgent,
    OnChainAgent,
    EventRegimeAgent,
    BaseSpecialist,
    SpecialistConfig,
)
from insight.committee.router import CommitteeRouter


# ==================== 测试辅助 ====================

class TestSpecialistConfig:
    """SpecialistConfig 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = SpecialistConfig()
        assert config.feature_version == "v1.0.0"
        assert config.prompt_version == "v1.0.0"
        assert config.max_hypothesis_length == 1000
        assert config.max_failure_modes == 10
        assert config.min_confidence_threshold == 0.6

    def test_custom_config(self):
        """测试自定义配置"""
        config = SpecialistConfig(
            feature_version="v2.0.0",
            prompt_version="v2.0.0",
            max_hypothesis_length=500,
        )
        assert config.feature_version == "v2.0.0"
        assert config.max_hypothesis_length == 500


# ==================== TrendAgent 测试 ====================

class TestTrendAgent:
    """TrendAgent 测试"""

    def setup_method(self):
        """测试前准备"""
        self.agent = TrendAgent()

    def test_specialist_type(self):
        """测试 specialist 类型"""
        assert self.agent.specialist_type == SpecialistType.TREND

    def test_name(self):
        """测试 agent 名称"""
        assert self.agent.name == "TrendAgent"

    def test_get_system_prompt(self):
        """测试系统提示词"""
        prompt = self.agent.get_system_prompt()
        assert "TrendAgent" in prompt
        assert "trend" in prompt.lower()

    def test_do_research_ema_keyword(self):
        """测试 EMA 关键词识别"""
        result = self.agent._do_research(
            "研究 EMA 交叉策略",
            {}
        )
        # EMA 关键词应该触发 ema 相关特征
        assert "ema_fast" in result["required_features"]
        assert "ema_slow" in result["required_features"]
        assert "ema_signal" in result["required_features"]

    def test_do_research_momentum_keyword(self):
        """测试动量关键词识别"""
        result = self.agent._do_research(
            "研究 momentum 策略",
            {}
        )
        # 动量策略应该触发价格动量相关特征
        assert "price_roc" in result["required_features"]
        assert "volume_roc" in result["required_features"]

    def test_do_research_bollinger_keyword(self):
        """测试布林带关键词识别"""
        result = self.agent._do_research(
            "研究 Bollinger Band 突破策略",
            {}
        )
        # Bollinger Band 策略应该触发表林带相关特征
        assert "bb_upper" in result["required_features"]
        assert "bb_width" in result["required_features"]

    def test_do_research_adx_keyword(self):
        """测试 ADX 关键词识别"""
        result = self.agent._do_research(
            "研究趋势强度 ADX 策略",
            {}
        )
        assert "adx" in result["required_features"]

    def test_do_research_default_types(self):
        """测试默认趋势类型"""
        result = self.agent._do_research(
            "随便研究点什么",
            {}
        )
        # 默认应该包含 EMA 和动量相关特征
        assert "ema_fast" in result["required_features"] or "price_roc" in result["required_features"]

    def test_hypothesis_contains_strategy_description(self):
        """测试假设包含策略描述"""
        result = self.agent._do_research(
            "研究趋势策略",
            {}
        )
        assert len(result["hypothesis"]) > 0
        assert "趋势" in result["hypothesis"]

    def test_failure_modes_are_list(self):
        """测试失效条件是列表"""
        result = self.agent._do_research(
            "研究趋势策略",
            {}
        )
        assert isinstance(result["failure_modes"], list)
        assert len(result["failure_modes"]) > 0

    def test_evidence_refs_format(self):
        """测试证据引用格式"""
        result = self.agent._do_research(
            "研究 EMA 策略",
            {}
        )
        assert isinstance(result["evidence_refs"], list)
        assert all(ref.startswith("trend_") for ref in result["evidence_refs"])

    def test_research_with_empty_context(self):
        """测试空上下文研究"""
        result = self.agent._do_research(
            "研究趋势策略",
            {}
        )
        assert "hypothesis" in result
        assert "required_features" in result
        assert "regime" in result
        assert "failure_modes" in result

    def test_full_research_output(self):
        """测试完整研究输出"""
        output = self.agent.research("研究 EMA 交叉趋势策略")
        
        assert output.output_type.value == "sleeve_proposal"
        assert output.trace_id is not None
        assert output.feature_version == self.agent.config.feature_version
        assert output.validation_result.is_valid is True
        assert "specialist_type" in output.content


# ==================== PriceVolumeAgent 测试 ====================

class TestPriceVolumeAgent:
    """PriceVolumeAgent 测试"""

    def setup_method(self):
        """测试前准备"""
        self.agent = PriceVolumeAgent()

    def test_specialist_type(self):
        """测试 specialist 类型"""
        assert self.agent.specialist_type == SpecialistType.PRICE_VOLUME

    def test_do_research_volume_keyword(self):
        """测试成交量关键词识别"""
        result = self.agent._do_research(
            "研究成交量异常策略",
            {}
        )
        # 成交量策略应该触发成交量相关特征
        assert "volume_roc" in result["required_features"]
        assert "volume_ma_ratio" in result["required_features"]
        assert "volume_spike" in result["required_features"]

    def test_do_research_volatility_keyword(self):
        """测试波动率关键词识别"""
        result = self.agent._do_research(
            "研究波动率压缩策略",
            {}
        )
        # 波动率策略应该触发波动率相关特征
        assert "atr" in result["required_features"]
        assert "volatility_ratio" in result["required_features"]
        assert "bb_width" in result["required_features"]

    def test_do_research_divergence_keyword(self):
        """测试背离关键词识别"""
        result = self.agent._do_research(
            "研究价量背离策略",
            {}
        )
        # 背离策略应该触发背离相关特征
        assert "price_roc" in result["required_features"]
        assert "volume_roc" in result["required_features"]
        assert "divergence_signal" in result["required_features"]

    def test_do_research_open_interest_keyword(self):
        """测试持仓量关键词识别"""
        result = self.agent._do_research(
            "研究持仓量变化策略",
            {}
        )
        # 持仓量策略应该触发持仓量相关特征
        assert "open_interest" in result["required_features"]
        assert "open_interest_roc" in result["required_features"]


# ==================== FundingOIAgent 测试 ====================

class TestFundingOIAgent:
    """FundingOIAgent 测试"""

    def setup_method(self):
        """测试前准备"""
        self.agent = FundingOIAgent()

    def test_specialist_type(self):
        """测试 specialist 类型"""
        assert self.agent.specialist_type == SpecialistType.FUNDING_OI

    def test_do_research_funding_keyword(self):
        """测试资金费率关键词识别"""
        result = self.agent._do_research(
            "研究资金费率异常策略",
            {}
        )
        # 资金费率策略应该触发资金费率相关特征
        assert "funding_rate" in result["required_features"]
        assert "funding_rate_zscore" in result["required_features"]
        assert "funding_rate_ma" in result["required_features"]

    def test_do_research_oi_keyword(self):
        """测试 OI 关键词识别"""
        result = self.agent._do_research(
            "研究持仓量 OI 策略",
            {}
        )
        # OI 策略应该触发持仓量相关特征
        assert "open_interest" in result["required_features"]
        assert "open_interest_roc" in result["required_features"]

    def test_do_research_long_short_keyword(self):
        """测试多空比关键词识别"""
        result = self.agent._do_research(
            "研究多空比策略",
            {}
        )
        # 多空比策略应该触发多空比相关特征
        assert "long_short_ratio" in result["required_features"]
        assert "ls_ratio_zscore" in result["required_features"]


# ==================== OnChainAgent 测试 ====================

class TestOnChainAgent:
    """OnChainAgent 测试"""

    def setup_method(self):
        """测试前准备"""
        self.agent = OnChainAgent()

    def test_specialist_type(self):
        """测试 specialist 类型"""
        assert self.agent.specialist_type == SpecialistType.ONCHAIN

    def test_do_research_exchange_keyword(self):
        """测试交易所关键词识别"""
        result = self.agent._do_research(
            "研究交易所净流量策略",
            {}
        )
        # 交易所净流量策略应该触发相关特征
        assert "exchange_inflow" in result["required_features"]
        assert "exchange_outflow" in result["required_features"]
        assert "netflow" in result["required_features"]

    def test_do_research_liquidation_keyword(self):
        """测试爆仓关键词识别"""
        result = self.agent._do_research(
            "研究爆仓数据策略",
            {}
        )
        # 爆仓策略应该触发爆仓相关特征
        assert "liquidation_long" in result["required_features"]
        assert "liquidation_short" in result["required_features"]
        assert "liquidation_total" in result["required_features"]

    def test_do_research_holder_keyword(self):
        """测试持币者关键词识别"""
        result = self.agent._do_research(
            "研究持币地址策略",
            {}
        )
        # 持币地址策略应该触发相关特征
        assert "holder_count" in result["required_features"]
        assert "holder_balance_distribution" in result["required_features"]
        assert "whale_ratio" in result["required_features"]


# ==================== EventRegimeAgent 测试 ====================

class TestEventRegimeAgent:
    """EventRegimeAgent 测试"""

    def setup_method(self):
        """测试前准备"""
        self.agent = EventRegimeAgent()

    def test_specialist_type(self):
        """测试 specialist 类型"""
        assert self.agent.specialist_type == SpecialistType.EVENT_REGIME

    def test_do_research_announcement_keyword(self):
        """测试公告关键词识别"""
        result = self.agent._do_research(
            "研究公告事件策略",
            {}
        )
        # 公告事件策略应该触发相关特征
        assert "announcement_type" in result["required_features"]
        assert "announcement_sentiment" in result["required_features"]
        assert "price_reaction_1m" in result["required_features"]

    def test_do_research_regime_keyword(self):
        """测试状态机关键词识别"""
        result = self.agent._do_research(
            "研究市场状态转换策略",
            {}
        )
        # 状态转换策略应该触发相关特征
        assert "regime_probability" in result["required_features"]
        assert "volatility_regime" in result["required_features"]
        assert "liquidity_regime" in result["required_features"]

    def test_do_research_macro_keyword(self):
        """测试宏观事件关键词识别"""
        result = self.agent._do_research(
            "研究宏观事件策略",
            {}
        )
        # 宏观事件策略应该触发相关特征
        assert "event_type" in result["required_features"]
        assert "event_timestamp" in result["required_features"]
        assert "market_expectation" in result["required_features"]
        assert "surprise_factor" in result["required_features"]


# ==================== CommitteeRouter 测试 ====================

class TestCommitteeRouter:
    """CommitteeRouter 测试"""

    def setup_method(self):
        """测试前准备"""
        self.router = CommitteeRouter()

    def test_route_trend_keywords(self):
        """测试趋势关键词路由"""
        types = self.router.route("研究 EMA 交叉策略")
        assert SpecialistType.TREND in types

    def test_route_volume_keywords(self):
        """测试成交量关键词路由"""
        types = self.router.route("研究成交量异常")
        assert SpecialistType.PRICE_VOLUME in types

    def test_route_funding_keywords(self):
        """测试资金费率关键词路由"""
        types = self.router.route("研究资金费率")
        assert SpecialistType.FUNDING_OI in types

    def test_route_onchain_keywords(self):
        """测试链上关键词路由"""
        types = self.router.route("研究交易所净流量")
        assert SpecialistType.ONCHAIN in types

    def test_route_event_keywords(self):
        """测试事件关键词路由"""
        types = self.router.route("研究公告事件")
        assert SpecialistType.EVENT_REGIME in types

    def test_route_unknown_returns_trend(self):
        """测试未知关键词默认返回趋势"""
        types = self.router.route("随便研究点什么")
        assert SpecialistType.TREND in types

    def test_route_multiple_keywords(self):
        """测试多个关键词路由"""
        types = self.router.route("研究 EMA 和成交量")
        assert SpecialistType.TREND in types
        assert SpecialistType.PRICE_VOLUME in types

    def test_get_agent(self):
        """测试获取 agent"""
        agent = self.router.get_agent(SpecialistType.TREND)
        assert isinstance(agent, TrendAgent)
        assert agent.specialist_type == SpecialistType.TREND

    def test_get_agent_valid_type(self):
        """测试获取有效类型"""
        agent = self.router.get_agent(SpecialistType.TREND)
        assert isinstance(agent, TrendAgent)
        assert agent.specialist_type == SpecialistType.TREND

    def test_run_specialists(self):
        """测试运行 specialists"""
        outputs = self.router.run_specialists(
            "研究 EMA 策略",
            {SpecialistType.TREND},
            {}
        )
        assert len(outputs) == 1
        assert outputs[0].output_type.value == "sleeve_proposal"

    def test_run_all_specialists(self):
        """测试运行所有 specialists"""
        outputs = self.router.run_all_specialists("研究策略", {})
        assert len(outputs) == len(SpecialistType)

    def test_route_and_run(self):
        """测试路由并运行"""
        outputs = self.router.route_and_run("研究 EMA 策略", {})
        assert len(outputs) >= 1


# ==================== BaseSpecialist 验证测试 ====================

class TestBaseSpecialistValidation:
    """BaseSpecialist 验证测试"""

    def test_validate_output_empty_hypothesis(self):
        """测试空假设验证"""
        agent = TrendAgent()
        # 创建一个假设为空的 proposal
        from insight.committee.schemas import SleeveProposal
        
        class TestAgent(BaseSpecialist):
            @property
            def specialist_type(self):
                return SpecialistType.TREND
            
            def _do_research(self, research_request, context):
                return {"hypothesis": "", "required_features": [], "regime": "", "failure_modes": []}
            
            def _get_research_domain_description(self):
                return "Test"
        
        test_agent = TestAgent()
        # 由于假设为空，应该返回无效
        result = test_agent._validate_output(
            SleeveProposal(
                specialist_type=SpecialistType.TREND,
                hypothesis="",
                required_features=["test"],
                regime="test",
                failure_modes=["test"],
            )
        )
        assert result.is_valid is False

    def test_validate_output_missing_features(self):
        """测试缺少特征验证"""
        agent = TrendAgent()
        from insight.committee.schemas import SleeveProposal
        
        class TestAgent(BaseSpecialist):
            @property
            def specialist_type(self):
                return SpecialistType.TREND
            
            def _do_research(self, research_request, context):
                return {"hypothesis": "Test hypothesis", "required_features": [], "regime": "", "failure_modes": []}
            
            def _get_research_domain_description(self):
                return "Test"
        
        test_agent = TestAgent()
        result = test_agent._validate_output(
            SleeveProposal(
                specialist_type=SpecialistType.TREND,
                hypothesis="Test hypothesis",
                required_features=[],
                regime="test",
                failure_modes=["test"],
            )
        )
        assert result.is_valid is False


# ==================== 边界约束测试 ====================

class TestBoundaryConstraints:
    """边界约束测试"""

    def test_no_direct_trading_instructions(self):
        """测试不包含直接交易指令"""
        agent = TrendAgent()
        
        # 研究请求不应该包含交易指令
        result = agent._do_research("研究趋势策略", {})
        
        # 验证假设中不包含直接交易指令
        forbidden_phrases = ["买入", "卖出", "做多", "做空", "buy", "sell", "long", "short"]
        for phrase in forbidden_phrases:
            assert phrase not in result["hypothesis"]

    def test_research_returns_proposal_not_trading_signal(self):
        """测试研究返回提案而非交易信号"""
        agent = TrendAgent()
        output = agent.research("研究 EMA 趋势策略")
        
        # 应该返回 sleeve_proposal 类型
        assert output.output_type.value == "sleeve_proposal"
        
        # content 应该包含假设和特征，而不是具体交易指令
        assert "hypothesis" in output.content
        assert "required_features" in output.content
