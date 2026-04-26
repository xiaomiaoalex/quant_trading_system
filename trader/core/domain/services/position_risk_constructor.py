"""
Position & Risk Constructor - 仓位风险构造函数
===============================================
交易系统的仓位风险控制核心服务。

核心功能：
1. 单币种最大暴露 (Per-Symbol Max Exposure)
2. 总暴露控制 (Total Exposure Control)
3. 冷却期管理 (Cooldown Period)
4. 最小交易阈值 (Minimum Trade Threshold)
5. Regime 风险折扣 (Regime Risk Discount)

重要约束：
- Core Plane 禁止 IO
- 所有计算使用 Decimal
- Fail-Closed 异常处理
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List, Protocol, TYPE_CHECKING

from trader.core.domain.models.position import Position
from trader.core.domain.models.signal import Signal, SignalType

if TYPE_CHECKING:
    from trader.adapters.persistence.feature_store import FeatureStore


class MarketRegime(Enum):
    """市场状态枚举"""
    BULL = "BULL"           # 牛市 - 正常风险敞口
    BEAR = "BEAR"           # 熊市 - 降低敞口
    SIDEWAYS = "SIDEWAYS"   # 震荡市 - 适度敞口
    CRISIS = "CRISIS"       # 危机 - 最小化敞口


@dataclass
class PositionRiskConstructorConfig:
    """仓位风险构造函数配置"""
    
    # 单币种最大暴露
    max_exposure_per_symbol: Decimal = Decimal("10000")   # 单币种最大暴露金额 (USD)
    max_position_size_percent: Decimal = Decimal("10")   # 单币种最大仓位占比 (%)
    
    # 总暴露控制
    max_total_exposure: Decimal = Decimal("50000")        # 总最大暴露金额 (USD)
    total_exposure_warning_threshold: Decimal = Decimal("80")  # 警告阈值 (%)
    
    # 冷却期
    cooldown_seconds: int = 300                           # 冷却期秒数 (默认5分钟)
    cooldown_enabled: bool = True                        # 是否启用冷却期
    
    # 最小交易阈值
    min_trade_threshold: Decimal = Decimal("10")         # 最小交易金额 (USD)
    min_trade_threshold_enabled: bool = True             # 是否启用最小阈值
    
    # Regime 风险折扣
    regime_discounts: Dict[MarketRegime, Decimal] = field(default_factory=lambda: {
        MarketRegime.BULL: Decimal("1.0"),      # 100% 敞口
        MarketRegime.BEAR: Decimal("0.5"),       # 50% 敞口
        MarketRegime.SIDEWAYS: Decimal("0.7"),  # 70% 敞口
        MarketRegime.CRISIS: Decimal("0.2"),    # 20% 敞口
    })
    regime_default: MarketRegime = MarketRegime.BULL     # 默认市场状态


@dataclass
class PerSymbolExposureResult:
    """单币种暴露检查结果"""
    allowed: bool
    max_allowed_qty: Decimal
    current_exposure: Decimal
    remaining_exposure: Decimal
    rejection_reason: Optional[str] = None
    message: str = ""


@dataclass
class TotalExposureResult:
    """总暴露检查结果"""
    allowed: bool
    total_current_exposure: Decimal
    remaining_exposure: Decimal
    exposure_percent: Decimal
    is_warning: bool = False
    rejection_reason: Optional[str] = None
    message: str = ""


@dataclass
class CooldownResult:
    """冷却期检查结果"""
    allowed: bool
    last_trade_time: Optional[datetime]
    cooldown_remaining_seconds: float = 0.0
    rejection_reason: Optional[str] = None
    message: str = ""


@dataclass
class MinThresholdResult:
    """最小交易阈值检查结果"""
    allowed: bool
    signal_strength: Decimal
    calculated_qty: Decimal
    min_threshold: Decimal
    rejection_reason: Optional[str] = None
    message: str = ""


@dataclass
class RegimeDiscountResult:
    """Regime 风险折扣结果"""
    original_strength: Decimal
    adjusted_strength: Decimal
    regime: MarketRegime
    discount_factor: Decimal


@dataclass
class PositionRiskConstruction:
    """仓位风险构造结果"""
    per_symbol_result: PerSymbolExposureResult
    total_exposure_result: TotalExposureResult
    cooldown_result: CooldownResult
    min_threshold_result: MinThresholdResult
    regime_discount_result: RegimeDiscountResult
    
    @property
    def is_allowed(self) -> bool:
        """所有检查是否通过"""
        return (
            self.per_symbol_result.allowed
            and self.total_exposure_result.allowed
            and self.cooldown_result.allowed
            and self.min_threshold_result.allowed
        )


class RegimeProviderPort(Protocol):
    """Regime 提供者端口协议"""
    
    def get_current_regime(self, symbol: str) -> MarketRegime:
        """
        获取当前市场状态
        
        Args:
            symbol: 交易标的
            
        Returns:
            MarketRegime: 当前市场状态
        """
        ...


class CooldownTrackerPort(Protocol):
    """冷却期追踪器端口协议"""
    
    def get_last_trade_time(self, symbol: str) -> Optional[datetime]:
        """
        获取上次交易时间
        
        Args:
            symbol: 交易标的
            
        Returns:
            datetime: 上次交易时间，不存在返回 None
        """
        ...


class PositionRiskConstructor:
    """
    仓位风险构造函数
    
    在交易执行前进行多维度风险检查：
    1. 单币种最大暴露
    2. 总暴露控制
    3. 冷却期管理
    4. 最小交易阈值
    5. Regime 风险折扣
    """
    
    def __init__(
        self,
        config: Optional[PositionRiskConstructorConfig] = None,
        regime_provider: Optional[RegimeProviderPort] = None,
        cooldown_tracker: Optional[CooldownTrackerPort] = None,
    ):
        self._config = config or PositionRiskConstructorConfig()
        self._regime_provider = regime_provider
        self._cooldown_tracker = cooldown_tracker
    
    @property
    def config(self) -> PositionRiskConstructorConfig:
        """获取配置"""
        return self._config
    
    # ==================== 单币种最大暴露 ====================
    
    def check_per_symbol_exposure(
        self,
        signal: Signal,
        current_position: Optional[Position],
        max_exposure: Optional[Decimal] = None,
        total_portfolio_value: Optional[Decimal] = None,
    ) -> PerSymbolExposureResult:
        """
        检查单币种最大暴露
        
        Args:
            signal: 交易信号
            current_position: 当前持仓
            max_exposure: 可选的覆盖配置的最大暴露值
            total_portfolio_value: 可选的总投资组合价值，用于计算仓位占比限制
            
        Returns:
            PerSymbolExposureResult: 检查结果
        """
        max_per_symbol = max_exposure or self._config.max_exposure_per_symbol
        
        # 计算当前暴露（使用绝对值，多空仓位都计入暴露）
        current_exposure = Decimal("0")
        if current_position is not None and current_position.quantity != 0:
            current_exposure = abs(current_position.market_value)
        
        # 计算剩余暴露
        remaining = max_per_symbol - current_exposure
        
        # 边界检查
        if remaining <= 0:
            return PerSymbolExposureResult(
                allowed=False,
                max_allowed_qty=Decimal("0"),
                current_exposure=current_exposure,
                remaining_exposure=Decimal("0"),
                rejection_reason="MAX_EXPOSURE_REACHED",
                message=f"币种 {signal.symbol} 已达最大暴露 {max_per_symbol} USD"
            )
        
        # 计算允许的最大交易量（基于信号价格）
        if signal.price > 0:
            max_allowed_qty = remaining / signal.price
        else:
            return PerSymbolExposureResult(
                allowed=False,
                max_allowed_qty=Decimal("0"),
                current_exposure=current_exposure,
                remaining_exposure=remaining,
                rejection_reason="INVALID_SIGNAL_PRICE",
                message=f"信号价格无效: {signal.price}"
            )
        
        # 应用仓位占比限制（如果提供了总投资组合价值且大于0）
        if total_portfolio_value is not None and total_portfolio_value > 0 and self._config.max_position_size_percent > 0:
            max_position_pct_value = total_portfolio_value * self._config.max_position_size_percent / Decimal("100")
            max_allowed_qty_pct = max_position_pct_value / signal.price
            # 取两种限制的较小值
            max_allowed_qty = min(max_allowed_qty, max_allowed_qty_pct)
        
        return PerSymbolExposureResult(
            allowed=True,
            max_allowed_qty=max_allowed_qty,
            current_exposure=current_exposure,
            remaining_exposure=remaining,
            message=f"币种 {signal.symbol} 剩余暴露额度: {remaining} USD"
        )

    def check_per_symbol_exposure_multi_strategy(
        self,
        signal: Signal,
        strategy_positions: List[Position],
        max_exposure: Optional[Decimal] = None,
        total_portfolio_value: Optional[Decimal] = None,
    ) -> PerSymbolExposureResult:
        max_per_symbol = max_exposure or self._config.max_exposure_per_symbol

        current_exposure = Decimal("0")
        for pos in strategy_positions:
            if pos.symbol == signal.symbol and pos.quantity != 0:
                current_exposure += abs(pos.market_value)

        remaining = max_per_symbol - current_exposure

        if remaining <= 0:
            return PerSymbolExposureResult(
                allowed=False,
                max_allowed_qty=Decimal("0"),
                current_exposure=current_exposure,
                remaining_exposure=Decimal("0"),
                rejection_reason="MAX_EXPOSURE_REACHED",
                message=f"币种 {signal.symbol} 已达最大暴露 {max_per_symbol} USD (跨策略合计)"
            )

        if signal.price > 0:
            max_allowed_qty = remaining / signal.price
        else:
            return PerSymbolExposureResult(
                allowed=False,
                max_allowed_qty=Decimal("0"),
                current_exposure=current_exposure,
                remaining_exposure=remaining,
                rejection_reason="INVALID_SIGNAL_PRICE",
                message=f"信号价格无效: {signal.price}"
            )

        if total_portfolio_value is not None and total_portfolio_value > 0 and self._config.max_position_size_percent > 0:
            max_position_pct_value = total_portfolio_value * self._config.max_position_size_percent / Decimal("100")
            max_allowed_qty_pct = max_position_pct_value / signal.price
            max_allowed_qty = min(max_allowed_qty, max_allowed_qty_pct)

        return PerSymbolExposureResult(
            allowed=True,
            max_allowed_qty=max_allowed_qty,
            current_exposure=current_exposure,
            remaining_exposure=remaining,
            message=f"币种 {signal.symbol} 剩余暴露额度: {remaining} USD (跨策略合计)"
        )
    
    # ==================== 总暴露控制 ====================
    
    def check_total_exposure(
        self,
        positions: List[Position],
        total_max_exposure: Optional[Decimal] = None,
    ) -> TotalExposureResult:
        """
        检查总暴露控制
        
        Args:
            positions: 所有持仓列表
            total_max_exposure: 可选的覆盖配置的总最大暴露值
            
        Returns:
            TotalExposureResult: 检查结果
        """
        max_total = total_max_exposure or self._config.max_total_exposure
        
        # 计算当前总暴露（使用绝对值，多空仓位都计入暴露）
        total_current = sum(
            (abs(p.market_value) for p in positions if p.quantity != 0),
            Decimal("0")
        )
        
        # 计算剩余暴露
        remaining = max_total - total_current
        
        # 计算使用百分比
        if max_total > 0:
            exposure_percent = (total_current / max_total) * Decimal("100")
        else:
            exposure_percent = Decimal("100")
        
        # 边界检查
        if remaining <= 0:
            return TotalExposureResult(
                allowed=False,
                total_current_exposure=total_current,
                remaining_exposure=Decimal("0"),
                exposure_percent=exposure_percent,
                is_warning=True,
                rejection_reason="MAX_TOTAL_EXPOSURE_REACHED",
                message=f"总暴露已达上限 {max_total} USD"
            )
        
        # 警告检查
        is_warning = exposure_percent >= self._config.total_exposure_warning_threshold
        
        return TotalExposureResult(
            allowed=True,
            total_current_exposure=total_current,
            remaining_exposure=remaining,
            exposure_percent=exposure_percent,
            is_warning=is_warning,
            message=f"总暴露 {total_current} USD ({exposure_percent:.1f}%)，剩余 {remaining} USD"
        )
    
    # ==================== 冷却期管理 ====================
    
    def check_cooldown(
        self,
        symbol: str,
        current_time: Optional[datetime] = None,
    ) -> CooldownResult:
        """
        检查冷却期
        
        Args:
            symbol: 交易标的
            current_time: 可选的当前时间，默认使用 UTC now
            
        Returns:
            CooldownResult: 检查结果
        """
        # 如果未启用冷却期，直接通过
        if not self._config.cooldown_enabled:
            return CooldownResult(
                allowed=True,
                last_trade_time=None,
                message="冷却期检查已禁用"
            )
        
        # 获取当前时间
        now = current_time or datetime.now(timezone.utc)
        
        # 如果没有冷却期追踪器，无法检查
        if self._cooldown_tracker is None:
            return CooldownResult(
                allowed=True,
                last_trade_time=None,
                message="冷却期追踪器未配置，跳过检查"
            )
        
        # 获取上次交易时间
        last_trade = self._cooldown_tracker.get_last_trade_time(symbol)
        
        if last_trade is None:
            return CooldownResult(
                allowed=True,
                last_trade_time=None,
                message=f"币种 {symbol} 无交易历史"
            )
        
        # 计算冷却期剩余时间
        cooldown_duration = timedelta(seconds=self._config.cooldown_seconds)
        elapsed = now - last_trade
        remaining = cooldown_duration - elapsed
        
        if remaining.total_seconds() > 0:
            return CooldownResult(
                allowed=False,
                last_trade_time=last_trade,
                cooldown_remaining_seconds=remaining.total_seconds(),
                rejection_reason="IN_COOLDOWN",
                message=f"币种 {symbol} 冷却期中，剩余 {remaining.total_seconds():.0f} 秒"
            )
        
        return CooldownResult(
            allowed=True,
            last_trade_time=last_trade,
            cooldown_remaining_seconds=0.0,
            message=f"币种 {symbol} 冷却期已过"
        )
    
    # ==================== 最小交易阈值 ====================
    
    def check_min_threshold(
        self,
        signal: Signal,
        calculated_qty: Optional[Decimal] = None,
        min_threshold: Optional[Decimal] = None,
    ) -> MinThresholdResult:
        """
        检查最小交易阈值
        
        Args:
            signal: 交易信号
            calculated_qty: 计算出的交易量
            min_threshold: 可选的覆盖配置的最小阈值
            
        Returns:
            MinThresholdResult: 检查结果
        """
        threshold = min_threshold or self._config.min_trade_threshold
        
        # 如果未启用最小阈值检查，直接通过
        if not self._config.min_trade_threshold_enabled:
            return MinThresholdResult(
                allowed=True,
                signal_strength=signal.confidence,
                calculated_qty=calculated_qty or signal.quantity,
                min_threshold=threshold,
                message="最小阈值检查已禁用"
            )
        
        # 使用信号中的数量或计算出的数量
        qty = calculated_qty or signal.quantity
        
        # 计算交易金额
        if signal.price > 0:
            trade_value = qty * signal.price
        else:
            trade_value = Decimal("0")
        
        # 边界检查
        if trade_value < threshold:
            return MinThresholdResult(
                allowed=False,
                signal_strength=signal.confidence,
                calculated_qty=qty,
                min_threshold=threshold,
                rejection_reason="BELOW_MIN_THRESHOLD",
                message=f"交易金额 {trade_value} USD < 最小阈值 {threshold} USD"
            )
        
        return MinThresholdResult(
            allowed=True,
            signal_strength=signal.confidence,
            calculated_qty=qty,
            min_threshold=threshold,
            message=f"交易金额 {trade_value} USD >= 最小阈值 {threshold} USD"
        )
    
    # ==================== Regime 风险折扣 ====================
    
    def apply_regime_discount(
        self,
        signal_strength: Decimal,
        regime: Optional[MarketRegime] = None,
        symbol: Optional[str] = None,
    ) -> RegimeDiscountResult:
        """
        应用 Regime 风险折扣
        
        Args:
            signal_strength: 原始信号强度 (0-1)
            regime: 可选的市场状态，不提供则从 provider 获取
            symbol: 交易标的，用于从 provider 获取 regime
            
        Returns:
            RegimeDiscountResult: 折扣结果
        """
        # 获取 regime
        current_regime = regime
        if current_regime is None:
            if self._regime_provider is not None:
                if symbol:
                    current_regime = self._regime_provider.get_current_regime(symbol)
                else:
                    current_regime = self._config.regime_default
            else:
                current_regime = self._config.regime_default
        
        # 获取折扣因子
        discount = self._config.regime_discounts.get(current_regime, Decimal("1.0"))
        
        # 应用折扣
        adjusted = signal_strength * discount
        
        # 确保在有效范围内
        adjusted = max(Decimal("0"), min(Decimal("1"), adjusted))
        
        return RegimeDiscountResult(
            original_strength=signal_strength,
            adjusted_strength=adjusted,
            regime=current_regime,
            discount_factor=discount
        )
    
    # ==================== 完整风控检查 ====================
    
    def construct_position_risk(
        self,
        signal: Signal,
        positions: List[Position],
        current_position: Optional[Position] = None,
        current_time: Optional[datetime] = None,
    ) -> PositionRiskConstruction:
        """
        执行完整的仓位风险检查
        
        Args:
            signal: 交易信号
            positions: 所有持仓列表
            current_position: 当前标的持仓
            current_time: 当前时间
            
        Returns:
            PositionRiskConstruction: 综合风控结果
        """
        # 计算总投资组合价值（用于仓位占比限制）
        total_portfolio_value = sum(
            (abs(p.market_value) for p in positions if p.quantity != 0),
            Decimal("0")
        )
        
        # 1. 单币种最大暴露检查
        per_symbol_result = self.check_per_symbol_exposure(
            signal=signal,
            current_position=current_position,
            total_portfolio_value=total_portfolio_value,
        )
        
        # 2. 总暴露控制检查
        total_exposure_result = self.check_total_exposure(
            positions=positions,
        )
        
        # 3. 冷却期检查
        cooldown_result = self.check_cooldown(
            symbol=signal.symbol,
            current_time=current_time,
        )
        
        # 4. 最小交易阈值检查
        min_threshold_result = self.check_min_threshold(
            signal=signal,
            calculated_qty=per_symbol_result.max_allowed_qty,
        )
        
        # 5. Regime 风险折扣检查
        regime_discount_result = self.apply_regime_discount(
            signal_strength=signal.confidence,
            symbol=signal.symbol,
        )
        
        return PositionRiskConstruction(
            per_symbol_result=per_symbol_result,
            total_exposure_result=total_exposure_result,
            cooldown_result=cooldown_result,
            min_threshold_result=min_threshold_result,
            regime_discount_result=regime_discount_result,
        )
    
    # ==================== 辅助方法 ====================
    
    def calculate_adjusted_quantity(
        self,
        signal: Signal,
        positions: List[Position],
        current_position: Optional[Position] = None,
        current_time: Optional[datetime] = None,
    ) -> Optional[Decimal]:
        """
        计算调整后的交易数量
        
        综合所有风控检查后，返回允许的最大交易数量。
        如果任何检查未通过，返回 None。
        
        Args:
            signal: 交易信号
            positions: 所有持仓列表
            current_position: 当前标的持仓
            current_time: 当前时间
            
        Returns:
            Decimal: 允许的最大交易数量，None 表示不允许交易
        """
        construction = self.construct_position_risk(
            signal=signal,
            positions=positions,
            current_position=current_position,
            current_time=current_time,
        )
        
        if not construction.is_allowed:
            return None
        
        # 综合考虑单币种限制和 regime 折扣
        max_qty = construction.per_symbol_result.max_allowed_qty
        adjusted_confidence = construction.regime_discount_result.adjusted_strength
        
        # 应用调整后的信号强度
        adjusted_qty = max_qty * adjusted_confidence
        
        return adjusted_qty
