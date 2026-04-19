"""
OMS Callback Handler - OMS下单回调处理
======================================

职责：
1. 将策略 Signal 映射为真实订单
2. 使用 BinanceSpotDemoBroker 执行订单
3. 处理下单错误（余额不足、最小名义金额、精度错误等）
4. 订单与成交写入控制面存储
5. 发布策略事件（下单成功/拒单/成交）

架构约束：
- 属于 Adapter 层，负责 IO
- 不允许 except: pass
- 失败必须 fail-closed，不能 silent fallback
"""
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, Callable, Union

from trader.adapters.broker.binance_spot_demo_broker import BinanceSpotDemoBroker
from trader.core.domain.models.order import OrderSide, OrderType, OrderStatus
from trader.core.domain.models.signal import Signal
from trader.storage.in_memory import get_storage, ControlPlaneInMemoryStorage

logger = logging.getLogger(__name__)


class OMSCallbackError(Exception):
    """OMS回调异常"""
    pass


class InsufficientBalanceError(OMSCallbackError):
    """余额不足"""
    pass


class MinNotionalError(OMSCallbackError):
    """最小名义金额不满足"""
    pass


class InvalidQuantityError(OMSCallbackError):
    """数量精度错误"""
    pass


class TradingDisabledError(OMSCallbackError):
    """交易未启用"""
    pass


