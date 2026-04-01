"""
Order Execution Simulator - 订单执行模拟器
============================================

提供回测环境下的订单执行模拟，支持：
- 方向感知滑点 (Direction-Aware Slippage)
- 下一K线开盘价执行 (Next Bar Open Execution)
- 止损止盈 (Stop-Loss / Take-Profit)
- 手续费计算

设计原则：
1. 消除未来函数：订单在下一K线执行
2. 滑点方向正确：买入时向不利方向滑，卖出时向不利方向滑
3. K线内执行：检查高低价触发SL/TP

Order Execution Flow:
    Bar N: Strategy generates signal
        -> Order queued for Bar N+1 execution
        -> Check SL/TP conditions using Bar N+1 high/low
        -> Execute at Bar N+1 open (or SL/TP if triggered)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List
from uuid import uuid4

from trader.core.domain.models.order import OrderSide
from trader.services.backtesting.ports import OHLCV


class ExitReason(Enum):
    """退出原因"""
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    SIGNAL = "SIGNAL"
    TIMEOUT = "TIMEOUT"


class SlippageModel(Enum):
    """滑点模型"""
    NO_SLIPPAGE = "no_slippage"
    FIXED = "fixed"
    PERCENTAGE = "percentage"
    VOLUME_BASED = "volume_based"


@dataclass(slots=True)
class ExecutionResult:
    """
    订单执行结果
    
    属性：
        execution_id: 执行唯一ID
        symbol: 交易标的
        side: 订单方向
        quantity: 成交数量
        price: 成交价格
        commission: 手续费
        slippage: 滑点成本
        slippage_rate: 实际滑点率
        timestamp: 成交时间
        exit_reason: 退出原因（用于平仓）
        entry_price: 开仓价格（用于平仓计算）
        bars_held: 持有K线数
    """
    execution_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: Decimal
    slippage: Decimal
    slippage_rate: Decimal
    timestamp: datetime
    exit_reason: Optional[ExitReason] = None
    entry_price: Optional[Decimal] = None
    bars_held: int = 0

    def __post_init__(self):
        if isinstance(self.quantity, (int, float)):
            object.__setattr__(self, 'quantity', Decimal(str(self.quantity)))
        if isinstance(self.price, (int, float)):
            object.__setattr__(self, 'price', Decimal(str(self.price)))
        if isinstance(self.commission, (int, float)):
            object.__setattr__(self, 'commission', Decimal(str(self.commission)))
        if isinstance(self.slippage, (int, float)):
            object.__setattr__(self, 'slippage', Decimal(str(self.slippage)))
        if isinstance(self.slippage_rate, (int, float)):
            object.__setattr__(self, 'slippage_rate', Decimal(str(self.slippage_rate)))
        if self.execution_id is None:
            object.__setattr__(self, 'execution_id', str(uuid4()))


@dataclass(slots=True)
class PendingOrder:
    """
    待执行订单
    
    属性：
        order_id: 订单ID
        symbol: 交易标的
        side: 订单方向
        quantity: 数量
        stop_loss: 止损价格（可选）
        take_profit: 止盈价格（可选）
        created_bar_index: 创建时的K线索引
        signal_price: 信号产生时的价格
    """
    order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    created_bar_index: int = 0
    signal_price: Optional[Decimal] = None

    def __post_init__(self):
        if isinstance(self.quantity, (int, float)):
            object.__setattr__(self, 'quantity', Decimal(str(self.quantity)))
        if isinstance(self.stop_loss, (int, float)):
            object.__setattr__(self, 'stop_loss', Decimal(str(self.stop_loss)))
        if isinstance(self.take_profit, (int, float)):
            object.__setattr__(self, 'take_profit', Decimal(str(self.take_profit)))
        if isinstance(self.signal_price, (int, float)):
            object.__setattr__(self, 'signal_price', Decimal(str(self.signal_price)))
        if self.order_id is None:
            object.__setattr__(self, 'order_id', str(uuid4()))


@dataclass(slots=True)
class OrderExecutionConfig:
    """
    订单执行配置
    
    属性：
        slippage_model: 滑点模型
        slippage_rate: 滑点率（百分比，如0.0005表示5bps）
        commission_rate: 手续费率（百分比）
        tp_percentage: 止盈百分比（相对于入场价，如0.02表示2%）
        sl_percentage: 止损百分比（相对于入场价，如0.01表示1%）
        max_bars_held: 最大持有K线数（超时退出）
        enable_slippage: 是否启用滑点
        enable_commission: 是否计算手续费
    """
    slippage_model: SlippageModel = SlippageModel.PERCENTAGE
    slippage_rate: Decimal = Decimal("0.0005")
    commission_rate: Decimal = Decimal("0.001")
    tp_percentage: Decimal = Decimal("0.02")
    sl_percentage: Decimal = Decimal("0.01")
    max_bars_held: int = 100
    enable_slippage: bool = True
    enable_commission: bool = True

    def __post_init__(self):
        if isinstance(self.slippage_rate, (int, float)):
            object.__setattr__(self, 'slippage_rate', Decimal(str(self.slippage_rate)))
        if isinstance(self.commission_rate, (int, float)):
            object.__setattr__(self, 'commission_rate', Decimal(str(self.commission_rate)))
        if isinstance(self.tp_percentage, (int, float)):
            object.__setattr__(self, 'tp_percentage', Decimal(str(self.tp_percentage)))
        if isinstance(self.sl_percentage, (int, float)):
            object.__setattr__(self, 'sl_percentage', Decimal(str(self.sl_percentage)))


class DirectionAwareSlippage:
    """
    方向感知滑点计算器
    
    买入(BUY)订单：执行价格 = 开盘价 * (1 + 滑点率)
    卖出(SELL)订单：执行价格 = 开盘价 * (1 - 滑点率)
    
    这样确保滑点总是对交易者不利：
    - 买入时：成交价高于开盘价
    - 卖出时：成交价低于开盘价
    """

    def __init__(self, slippage_rate: Decimal):
        """
        初始化滑点计算器
        
        Args:
            slippage_rate: 滑点率（如0.0005表示5bps）
        """
        self._slippage_rate = slippage_rate

    def calculate(
        self,
        open_price: Decimal,
        side: OrderSide,
        quantity: Optional[Decimal] = None,
        volume: Optional[Decimal] = None,
        model: SlippageModel = SlippageModel.PERCENTAGE,
    ) -> tuple[Decimal, Decimal]:
        """
        计算滑点后的执行价格
        
        Args:
            open_price: 开盘价
            side: 订单方向
            quantity: 订单数量（用于volume-based模型）
            volume: 成交量（用于volume-based模型）
            model: 滑点模型
            
        Returns:
            Tuple[执行价格, 滑点成本绝对值]
        """
        if model == SlippageModel.NO_SLIPPAGE:
            return open_price, Decimal("0")

        if model == SlippageModel.FIXED:
            slippage_amount = self._slippage_rate
        elif model == SlippageModel.PERCENTAGE:
            slippage_amount = open_price * self._slippage_rate
        elif model == SlippageModel.VOLUME_BASED:
            if volume and quantity:
                volume_ratio = quantity / volume if volume > 0 else Decimal("0")
                slippage_amount = open_price * self._slippage_rate * (Decimal("1") + volume_ratio)
            else:
                slippage_amount = open_price * self._slippage_rate
        else:
            slippage_amount = open_price * self._slippage_rate

        if side == OrderSide.BUY:
            execution_price = open_price + slippage_amount
        else:
            execution_price = open_price - slippage_amount

        return execution_price, abs(slippage_amount * quantity) if quantity else slippage_amount

    def calculate_rate(self, execution_price: Decimal, open_price: Decimal, side: OrderSide) -> Decimal:
        """
        计算实际滑点率
        
        Args:
            execution_price: 执行价格
            open_price: 开盘价
            side: 订单方向
            
        Returns:
            实际滑点率
        """
        if open_price == 0:
            return Decimal("0")
        price_diff = abs(execution_price - open_price)
        return price_diff / open_price


class NextBarOpenExecutor:
    """
    下一K线开盘价执行器
    
    核心机制：
    - 当前K线产生的信号，在下一K线开盘时执行
    - 消除回测中的未来函数/look-ahead bias
    
    执行流程：
    1. Bar N: 策略产生信号，订单进入pending队列
    2. Bar N+1: 检查SL/TP条件，然后以Bar N+1开盘价执行
    """

    def __init__(self, config: OrderExecutionConfig):
        self._config = config
        self._slippage_calculator = DirectionAwareSlippage(config.slippage_rate)
        self._pending_orders: Dict[str, PendingOrder] = {}

    def queue_order(self, order: PendingOrder) -> None:
        """
        将订单加入待执行队列
        
        Args:
            order: 待执行订单
        """
        self._pending_orders[order.order_id] = order

    def cancel_order(self, order_id: str) -> Optional[PendingOrder]:
        """
        取消待执行订单
        
        Args:
            order_id: 订单ID
            
        Returns:
            被取消的订单，如果不存在则返回None
        """
        return self._pending_orders.pop(order_id, None)

    def get_pending_orders(self) -> List[PendingOrder]:
        """获取所有待执行订单"""
        return list(self._pending_orders.values())

    def execute_pending(
        self,
        current_bar: OHLCV,
        bar_index: int,
    ) -> List[ExecutionResult]:
        """
        执行当前K线时到期的待执行订单
        
        Args:
            current_bar: 当前K线（作为下一K线执行）
            bar_index: 当前K线索引
            
        Returns:
            执行结果列表
        """
        results = []
        executed_ids = []

        for order_id, order in self._pending_orders.items():
            bars_held = bar_index - order.created_bar_index
            
            sl_triggered, sl_price = self._check_stop_loss(order, current_bar)
            tp_triggered, tp_price = self._check_take_profit(order, current_bar)
            
            execution_price: Decimal
            exit_reason: ExitReason
            
            if sl_triggered and tp_triggered:
                if order.side == OrderSide.BUY:
                    execution_price = tp_price
                    exit_reason = ExitReason.TAKE_PROFIT
                else:
                    execution_price = sl_price
                    exit_reason = ExitReason.STOP_LOSS
            elif tp_triggered:
                execution_price = tp_price
                exit_reason = ExitReason.TAKE_PROFIT
            elif sl_triggered:
                execution_price = sl_price
                exit_reason = ExitReason.STOP_LOSS
            elif bars_held >= self._config.max_bars_held:
                execution_price = current_bar.open
                exit_reason = ExitReason.TIMEOUT
            else:
                exec_price_raw, slippage = self._slippage_calculator.calculate(
                    current_bar.open,
                    order.side,
                    order.quantity,
                    current_bar.volume,
                    self._config.slippage_model,
                )
                execution_price = exec_price_raw
                slippage_cost = slippage
                exit_reason = ExitReason.SIGNAL

            commission = self._calculate_commission(order.quantity, execution_price)
            
            if self._config.enable_slippage and exit_reason in (ExitReason.SIGNAL, ExitReason.TIMEOUT):
                _, slippage_cost = self._slippage_calculator.calculate(
                    current_bar.open,
                    order.side,
                    order.quantity,
                    current_bar.volume,
                    self._config.slippage_model,
                )
            else:
                slippage_cost = Decimal("0")

            slippage_rate = self._slippage_calculator.calculate_rate(
                execution_price, current_bar.open, order.side
            )

            result = ExecutionResult(
                execution_id=str(uuid4()),
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=execution_price,
                commission=commission,
                slippage=slippage_cost,
                slippage_rate=slippage_rate,
                timestamp=current_bar.timestamp,
                exit_reason=exit_reason,
                entry_price=order.signal_price,
                bars_held=bars_held,
            )
            results.append(result)
            executed_ids.append(order_id)

        for order_id in executed_ids:
            del self._pending_orders[order_id]

        return results

    def _check_stop_loss(
        self,
        order: PendingOrder,
        bar: OHLCV,
    ) -> tuple[bool, Decimal]:
        """
        检查止损是否触发
        
        Args:
            order: 订单
            bar: K线数据
            
        Returns:
            Tuple[是否触发, 触发价格]
        """
        if order.stop_loss is None:
            return False, Decimal("0")

        if order.side == OrderSide.BUY:
            if bar.low < order.stop_loss:
                return True, order.stop_loss
        else:
            if bar.high > order.stop_loss:
                return True, order.stop_loss

        return False, Decimal("0")

    def _check_take_profit(
        self,
        order: PendingOrder,
        bar: OHLCV,
    ) -> tuple[bool, Decimal]:
        """
        检查止盈是否触发
        
        Args:
            order: 订单
            bar: K线数据
            
        Returns:
            Tuple[是否触发, 触发价格]
        """
        if order.take_profit is None:
            return False, Decimal("0")

        if order.side == OrderSide.BUY:
            if bar.high > order.take_profit:
                return True, order.take_profit
        else:
            if bar.low < order.take_profit:
                return True, order.take_profit

        return False, Decimal("0")

    def _calculate_commission(self, quantity: Decimal, price: Decimal) -> Decimal:
        """
        计算手续费
        
        公式：quantity * price * commission_rate
        
        Args:
            quantity: 成交数量
            price: 成交价格
            
        Returns:
            手续费金额
        """
        if not self._config.enable_commission:
            return Decimal("0")
        return quantity * price * self._config.commission_rate


class StopLossTakeProfitExecutor:
    """
    止损止盈执行器
    
    职责：
    1. 计算SL/TP价格（基于入场价和配置百分比）
    2. 检查K线内高低价是否触发SL/TP
    3. 返回退出原因
    
    计算公式：
    - BUY订单：
        - SL = entry_price * (1 - sl_percentage)
        - TP = entry_price * (1 + tp_percentage)
    - SELL订单：
        - SL = entry_price * (1 + sl_percentage)
        - TP = entry_price * (1 - tp_percentage)
    """

    def __init__(self, config: OrderExecutionConfig):
        self._config = config

    def calculate_levels(
        self,
        entry_price: Decimal,
        side: OrderSide,
    ) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """
        计算止损止盈价格
        
        Args:
            entry_price: 入场价格
            side: 订单方向
            
        Returns:
            Tuple[止损价格, 止盈价格]
        """
        stop_loss = None
        take_profit = None

        if self._config.sl_percentage > 0:
            if side == OrderSide.BUY:
                stop_loss = entry_price * (Decimal("1") - self._config.sl_percentage)
            else:
                stop_loss = entry_price * (Decimal("1") + self._config.sl_percentage)

        if self._config.tp_percentage > 0:
            if side == OrderSide.BUY:
                take_profit = entry_price * (Decimal("1") + self._config.tp_percentage)
            else:
                take_profit = entry_price * (Decimal("1") - self._config.tp_percentage)

        return stop_loss, take_profit

    def check_trigger(
        self,
        bar: OHLCV,
        side: OrderSide,
        stop_loss: Optional[Decimal],
        take_profit: Optional[Decimal],
    ) -> tuple[Optional[ExitReason], Optional[Decimal]]:
        """
        检查是否触发止损止盈
        
        K线内检查逻辑：
        - BUY订单：SL在低价区（触碰下限），TP在高价区（触碰上限）
        - SELL订单：SL在高价区（触碰上限），TP在低价区（触碰下限）
        
        执行优先级：
        1. 如果同时触发SL和TP，优先执行先触发的
        2. 如果在同一价格触发，比较bars_held
        
        Args:
            bar: K线数据
            side: 订单方向
            stop_loss: 止损价格
            take_profit: 止盈价格
            
        Returns:
            Tuple[退出原因, 触发价格]
        """
        if side == OrderSide.BUY:
            return self._check_trigger_buy(bar, stop_loss, take_profit)
        else:
            return self._check_trigger_sell(bar, stop_loss, take_profit)

    def _check_trigger_buy(
        self,
        bar: OHLCV,
        stop_loss: Optional[Decimal],
        take_profit: Optional[Decimal],
    ) -> tuple[Optional[ExitReason], Optional[Decimal]]:
        """检查BUY订单的SL/TP触发"""
        sl_triggered = False
        tp_triggered = False
        sl_price = None
        tp_price = None

        if stop_loss is not None:
            if bar.low < stop_loss:
                sl_triggered = True
                sl_price = bar.low

        if take_profit is not None:
            if bar.high > take_profit:
                tp_triggered = True
                tp_price = bar.high

        if sl_triggered and tp_triggered:
            return ExitReason.STOP_LOSS, sl_price
        elif sl_triggered:
            return ExitReason.STOP_LOSS, sl_price
        elif tp_triggered:
            return ExitReason.TAKE_PROFIT, tp_price

        return None, None

    def _check_trigger_sell(
        self,
        bar: OHLCV,
        stop_loss: Optional[Decimal],
        take_profit: Optional[Decimal],
    ) -> tuple[Optional[ExitReason], Optional[Decimal]]:
        """检查SELL订单的SL/TP触发"""
        sl_triggered = False
        tp_triggered = False
        sl_price = None
        tp_price = None

        if stop_loss is not None:
            if bar.high > stop_loss:
                sl_triggered = True
                sl_price = bar.high

        if take_profit is not None:
            if bar.low < take_profit:
                tp_triggered = True
                tp_price = bar.low

        if sl_triggered and tp_triggered:
            return ExitReason.STOP_LOSS, sl_price
        elif sl_triggered:
            return ExitReason.STOP_LOSS, sl_price
        elif tp_triggered:
            return ExitReason.TAKE_PROFIT, tp_price

        return None, None


@dataclass
class PositionState:
    """
    持仓状态
    
    用于跟踪当前持仓及其SL/TP触发状态
    """
    position_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    entry_price: Decimal
    entry_bar_index: int
    entry_timestamp: datetime
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    current_bar_index: int = 0

    def __post_init__(self):
        if isinstance(self.quantity, (int, float)):
            object.__setattr__(self, 'quantity', Decimal(str(self.quantity)))
        if isinstance(self.entry_price, (int, float)):
            object.__setattr__(self, 'entry_price', Decimal(str(self.entry_price)))
        if isinstance(self.stop_loss, (int, float)):
            object.__setattr__(self, 'stop_loss', Decimal(str(self.stop_loss)))
        if isinstance(self.take_profit, (int, float)):
            object.__setattr__(self, 'take_profit', Decimal(str(self.take_profit)))


class ExecutionSimulator:
    """
    订单执行模拟器（ facade类）
    
    整合所有执行组件，提供统一的回测执行接口。
    
    使用示例：
        config = OrderExecutionConfig(
            slippage_rate=Decimal("0.0005"),
            commission_rate=Decimal("0.001"),
            tp_percentage=Decimal("0.02"),
            sl_percentage=Decimal("0.01"),
        )
        
        simulator = ExecutionSimulator(config)
        
        # 添加持仓
        simulator.open_position(symbol, side, quantity, entry_bar, entry_price)
        
        # 处理K线
        for bar in bars:
            exits = simulator.check_exits(bar)
            entries = simulator.execute_pending(bar)
            # 处理exits和entries...
    """

    def __init__(self, config: OrderExecutionConfig):
        self._config = config
        self._entry_executor = NextBarOpenExecutor(config)
        self._exit_executor = StopLossTakeProfitExecutor(config)
        self._positions: Dict[str, PositionState] = {}
        self._closed_positions: List[ExecutionResult] = []
        self._bar_index = 0

    @property
    def config(self) -> OrderExecutionConfig:
        """获取配置"""
        return self._config

    @property
    def positions(self) -> Dict[str, PositionState]:
        """获取当前持仓"""
        return self._positions

    @property
    def closed_positions(self) -> List[ExecutionResult]:
        """获取已平仓记录"""
        return self._closed_positions

    def queue_entry(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        signal_price: Optional[Decimal] = None,
    ) -> PendingOrder:
        """
        队列入场订单
        
        Args:
            symbol: 交易标的
            side: 订单方向
            quantity: 数量
            stop_loss: 止损价格（可选）
            take_profit: 止盈价格（可选）
            signal_price: 信号价格
            
        Returns:
            PendingOrder: 待执行订单
        """
        order = PendingOrder(
            order_id=str(uuid4()),
            symbol=symbol,
            side=side,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            created_bar_index=self._bar_index,
            signal_price=signal_price,
        )
        self._entry_executor.queue_order(order)
        return order

    def open_position(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        entry_bar_index: int,
        entry_price: Decimal,
        timestamp: datetime,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> PositionState:
        """
        开仓（直接创建持仓，用于回测引擎）
        
        Args:
            symbol: 交易标的
            side: 订单方向
            quantity: 数量
            entry_bar_index: 入场K线索引
            entry_price: 入场价格
            timestamp: 入场时间
            stop_loss: 止损价格
            take_profit: 止盈价格
            
        Returns:
            PositionState: 持仓状态
        """
        position = PositionState(
            position_id=str(uuid4()),
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            entry_bar_index=entry_bar_index,
            entry_timestamp=timestamp,
            stop_loss=stop_loss,
            take_profit=take_profit,
            current_bar_index=entry_bar_index,
        )
        self._positions[symbol] = position
        return position

    def check_exits(self, bar: OHLCV) -> List[ExecutionResult]:
        """
        检查所有持仓是否触发SL/TP
        
        Args:
            bar: 当前K线
            
        Returns:
            List[ExecutionResult]: 触发的退出记录
        """
        results = []
        triggered_positions = []

        for symbol, position in self._positions.items():
            if position.symbol != symbol:
                continue

            position.current_bar_index += 1
            bars_held = position.current_bar_index - position.entry_bar_index

            if position.stop_loss is None and position.take_profit is None:
                if bars_held >= self._config.max_bars_held:
                    commission = self._calculate_total_commission(position.quantity, bar.open)
                    result = ExecutionResult(
                        execution_id=str(uuid4()),
                        symbol=symbol,
                        side=OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY,
                        quantity=position.quantity,
                        price=bar.open,
                        commission=commission,
                        slippage=Decimal("0"),
                        slippage_rate=Decimal("0"),
                        timestamp=bar.timestamp,
                        exit_reason=ExitReason.TIMEOUT,
                        entry_price=position.entry_price,
                        bars_held=bars_held,
                    )
                    results.append(result)
                    triggered_positions.append(symbol)
                continue

            exit_reason, trigger_price = self._exit_executor.check_trigger(
                bar,
                position.side,
                position.stop_loss,
                position.take_profit,
            )

            if exit_reason is not None:
                assert trigger_price is not None, "trigger_price must not be None when exit_reason is set"
                commission = self._calculate_total_commission(position.quantity, trigger_price)
                result = ExecutionResult(
                    execution_id=str(uuid4()),
                    symbol=symbol,
                    side=OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY,
                    quantity=position.quantity,
                    price=trigger_price,
                    commission=commission,
                    slippage=Decimal("0"),
                    slippage_rate=Decimal("0"),
                    timestamp=bar.timestamp,
                    exit_reason=exit_reason,
                    entry_price=position.entry_price,
                    bars_held=bars_held,
                )
                results.append(result)
                triggered_positions.append(symbol)

        for symbol in triggered_positions:
            del self._positions[symbol]

        self._closed_positions.extend(results)
        return results

    def execute_pending(self, bar: OHLCV) -> List[ExecutionResult]:
        """
        执行待执行订单
        
        Args:
            bar: 当前K线
            
        Returns:
            List[ExecutionResult]: 入场执行结果
        """
        self._bar_index += 1
        results = self._entry_executor.execute_pending(bar, self._bar_index)

        for result in results:
            if result.exit_reason == ExitReason.SIGNAL:
                stop_loss, take_profit = self._exit_executor.calculate_levels(
                    result.price, result.side
                )
                self.open_position(
                    symbol=result.symbol,
                    side=result.side,
                    quantity=result.quantity,
                    entry_bar_index=self._bar_index,
                    entry_price=result.price,
                    timestamp=result.timestamp,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )

        return results

    def process_bar(self, bar: OHLCV) -> tuple[List[ExecutionResult], List[ExecutionResult]]:
        """
        处理单个K线
        
        顺序：先检查退出，再执行入场
        
        Args:
            bar: 当前K线
            
        Returns:
            Tuple[退出结果列表, 入场结果列表]
        """
        exits = self.check_exits(bar)
        entries = self.execute_pending(bar)
        return exits, entries

    def _calculate_total_commission(self, quantity: Decimal, price: Decimal) -> Decimal:
        """计算双边手续费（入场+出场）"""
        if not self._config.enable_commission:
            return Decimal("0")
        single_side = quantity * price * self._config.commission_rate
        return single_side * Decimal("2")

    def reset(self) -> None:
        """重置模拟器状态"""
        self._positions.clear()
        self._closed_positions.clear()
        self._bar_index = 0
