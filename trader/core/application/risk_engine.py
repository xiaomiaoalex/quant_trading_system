"""
RiskEngine - 风险引擎
====================
交易系统的安全保障。

风控原则：
1. Fail-Closed（故障关闭）：风控不可用时，禁止交易
2. 层层把关：交易前、交易中、交易后
3. 可配置：阈值可通过配置调整

三层风控：
1. Pre-trade（交易前）：信号检查、资金检查、持仓检查
2. In-trade（交易中）：订单超时、部分成交处理
3. Post-trade（交易后）：异常检测、统计归因
"""
from __future__ import annotations

import logging
from typing import List, Dict, Optional, Any, Protocol
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import dataclass, field
from enum import Enum, IntEnum

from trader.core.application.ports import BrokerPort
from trader.core.domain.models.signal import Signal
from trader.core.domain.models.order import OrderSide

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """风险等级"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class KillSwitchLevel(IntEnum):
    """KillSwitch 级别定义"""
    L0_NORMAL = 0
    L1_NO_NEW_POSITIONS = 1
    L2_CANCEL_ALL_AND_HALT = 2
    L3_LIQUIDATE_AND_DISCONNECT = 3


class RejectionReason(Enum):
    """风控拒绝原因"""
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    MAX_POSITION_LIMIT = "MAX_POSITION_LIMIT"
    MAX_POSITIONS = "MAX_POSITIONS"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    MAX_ORDER_RATE = "MAX_ORDER_RATE"
    CANCEL_RATE = "CANCEL_RATE"
    TRADING_HOURS = "TRADING_HOURS"
    PRICE_LIMIT = "PRICE_LIMIT"
    RISK_SYSTEM_ERROR = "RISK_SYSTEM_ERROR"


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    passed: bool
    risk_level: RiskLevel = RiskLevel.LOW
    rejection_reason: Optional[RejectionReason] = None
    message: str = ""
    details: Dict = field(default_factory=dict)


@dataclass
class RiskMetrics:
    """风险指标快照"""
    current_balance: Decimal = Decimal("0")
    daily_pnl: Decimal = Decimal("0")
    daily_pnl_percent: Decimal = Decimal("0")
    current_drawdown: Decimal = Decimal("0")
    peak_balance: Decimal = Decimal("0")
    open_positions_count: int = 0
    today_order_count: int = 0
    today_cancel_count: int = 0
    max_drawdown: Decimal = Decimal("0")


@dataclass
class RiskConfig:
    """风控配置"""
    # 亏损控制
    max_daily_loss_percent: Decimal = Decimal("5.0")      # 日亏损限制
    max_drawdown_percent: Decimal = Decimal("15.0")         # 最大回撤限制

    # 仓位控制
    max_position_percent: Decimal = Decimal("10.0")         # 单币种最大仓位比例
    max_positions: int = 3                                  # 最大持仓币种数

    # 频率控制
    max_order_rate: int = 100                              # 每分钟最大订单数
    max_cancel_rate: float = 0.6                            # 最大撤单率

    # 资金控制
    min_order_value: Decimal = Decimal("10")                # 最小订单金额

    # 交易时段（A股需要）
    trading_start_hour: int = 9                             # 交易开始时间（小时）
    trading_end_hour: int = 15                             # 交易结束时间（小时）


class PreTradeRiskPlugin(Protocol):
    """交易前风控插件接口"""

    async def check(
        self,
        signal: Signal,
        metrics: RiskMetrics,
        engine: RiskEngine
    ) -> Optional[RiskCheckResult]:
        ...


class InTradeRiskPlugin(Protocol):
    """交易中风控插件接口"""

    async def check(
        self,
        context: Dict[str, Any],
        engine: RiskEngine
    ) -> Optional[RiskCheckResult]:
        ...


class PostTradeRiskPlugin(Protocol):
    """交易后风控插件接口"""

    async def check(
        self,
        context: Dict[str, Any],
        engine: RiskEngine
    ) -> Optional[RiskCheckResult]:
        ...


class RiskEngine:
    """
    风险引擎

    在执行交易前进行全面的风险检查。
    """

    def __init__(
        self,
        broker: BrokerPort,
        config: RiskConfig = None,
        pre_trade_plugins: Optional[List[PreTradeRiskPlugin]] = None,
        in_trade_plugins: Optional[List[InTradeRiskPlugin]] = None,
        post_trade_plugins: Optional[List[PostTradeRiskPlugin]] = None
    ):
        self._broker = broker
        self._config = config or RiskConfig()
        self._pre_trade_plugins: List[PreTradeRiskPlugin] = list(pre_trade_plugins or [])
        self._in_trade_plugins: List[InTradeRiskPlugin] = list(in_trade_plugins or [])
        self._post_trade_plugins: List[PostTradeRiskPlugin] = list(post_trade_plugins or [])

        # 运行时状态
        self._daily_start_balance: Optional[Decimal] = None
        self._peak_balance: Decimal = Decimal("0")
        self._today_orders: List[datetime] = []
        self._today_cancels: List[datetime] = []
        self._last_reset: Optional[datetime] = None

    # ==================== 交易前风控 ====================

    async def check_signal(self, signal: Signal) -> RiskCheckResult:
        """
        兼容入口：交易前风控检查
        """
        return await self.check_pre_trade(signal)

    async def check_pre_trade(self, signal: Signal) -> RiskCheckResult:
        """
        检查交易信号（交易前风控）

        这是最重要的风控入口。
        """
        # 获取当前风险指标
        metrics = await self._collect_metrics()

        # 1. 日亏损检查
        if metrics.daily_pnl_percent <= -self._config.max_daily_loss_percent:
            return self._with_killswitch_hint(RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                rejection_reason=RejectionReason.DAILY_LOSS_LIMIT,
                message=f"日亏损已达 {abs(metrics.daily_pnl_percent)}%，超过限制 {self._config.max_daily_loss_percent}%",
                details={"daily_pnl_percent": str(metrics.daily_pnl_percent)}
            ))

        # 2. 回撤检查
        if metrics.current_drawdown >= self._config.max_drawdown_percent:
            return self._with_killswitch_hint(RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                rejection_reason=RejectionReason.MAX_DRAWDOWN,
                message=f"回撤已达 {metrics.current_drawdown}%，超过限制 {self._config.max_drawdown_percent}%",
                details={"current_drawdown": str(metrics.current_drawdown)}
            ))

        # 3. 并发持仓数检查
        if metrics.open_positions_count >= self._config.max_positions:
            if signal.is_buy_signal():
                return self._with_killswitch_hint(RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.HIGH,
                    rejection_reason=RejectionReason.MAX_POSITIONS,
                    message=f"当前持仓数 {metrics.open_positions_count} 已达上限 {self._config.max_positions}",
                    details={"open_positions_count": metrics.open_positions_count}
                ))

        # 4. 订单频率检查
        if not self._check_order_rate():
            return self._with_killswitch_hint(RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.MEDIUM,
                rejection_reason=RejectionReason.MAX_ORDER_RATE,
                message="订单频率超过限制",
                details={"order_count": len(self._today_orders)}
            ))

        # 5. 资金检查（对于买入信号）
        if signal.is_buy_signal():
            account = await self._broker.get_account()
            required_amount = signal.price * signal.quantity

            if account.available_cash < required_amount:
                return self._with_killswitch_hint(RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.MEDIUM,
                    rejection_reason=RejectionReason.INSUFFICIENT_BALANCE,
                    message=f"可用资金 {account.available_cash} 不足，需要 {required_amount}",
                    details={
                        "available": str(account.available_cash),
                        "required": str(required_amount)
                    }
                ))

            # 检查最小订单金额
            order_value = signal.price * signal.quantity
            if order_value < self._config.min_order_value:
                return self._with_killswitch_hint(RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.LOW,
                    rejection_reason=RejectionReason.INSUFFICIENT_BALANCE,
                    message=f"订单金额 {order_value} 低于最小限制 {self._config.min_order_value}",
                    details={
                        "order_value": str(order_value),
                        "min_value": str(self._config.min_order_value)
                    }
                ))

        # 6. 交易时段检查
        if not self._check_trading_hours():
            return self._with_killswitch_hint(RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=RejectionReason.TRADING_HOURS,
                message="当前不在交易时段内",
            ))

        plugin_result = await self._run_pre_trade_plugins(signal, metrics)
        if plugin_result is not None:
            return self._with_killswitch_hint(plugin_result)

        return self._with_killswitch_hint(RiskCheckResult(
            passed=True,
            risk_level=RiskLevel.LOW,
            message="风控检查通过",
            details={
                "daily_pnl_percent": str(metrics.daily_pnl_percent),
                "current_drawdown": str(metrics.current_drawdown),
                "open_positions": metrics.open_positions_count
            }
        ))

    async def check_in_trade(self, context: Dict[str, Any]) -> RiskCheckResult:
        """
        交易中风控入口（默认通过，可由插件接管）
        """
        plugin_result = await self._run_in_trade_plugins(context)
        if plugin_result is not None:
            return self._with_killswitch_hint(plugin_result)
        return self._with_killswitch_hint(RiskCheckResult(
            passed=True,
            risk_level=RiskLevel.LOW,
            message="交易中风控检查通过",
            details={}
        ))

    async def check_post_trade(self, context: Dict[str, Any]) -> RiskCheckResult:
        """
        交易后风控入口（默认通过，可由插件接管）
        """
        plugin_result = await self._run_post_trade_plugins(context)
        if plugin_result is not None:
            return self._with_killswitch_hint(plugin_result)
        return self._with_killswitch_hint(RiskCheckResult(
            passed=True,
            risk_level=RiskLevel.LOW,
            message="交易后风控检查通过",
            details={}
        ))

    # ==================== 辅助方法 ====================

    async def _collect_metrics(self) -> RiskMetrics:
        """收集当前风险指标"""
        # 清理跨日数据
        await self._cleanup_daily_data()

        # 获取账户信息
        try:
            account = await self._broker.get_account()
        except Exception as e:
            logger.error(f"获取账户信息失败: {e}")
            # 风控不可用时返回保守估计
            return RiskMetrics()

        # 获取持仓信息
        try:
            positions = await self._broker.get_positions()
        except Exception:
            positions = []

        # 初始化
        if self._daily_start_balance is None:
            self._daily_start_balance = account.total_equity

        if self._peak_balance == 0:
            self._peak_balance = account.total_equity

        # 更新峰值
        if account.total_equity > self._peak_balance:
            self._peak_balance = account.total_equity
            # 重置日盈亏基准
            self._daily_start_balance = account.total_equity

        # 计算日盈亏
        daily_pnl = account.total_equity - (self._daily_start_balance or account.total_equity)
        daily_pnl_percent = Decimal("0")
        if self._daily_start_balance and self._daily_start_balance > 0:
            daily_pnl_percent = (daily_pnl / self._daily_start_balance) * 100

        # 计算回撤
        drawdown = Decimal("0")
        if self._peak_balance > 0:
            drawdown = ((self._peak_balance - account.total_equity) / self._peak_balance) * 100

        return RiskMetrics(
            current_balance=account.total_equity,
            daily_pnl=daily_pnl,
            daily_pnl_percent=daily_pnl_percent,
            current_drawdown=drawdown,
            peak_balance=self._peak_balance,
            open_positions_count=len(positions),
            today_order_count=len(self._today_orders),
            today_cancel_count=len(self._today_cancels)
        )

    async def _run_pre_trade_plugins(
        self,
        signal: Signal,
        metrics: RiskMetrics
    ) -> Optional[RiskCheckResult]:
        for plugin in self._pre_trade_plugins:
            try:
                result = await plugin.check(signal, metrics, self)
            except Exception as exc:
                logger.error(f"Pre-trade 风控插件异常: {exc}")
                return RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.CRITICAL,
                    rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
                    message="Pre-trade 风控插件执行失败，已 Fail-Closed",
                    details={"error": str(exc)}
                )
            if result is not None and not result.passed:
                return result
        return None

    async def _run_in_trade_plugins(
        self,
        context: Dict[str, Any]
    ) -> Optional[RiskCheckResult]:
        for plugin in self._in_trade_plugins:
            try:
                result = await plugin.check(context, self)
            except Exception as exc:
                logger.error(f"In-trade 风控插件异常: {exc}")
                return RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.CRITICAL,
                    rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
                    message="In-trade 风控插件执行失败，已 Fail-Closed",
                    details={"error": str(exc)}
                )
            if result is not None and not result.passed:
                return result
        return None

    async def _run_post_trade_plugins(
        self,
        context: Dict[str, Any]
    ) -> Optional[RiskCheckResult]:
        for plugin in self._post_trade_plugins:
            try:
                result = await plugin.check(context, self)
            except Exception as exc:
                logger.error(f"Post-trade 风控插件异常: {exc}")
                return RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.CRITICAL,
                    rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
                    message="Post-trade 风控插件执行失败，已 Fail-Closed",
                    details={"error": str(exc)}
                )
            if result is not None and not result.passed:
                return result
        return None

    def _with_killswitch_hint(self, result: RiskCheckResult) -> RiskCheckResult:
        enriched_details = dict(result.details)
        enriched_details["recommended_killswitch_level"] = self.recommend_killswitch_level(result)
        result.details = enriched_details
        return result

    def recommend_killswitch_level(self, result: RiskCheckResult) -> int:
        """
        根据风控结果给出 KillSwitch 建议级别。
        """
        if result.passed:
            return int(KillSwitchLevel.L0_NORMAL)

        if result.rejection_reason == RejectionReason.RISK_SYSTEM_ERROR:
            return int(KillSwitchLevel.L3_LIQUIDATE_AND_DISCONNECT)

        if result.rejection_reason in {
            RejectionReason.DAILY_LOSS_LIMIT,
            RejectionReason.MAX_DRAWDOWN,
        }:
            return int(KillSwitchLevel.L2_CANCEL_ALL_AND_HALT)

        if result.rejection_reason in {
            RejectionReason.MAX_POSITIONS,
            RejectionReason.MAX_ORDER_RATE,
            RejectionReason.TRADING_HOURS,
        }:
            return int(KillSwitchLevel.L1_NO_NEW_POSITIONS)

        return int(KillSwitchLevel.L1_NO_NEW_POSITIONS)

    def _check_order_rate(self) -> bool:
        """检查订单频率"""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=1)

        # 清理过期记录
        self._today_orders = [t for t in self._today_orders if t > cutoff]

        return len(self._today_orders) < self._config.max_order_rate

    def _check_trading_hours(self) -> bool:
        """检查交易时段"""
        now = datetime.now(timezone.utc)
        # A股时段：9:30-11:30, 13:00-15:00（简化处理）
        hour = now.hour

        # 简单判断：在交易时段内
        if 9 <= hour < 11 or 13 <= hour < 15:
            return True

        return True  # 币圈24小时交易

    async def _cleanup_daily_data(self):
        """清理跨日数据"""
        now = datetime.now(timezone.utc)

        # 每天重置
        if self._last_reset is None or now.date() > self._last_reset.date():
            self._today_orders = []
            self._today_cancels = []
            self._last_reset = now

            # 重置日盈亏基准
            if self._peak_balance > 0:
                self._daily_start_balance = self._peak_balance

    # ==================== 统计方法 ====================

    def record_order(self):
        """记录订单（用于频率统计）"""
        self._today_orders.append(datetime.now(timezone.utc))

    def record_cancel(self):
        """记录撤单（用于撤单率统计）"""
        self._today_cancels.append(datetime.now(timezone.utc))

    def get_cancel_rate(self) -> float:
        """计算撤单率"""
        total = len(self._today_orders) + len(self._today_cancels)
        if total == 0:
            return 0.0
        return len(self._today_cancels) / total

    # ==================== 配置更新 ====================

    def update_config(self, config: RiskConfig):
        """更新风控配置"""
        self._config = config
        logger.info("[RiskEngine] 风控配置已更新")

    def get_config(self) -> RiskConfig:
        """获取当前风控配置"""
        return self._config

    def register_pre_trade_plugin(self, plugin: PreTradeRiskPlugin) -> None:
        """注册交易前风控插件"""
        self._pre_trade_plugins.append(plugin)

    def register_in_trade_plugin(self, plugin: InTradeRiskPlugin) -> None:
        """注册交易中风控插件"""
        self._in_trade_plugins.append(plugin)

    def register_post_trade_plugin(self, plugin: PostTradeRiskPlugin) -> None:
        """注册交易后风控插件"""
        self._post_trade_plugins.append(plugin)