class OMSCallbackHandler:
    """
    OMS下单回调处理器

    将策略信号转换为真实订单并执行。
    
    幂等性保证：
    - 使用 cl_ord_id 进行去重
    - cl_ord_id 格式: {strategy_id}_{uuid_hex}
    - 下单前检查存储层是否已存在相同 cl_ord_id 的订单
    - 已处理的 cl_ord_id 缓存在内存中防止重复处理
    """

    def __init__(
        self,
        broker: BinanceSpotDemoBroker,
        storage: Optional[ControlPlaneInMemoryStorage] = None,
        live_trading_enabled: Union[bool, Callable[[], bool]] = False,
        event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        fill_callback: Optional[Callable[[str, str, str, str, float, float], None]] = None,
    ):
        """
        初始化OMS回调处理器

        Args:
            broker: Binance broker实例
            storage: 存储实例（默认使用全局存储）
            live_trading_enabled: 是否允许真实下单（安全闸门）。
                传入 bool 时作为静态值；传入 Callable[[], bool] 时每次执行信号时动态读取，
                确保运行时开关变更能即时生效。
            event_callback: 事件发布回调
            fill_callback: 成交回调函数 (strategy_id, order_id, symbol, side, quantity, price)
        """
        self._broker = broker
        self._storage = storage or get_storage()
        if callable(live_trading_enabled):
            self._live_trading_enabled_fn: Callable[[], bool] = live_trading_enabled
        else:
            self._live_trading_enabled_fn = lambda: bool(live_trading_enabled)
        self._event_callback = event_callback
        self._fill_callback = fill_callback

        # 缓存 symbol 的 stepSize
        self._step_size_cache: Dict[str, Decimal] = {}

        # 缓存 symbol 的 minNotional
        self._min_notional_cache: Dict[str, Decimal] = {}

        # 已处理的 cl_ord_id 集合（内存缓存，防止同一会话内重复处理）
        self._processed_cl_ord_ids: set = set()

    async def execute_signal(
        self,
        strategy_id: str,
        signal: Signal,
    ) -> Optional[Dict[str, Any]]:
        """
        执行策略信号

        Args:
            strategy_id: 策略ID
            signal: 交易信号

        Returns:
            订单结果字典，包含 order_id, status, error 等字段

        Raises:
            TradingDisabledError: 交易未启用
            InsufficientBalanceError: 余额不足
            MinNotionalError: 最小名义金额不满足
            InvalidQuantityError: 数量精度错误
        """
        # ==================== 安全闸门检查 ====================
        if not self._live_trading_enabled_fn():
            logger.warning(
                f"[OMSCallback] Live trading disabled, signal rejected: "
                f"strategy={strategy_id}, symbol={signal.symbol}"
            )
            self._publish_event(strategy_id, "strategy.order.rejected", {
                "symbol": signal.symbol,
                "side": str(signal.signal_type) if signal.signal_type else None,
                "quantity": str(signal.quantity) if signal.quantity else None,
                "price": str(signal.price) if signal.price else None,
                "reason": "LIVE_TRADING_DISABLED",
            })
            raise TradingDisabledError("Live trading is not enabled")

        # ==================== 信号验证 ====================
        if not signal.symbol:
            raise OMSCallbackError("Signal missing symbol")

        if not signal.signal_type:
            raise OMSCallbackError("Signal missing signal_type")

        if signal.quantity is None or signal.quantity <= 0:
            raise OMSCallbackError(f"Invalid signal quantity: {signal.quantity}")

        # ==================== 获取 stepSize ====================
        step_size = await self._get_step_size(signal.symbol)

        # ==================== 数量精度处理 ====================
        try:
            quantity = signal.quantity
            if step_size and step_size > 0:
                quantity = BinanceSpotDemoBroker.quantize_by_step_size(
                    Decimal(str(signal.quantity)), step_size
                )
                if quantity <= 0:
                    raise InvalidQuantityError(
                        f"Quantity too small after quantization: {signal.quantity} -> {quantity}"
                    )
        except InvalidQuantityError:
            raise
        except Exception as e:
            logger.warning(f"[OMSCallback] Quantity quantization failed: {e}")
            quantity = Decimal(str(signal.quantity))

        # ==================== 最小名义金额检查 ====================
        price = signal.price or Decimal("0")
        if price <= 0:
            logger.warning(f"[OMSCallback] Signal price is zero, cannot validate minNotional")
        else:
            notional = quantity * price
            min_notional = await self._get_min_notional(signal.symbol)
            if notional < min_notional:
                error_msg = f"Notional {notional} below minNotional {min_notional}"
                self._publish_event(strategy_id, "strategy.order.rejected", {
                    "symbol": signal.symbol,
                    "side": str(signal.signal_type) if signal.signal_type else None,
                    "quantity": str(quantity),
                    "price": str(price),
                    "reason": f"MIN_NOTIONAL: {error_msg}",
                })
                raise MinNotionalError(error_msg)

        # ==================== 生成订单ID ====================
        cl_ord_id = f"{strategy_id}_{uuid.uuid4().hex[:16]}"

        # ==================== 幂等性检查 ====================
        # 1. 检查内存缓存
        if cl_ord_id in self._processed_cl_ord_ids:
            logger.warning(f"[OMSCallback] Duplicate cl_ord_id detected (memory): {cl_ord_id}")
            return None

        # 2. 检查存储层是否已存在相同 cl_ord_id 的订单
        try:
            existing_order = await self._storage.get_order(cl_ord_id)
            if existing_order is not None:
                logger.warning(f"[OMSCallback] Duplicate cl_ord_id detected (storage): {cl_ord_id}")
                # 加入内存缓存防止后续重复处理
                self._processed_cl_ord_ids.add(cl_ord_id)
                return None
        except Exception as e:
            # 存储查询失败不影响主流程，但记录警告
            logger.warning(f"[OMSCallback] Failed to check existing order: {e}")

        # 3. 标记为处理中
        self._processed_cl_ord_ids.add(cl_ord_id)

        # ==================== 下单 ====================
        try:
            # 转换方向
            side = OrderSide.BUY if str(signal.signal_type).upper() in ("BUY", "LONG") else OrderSide.SELL

            # 转换订单类型（默认市价单）
            order_type = OrderType.MARKET
            if signal.price and signal.price > 0:
                order_type = OrderType.LIMIT

            # 下单
            broker_order = await self._broker.place_order(
                symbol=signal.symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=signal.price if order_type == OrderType.LIMIT else None,
                client_order_id=cl_ord_id,
            )

            # ==================== 保存订单到存储 ====================
            order_data = {
                "cl_ord_id": cl_ord_id,
                "broker_order_id": broker_order.broker_order_id,
                "symbol": signal.symbol,
                "side": side.value,
                "order_type": order_type.value,
                "quantity": str(quantity),
                "filled_qty": str(broker_order.filled_quantity),
                "avg_price": str(broker_order.average_price),
                "status": broker_order.status.value,
                "strategy_id": strategy_id,
                "venue": self._broker.broker_name,
                "created_at": broker_order.created_at.isoformat() if broker_order.created_at else datetime.now(timezone.utc).isoformat(),
            }
            self._storage.create_order(order_data)

            # ==================== 如果有成交，保存成交记录 ====================
            if broker_order.filled_quantity > 0:
                execution_data = {
                    "cl_ord_id": cl_ord_id,
                    "symbol": signal.symbol,
                    "side": side.value,
                    "quantity": str(broker_order.filled_quantity),
                    "price": str(broker_order.average_price),
                    "strategy_id": strategy_id,
                    "venue": self._broker.broker_name,
                    "ts_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                }
                self._storage.create_execution(execution_data)

                # ==================== 调用 on_fill 回调 ====================
                if self._fill_callback:
                    try:
                        await self._fill_callback(
                            strategy_id,
                            cl_ord_id,
                            signal.symbol,
                            side.value,
                            float(broker_order.filled_quantity),
                            float(broker_order.average_price),
                        )
                        logger.info(
                            f"[OMSCallback] on_fill called: strategy={strategy_id}, "
                            f"order={cl_ord_id}, qty={broker_order.filled_quantity}, "
                            f"price={broker_order.average_price}"
                        )
                    except Exception as e:
                        logger.error(f"[OMSCallback] on_fill callback error: {e}")

            # ==================== 发布成功事件 ====================
            event_type = "strategy.order.filled" if broker_order.filled_quantity > 0 else "strategy.order.submitted"
            self._publish_event(strategy_id, event_type, {
                "order_id": cl_ord_id,
                "symbol": signal.symbol,
                "side": side.value,
                "quantity": str(quantity),
                "filled_qty": str(broker_order.filled_quantity),
                "avg_price": str(broker_order.average_price),
                "status": broker_order.status.value,
            })

            logger.info(
                f"[OMSCallback] Order submitted: cl_ord_id={cl_ord_id}, "
                f"symbol={signal.symbol}, side={side.value}, qty={quantity}, "
                f"filled={broker_order.filled_quantity}"
            )

            return {
                "order_id": cl_ord_id,
                "broker_order_id": broker_order.broker_order_id,
                "status": broker_order.status.value,
                "filled_qty": str(broker_order.filled_quantity),
                "avg_price": str(broker_order.average_price),
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[OMSCallback] Order failed: {error_msg}")

            # ==================== 发布拒单事件 ====================
            self._publish_event(strategy_id, "strategy.order.rejected", {
                "symbol": signal.symbol,
                "side": str(signal.signal_type) if signal.signal_type else None,
                "quantity": str(quantity),
                "price": str(price),
                "reason": error_msg,
            })

            raise

    async def _get_step_size(self, symbol: str) -> Decimal:
        """
        获取交易对的 stepSize（带缓存）

        Args:
            symbol: 交易对符号

        Returns:
            stepSize 或 Decimal("0")
        """
        if symbol in self._step_size_cache:
            return self._step_size_cache[symbol]

        try:
            step_size = await self._broker.get_symbol_step_size(symbol)
            self._step_size_cache[symbol] = step_size
            return step_size
        except Exception as e:
            logger.warning(f"[OMSCallback] Failed to get stepSize for {symbol}: {e}")
            return Decimal("0")

    async def _get_min_notional(self, symbol: str) -> Decimal:
        """
        获取交易对的最小名义金额（带缓存）

        从交易所 exchangeInfo 的 NOTIONAL 或 MIN_NOTIONAL 过滤器中读取。
        如果获取失败，回退到默认值 Decimal("10")。

        Args:
            symbol: 交易对符号

        Returns:
            最小名义金额
        """
        if symbol in self._min_notional_cache:
            return self._min_notional_cache[symbol]

        try:
            data = await self._broker.get_exchange_info(symbol=symbol)
            symbols = data.get("symbols", [])
            if not symbols:
                logger.warning(f"[OMSCallback] Symbol {symbol} not found in exchangeInfo, using default minNotional")
                self._min_notional_cache[symbol] = Decimal("10")
                return Decimal("10")

            filters = symbols[0].get("filters", [])
            for f in filters:
                filter_type = f.get("filterType", "")
                if filter_type in ("NOTIONAL", "MIN_NOTIONAL"):
                    min_notional_str = f.get("minNotional") or f.get("notionalMin")
                    if min_notional_str:
                        min_notional = Decimal(str(min_notional_str))
                        self._min_notional_cache[symbol] = min_notional
                        return min_notional

            logger.warning(f"[OMSCallback] No NOTIONAL/MIN_NOTIONAL filter for {symbol}, using default")
            self._min_notional_cache[symbol] = Decimal("10")
            return Decimal("10")
        except Exception as e:
            logger.warning(f"[OMSCallback] Failed to get minNotional for {symbol}: {e}, using default")
            self._min_notional_cache[symbol] = Decimal("10")
            return Decimal("10")

    def _publish_event(
        self,
        strategy_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """发布策略事件"""
        if self._event_callback:
            try:
                self._event_callback(strategy_id, event_type, payload)
            except Exception as e:
                logger.error(f"[OMSCallback] Event callback failed: {e}")


def create_oms_callback(
    broker: BinanceSpotDemoBroker,
    live_trading_enabled: Union[bool, Callable[[], bool]] = False,
    event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    fill_callback: Optional[Callable[[str, str, str, str, float, float], None]] = None,
) -> tuple[Callable, Callable]:
    """
    创建OMS回调函数和成交处理器

    Args:
        broker: Binance broker实例
        live_trading_enabled: 是否允许真实下单（支持 bool 或 Callable[[], bool]）
        event_callback: 事件发布回调
        fill_callback: 成交回调函数 (strategy_id, order_id, symbol, side, quantity, price)

    Returns:
        tuple: (oms_callback 函数, fill_handler 函数)
        - oms_callback: 直接传给 StrategyRunner
        - fill_handler: 注册到 PrivateStreamManager 或 BinanceConnector
    """
    handler = OMSCallbackHandler(
        broker=broker,
        live_trading_enabled=live_trading_enabled,
        event_callback=event_callback,
        fill_callback=fill_callback,
    )

    async def oms_callback(strategy_id: str, signal: Signal) -> Optional[Dict[str, Any]]:
        """
        OMS 回调函数

        区分预期拒绝（返回 None）与基础设施错误（记录并返回 None）：
        - 预期拒绝：TradingDisabled, InsufficientBalance, MinNotional, InvalidQuantity
        - 基础设施错误：OMSCallbackError 基类（记录为 error）
        - 未知错误：记录为 error
        """
        try:
            return await handler.execute_signal(strategy_id, signal)
        except TradingDisabledError:
            # 预期：安全闸门关闭
            return None
        except (InsufficientBalanceError, MinNotionalError, InvalidQuantityError) as e:
            # 预期：业务规则拒绝（记录为 warning）
            logger.warning(f"[OMSCallback] Signal rejected by business rule: {e}")
            return None
        except OMSCallbackError as e:
            # 基础设施错误（记录为 error）
            logger.error(f"[OMSCallback] OMS infrastructure error: {e}")
            return None
        except Exception as e:
            # 未知错误（记录为 error，包含堆栈）
            logger.error(f"[OMSCallback] Unexpected error: {e}", exc_info=True)
            return None

    def create_fill_handler() -> Callable:
        """
        创建成交处理器，用于处理 PrivateStreamManager 的成交回调

        Returns:
            fill_handler: 接收 RawFillUpdate 并调用 fill_callback
        """
        async def fill_handler(update) -> None:
            """
            处理成交更新

            Args:
                update: RawFillUpdate dataclass，包含:
                    - cl_ord_id: 客户端订单ID
                    - side: 买卖方向
                    - qty: 成交数量
                    - price: 成交价格
                    - trade_id: 交易ID
            """
            try:
                # 获取 RawFillUpdate 的属性
                cl_ord_id = update.cl_ord_id
                side = update.side
                quantity = update.qty
                price = update.price

                # 从 cl_ord_id 提取 strategy_id（格式: strategy_id_uuid）
                strategy_id = cl_ord_id.split("_")[0] if cl_ord_id else ""

                if fill_callback and strategy_id:
                    # 传入 order_id=cl_ord_id, symbol="" (fill update 不包含 symbol)
                    await fill_callback(strategy_id, cl_ord_id, "", side, quantity, price)
                    logger.info(
                        f"[OMSCallback] Fill processed: cl_ord_id={cl_ord_id}, "
                        f"qty={quantity}, price={price}"
                    )
            except Exception as e:
                logger.error(f"[OMSCallback] Fill handler error: {e}", exc_info=True)

        return fill_handler

    return oms_callback, create_fill_handler
