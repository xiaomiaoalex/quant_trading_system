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
import asyncio
import logging
import time
import uuid
import inspect
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, Callable, Union

from trader.adapters.broker.binance_spot_demo_broker import BinanceSpotDemoBroker
from trader.core.domain.models.order import OrderSide, OrderType, OrderStatus
from trader.core.domain.models.signal import Signal
from trader.core.domain.services.position_lot_registry import (
    get_lot_manager,
    set_lot_manager,
)
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
        position_lot_manager: Any = None,  # None = 使用全局单例
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
            position_lot_manager: None = 使用全局单例 PositionLedgerManager
        """
        self._broker = broker
        self._storage = storage or get_storage()
        if callable(live_trading_enabled):
            self._live_trading_enabled_fn: Callable[[], bool] = live_trading_enabled
        else:
            self._live_trading_enabled_fn = lambda: bool(live_trading_enabled)
        self._event_callback = event_callback
        self._fill_callback = fill_callback
        # None = 从全局 registry 获取（推荐方式）
        self._position_lot_manager = position_lot_manager

        self._position_locks: Dict[str, asyncio.Lock] = {}

        # 缓存 symbol 的 stepSize (value, cached_at)
        self._step_size_cache: Dict[str, tuple[Decimal, float]] = {}

        # 缓存 symbol 的 minNotional (value, cached_at)
        self._min_notional_cache: Dict[str, tuple[Decimal, float]] = {}

        self._symbol_cache_ttl_seconds: int = 3600

        self._processed_cl_ord_ids: Dict[str, float] = {}
        self._cl_ord_id_to_strategy: Dict[str, str] = {}
        self._processed_exec_keys: Dict[str, float] = {}
        self._exec_dedup_ttl_seconds: int = 900
        self._cl_ord_id_ttl_seconds: int = 3600

        self._cl_ord_id_dedup_hits: int = 0
        self._exec_dedup_hits: int = 0

        self._order_submit_ok: int = 0
        self._order_submit_reject: int = 0
        self._order_submit_error: int = 0
        self._reject_reason_counts: Dict[str, int] = {}
        self._fill_latency_sum_ms: float = 0.0
        self._fill_latency_count: int = 0

    @property
    def _lot_mgr(self):
        """获取 PositionLedgerManager：优先注入，否则用全局单例"""
        return self._position_lot_manager or get_lot_manager()

    def _get_position_lock(self, strategy_id: str, symbol: str) -> asyncio.Lock:
        key = f"{strategy_id}:{symbol}"
        if key not in self._position_locks:
            self._position_locks[key] = asyncio.Lock()
        return self._position_locks[key]

    def _cleanup_idle_position_locks(self) -> int:
        """清理未被持有的持仓锁，释放内存。"""
        idle_keys = [
            k for k, lock in self._position_locks.items()
            if not lock.locked() and not lock._waiters
        ]
        for k in idle_keys:
            del self._position_locks[k]
        return len(idle_keys)

    def cleanup_expired_dedup_cache(self) -> int:
        """
        清理过期的幂等去重缓存，释放内存。

        Returns:
            清理的总条目数
        """
        now = time.time()
        expired_cl = [
            k for k, ts in self._processed_cl_ord_ids.items()
            if now - ts > self._cl_ord_id_ttl_seconds
        ]
        for k in expired_cl:
            del self._processed_cl_ord_ids[k]
            self._cl_ord_id_to_strategy.pop(k, None)

        expired_exec = [
            k for k, ts in self._processed_exec_keys.items()
            if now - ts > self._exec_dedup_ttl_seconds
        ]
        for k in expired_exec:
            del self._processed_exec_keys[k]

        total = len(expired_cl) + len(expired_exec)

        expired_step = [
            k for k, (_, cached_at) in self._step_size_cache.items()
            if now - cached_at > self._symbol_cache_ttl_seconds
        ]
        for k in expired_step:
            del self._step_size_cache[k]

        expired_min = [
            k for k, (_, cached_at) in self._min_notional_cache.items()
            if now - cached_at > self._symbol_cache_ttl_seconds
        ]
        for k in expired_min:
            del self._min_notional_cache[k]

        total += len(expired_step) + len(expired_min)

        idle_locks = self._cleanup_idle_position_locks()
        total += idle_locks
        if total:
            logger.info(
                f"[OMSCallback] Cleaned up dedup cache: "
                f"{len(expired_cl)} cl_ord_ids, {len(expired_exec)} exec_keys, "
                f"{len(expired_step)} step_size, {len(expired_min)} min_notional, "
                f"{idle_locks} idle locks"
            )
        return total

    def get_observable_metrics(self) -> Dict[str, Any]:
        """
        Task 19: 获取可观测性指标

        Returns:
            包含所有运行时指标的字典
        """
        return {
            "cl_ord_id_dedup_hits": self._cl_ord_id_dedup_hits,
            "exec_dedup_hits": self._exec_dedup_hits,
            "order_submit_ok": self._order_submit_ok,
            "order_submit_reject": self._order_submit_reject,
            "order_submit_error": self._order_submit_error,
            "reject_reason_counts": dict(self._reject_reason_counts),
            "fill_latency_ms_avg": (
                self._fill_latency_sum_ms / self._fill_latency_count
                if self._fill_latency_count > 0 else None
            ),
            "fill_latency_count": self._fill_latency_count,
        }

    def _cleanup_exec_dedup(self) -> None:
        now = time.time()
        expired_keys = [
            key for key, expiry in self._processed_exec_keys.items()
            if expiry <= now
        ]
        for key in expired_keys:
            del self._processed_exec_keys[key]

    def _determine_order_side(self, signal: Signal) -> tuple[OrderSide, bool]:
        """
        根据信号类型确定订单方向。
        
        Args:
            signal: 交易信号
            
        Returns:
            tuple: (OrderSide, is_emergency)
                - is_emergency=True 表示信号类型不明确，按紧急卖出处理
        """
        signal_value = signal.signal_type.value.upper()
        
        # 明确的买入信号
        if signal_value in ("BUY", "LONG"):
            return (OrderSide.BUY, False)
        
        # 明确的卖出信号
        if signal_value in ("SELL", "SHORT"):
            return (OrderSide.SELL, False)
        
        # 平仓信号
        if signal_value in ("CLOSE_LONG", "CLOSE_SHORT"):
            return (OrderSide.SELL, False)
        
        # 未知信号类型 -> EMERGENCY_EXIT
        logger.critical(
            f"[OMSCallback] 🚨 EMERGENCY_EXIT: 未知信号类型 {signal.signal_type}, "
            f"策略={signal.strategy_name}, 强制按 SELL 处理. "
            f"这通常表示策略发出了无法识别的信号，请检查策略实现."
        )
        return (OrderSide.SELL, True)

    def _make_exec_key(self, cl_ord_id: str, exec_id: str) -> str:
        return f"{cl_ord_id}:{exec_id}"

    def _mark_exec_seen(self, cl_ord_id: str, exec_id: str) -> bool:
        """
        记录成交幂等键。

        Returns:
            True: 首次出现
            False: 重复成交
        """
        if not cl_ord_id or not exec_id:
            return False
        self._cleanup_exec_dedup()
        key = self._make_exec_key(cl_ord_id, exec_id)
        if key in self._processed_exec_keys:
            self._exec_dedup_hits += 1  # Task 17: 计数重复成交
            return False
        self._processed_exec_keys[key] = time.time() + self._exec_dedup_ttl_seconds
        return True

    def _safe_strategy_id_from_cl_ord(self, cl_ord_id: Optional[str]) -> str:
        """从 cl_ord_id 提取 strategy_id（支持映射优先 + 分隔符解析回退）"""
        if not cl_ord_id:
            return ""
        # 优先使用精确映射（避免截断导致的不匹配）
        if hasattr(self, '_cl_ord_id_to_strategy') and cl_ord_id in self._cl_ord_id_to_strategy:
            return self._cl_ord_id_to_strategy[cl_ord_id]
        # 回退：按分隔符解析（兼容新旧格式）
        if "-" in cl_ord_id:
            return cl_ord_id.rsplit("-", 1)[0]
        if "_" in cl_ord_id:
            return cl_ord_id.rsplit("_", 1)[0]
        return cl_ord_id

    def get_dedup_stats(self) -> Dict[str, Any]:
        """
        获取幂等去重统计信息（Task 17 & Task 19）。

        Returns:
            Dict with:
                - cl_ord_id_dedup_hits: cl_ord_id 重复处理次数
                - exec_dedup_hits: exec_id 重复成交次数
                - active_exec_keys: 当前有效的 exec 键数量
                - order_submit_ok: 成功提交订单数 (Task 19)
                - order_submit_reject: 被拒绝订单数 (Task 19)
                - order_submit_error: 错误订单数 (Task 19)
                - reject_reason_counts: 按原因统计的拒单数 (Task 19)
                - fill_latency_ms_avg: 平均成交延迟 (Task 19)
                - fill_latency_count: 成交次数 (Task 19)
        """
        self._cleanup_exec_dedup()
        return {
            # Task 17: Dedup stats
            "cl_ord_id_dedup_hits": self._cl_ord_id_dedup_hits,
            "exec_dedup_hits": self._exec_dedup_hits,
            "active_exec_keys": len(self._processed_exec_keys),
            # Task 19: Order submission stats
            "order_submit_ok": self._order_submit_ok,
            "order_submit_reject": self._order_submit_reject,
            "order_submit_error": self._order_submit_error,
            "reject_reason_counts": dict(self._reject_reason_counts),
            "fill_latency_ms_avg": (
                self._fill_latency_sum_ms / self._fill_latency_count
                if self._fill_latency_count > 0 else None
            ),
            "fill_latency_count": self._fill_latency_count,
        }

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
        # 诊断日志
        logger.warning(
            f"[OMSCallback] execute_signal called: strategy={strategy_id} "
            f"signal_type={signal.signal_type} symbol={signal.symbol} "
            f"qty={signal.quantity} price={signal.price} "
            f"live_trading_enabled={self._live_trading_enabled_fn()}"
        )

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
            # Task 19: Track rejection
            self._order_submit_reject += 1
            reason = "LIVE_TRADING_DISABLED"
            self._reject_reason_counts[reason] = self._reject_reason_counts.get(reason, 0) + 1
            raise TradingDisabledError("Live trading is not enabled")

        # ==================== 信号验证 ====================
        if not signal.symbol:
            # Task 19: Track rejection
            self._order_submit_reject += 1
            reason = "MISSING_SYMBOL"
            self._reject_reason_counts[reason] = self._reject_reason_counts.get(reason, 0) + 1
            raise OMSCallbackError("Signal missing symbol")

        if not signal.signal_type:
            # Task 19: Track rejection
            self._order_submit_reject += 1
            reason = "MISSING_SIGNAL_TYPE"
            self._reject_reason_counts[reason] = self._reject_reason_counts.get(reason, 0) + 1
            raise OMSCallbackError("Signal missing signal_type")

        if signal.quantity is None or signal.quantity <= 0:
            # Task 19: Track rejection
            self._order_submit_reject += 1
            reason = "INVALID_QUANTITY"
            self._reject_reason_counts[reason] = self._reject_reason_counts.get(reason, 0) + 1
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
                # Task 19: Track rejection
                self._order_submit_reject += 1
                reason = "MIN_NOTIONAL"
                self._reject_reason_counts[reason] = self._reject_reason_counts.get(reason, 0) + 1
                raise MinNotionalError(error_msg)

        # ==================== 生成订单ID ====================
        # 币安要求 cl_ord_id 必须符合 ^[a-zA-Z0-9-_]{1,36}$
        # deployment_id 可能很长，截断到 23 字符为 UUID 留空间。
        # 使用 '-' 作为分隔符避免与 deployment_id 中的 '_' 混淆。
        safe_strategy_id = strategy_id[:23]
        cl_ord_id = f"{safe_strategy_id}-{uuid.uuid4().hex[:12]}"
        # 记录精确映射，避免截断后无法逆向解析 deployment_id
        self._cl_ord_id_to_strategy[cl_ord_id] = strategy_id

        # ==================== 幂等性检查 ====================
        # 1. 检查内存缓存
        if cl_ord_id in self._processed_cl_ord_ids:
            self._cl_ord_id_dedup_hits += 1
            logger.warning(f"[OMSCallback] Duplicate cl_ord_id detected (memory): {cl_ord_id}")
            return None

        # 2. 检查存储层是否已存在相同 cl_ord_id 的订单
        try:
            existing_order_result = self._storage.get_order(cl_ord_id)
            if inspect.isawaitable(existing_order_result):
                existing_order = await existing_order_result
            else:
                existing_order = existing_order_result
            existing_order_found = False
            if isinstance(existing_order, dict):
                existing_order_found = str(existing_order.get("cl_ord_id", "")) == cl_ord_id

            if existing_order_found:
                self._cl_ord_id_dedup_hits += 1
                logger.warning(f"[OMSCallback] Duplicate cl_ord_id detected (storage): {cl_ord_id}")
                self._processed_cl_ord_ids[cl_ord_id] = time.time()
                return None
        except Exception as e:
            # 存储查询失败不影响主流程，但记录警告
            logger.warning(f"[OMSCallback] Failed to check existing order: {e}")

        # 3. 标记为处理中
        self._processed_cl_ord_ids[cl_ord_id] = time.time()

        # ==================== 下单 ====================
        try:
            # 转换方向
            side, is_emergency = self._determine_order_side(signal)
            
            # 转换订单类型（默认市价单）
            order_type = OrderType.MARKET
            if signal.price and signal.price > 0:
                order_type = OrderType.LIMIT

            # 下单前余额预检查
            if signal.price and signal.price > 0:
                try:
                    # 刷新账户信息确保余额最新
                    await self._broker._fetch_account()
                    account = self._broker._account_cache
                    if account:
                        # 解析交易对的 base 和 quote asset
                        # 例如: BTCUSDT -> base=BTC, quote=USDT
                        #      ETHUSDT -> base=ETH, quote=USDT
                        #      BTCUSDC -> base=BTC, quote=USDC
                        symbol = signal.symbol.upper()
                        # 常见的 quote assets
                        quote_assets = ("USDT", "BUSD", "USDC", "FDUSD", "USD", "BTC", "ETH")
                        base_asset = symbol
                        quote_asset = None
                        for qa in quote_assets:
                            if symbol.endswith(qa):
                                base_asset = symbol[:-len(qa)]
                                quote_asset = qa
                                break
                        
                        if side == OrderSide.BUY:
                            # BUY: 检查 quote asset (USDT等) 余额
                            quote_balance = Decimal("0")
                            for bal in account.get("balances", []):
                                if bal.get("asset") == quote_asset:
                                    quote_balance = Decimal(str(bal.get("free", "0")))
                                    break
                            notional = quantity * signal.price
                            if quote_balance < notional:
                                error_msg = f"Insufficient {quote_asset} balance for BUY: required={notional}, available={quote_balance}"
                                logger.warning(f"[OMSCallback] {error_msg}")
                                self._publish_event(strategy_id, "strategy.order.rejected", {
                                    "symbol": signal.symbol,
                                    "side": side.value,
                                    "quantity": str(quantity),
                                    "price": str(signal.price),
                                    "reason": f"INSUFFICIENT_BALANCE: {error_msg}",
                                })
                                # Task 19: Track rejection
                                self._order_submit_reject += 1
                                reason = "INSUFFICIENT_BALANCE"
                                self._reject_reason_counts[reason] = self._reject_reason_counts.get(reason, 0) + 1
                                raise InsufficientBalanceError(error_msg)
                        elif side == OrderSide.SELL:
                            # SELL: 检查 base asset (BTC等) 余额
                            base_balance = Decimal("0")
                            for bal in account.get("balances", []):
                                if bal.get("asset") == base_asset:
                                    base_balance = Decimal(str(bal.get("free", "0")))
                                    break
                            logger.info(
                                f"[OMSCallback] SELL balance check: base_asset={base_asset}, "
                                f"required={quantity}, available={base_balance}"
                            )
                            if base_balance < quantity:
                                if is_emergency:
                                    # EMERGENCY_EXIT: 强制卖出，按实际余额调整
                                    original_qty = quantity
                                    quantity = base_balance
                                    logger.critical(
                                        f"[OMSCallback] 🚨 EMERGENCY EXIT: 余额不足强制调整! "
                                        f"原订单={original_qty} {base_asset}, 实际卖出={quantity} {base_asset}, "
                                        f"symbol={signal.symbol}, strategy={strategy_id}"
                                    )
                                else:
                                    error_msg = f"Insufficient {base_asset} balance for SELL: required={quantity}, available={base_balance}"
                                    logger.warning(f"[OMSCallback] {error_msg}")
                                    self._publish_event(strategy_id, "strategy.order.rejected", {
                                        "symbol": signal.symbol,
                                        "side": side.value,
                                        "quantity": str(quantity),
                                        "price": str(signal.price),
                                        "reason": f"INSUFFICIENT_BALANCE: {error_msg}",
                                    })
                                    # Task 19: Track rejection
                                    self._order_submit_reject += 1
                                    reason = "INSUFFICIENT_BALANCE"
                                    self._reject_reason_counts[reason] = self._reject_reason_counts.get(reason, 0) + 1
                                    raise InsufficientBalanceError(error_msg)
                except MinNotionalError:
                    raise  # Re-raise to be caught by outer handler
                except InvalidQuantityError:
                    raise  # Re-raise to be caught by outer handler
                except InsufficientBalanceError:
                    raise  # Re-raise to be caught by outer handler
                except Exception as e:
                    logger.warning(f"[OMSCallback] Balance pre-check failed: {e}")

            # 下单
            order_desc = f"[OMSCallback] Placing order: strategy={strategy_id}, symbol={signal.symbol}, "
            if is_emergency:
                order_desc += f"🚨 EMERGENCY_EXIT, "
            order_desc += f"side={side.value}, type={order_type.value}, qty={quantity}, "
            order_desc += f"price={signal.price if order_type == OrderType.LIMIT else 'MARKET'}"
            logger.info(order_desc)
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
                "account_id": "binance_demo",
                "instrument": signal.symbol,
                "symbol": signal.symbol,
                "side": side.value,
                "order_type": order_type.value,
                "qty": str(quantity),
                "quantity": str(quantity),
                "tif": "GTC",
                "filled_qty": str(broker_order.filled_quantity),
                "avg_price": str(broker_order.average_price),
                "status": broker_order.status.value,
                "strategy_id": strategy_id,
                "venue": self._broker.broker_name,
                "created_at": (
                    broker_order.created_at.isoformat()
                    if broker_order.created_at else datetime.now(timezone.utc).isoformat()
                ),
            }
            self._storage.create_order(order_data)

            # ==================== 如果有成交，保存成交记录 ====================
            if broker_order.filled_quantity > 0:
                exec_id = f"{broker_order.broker_order_id}:init"
                # Task 19: 幂等检查：避免重复计数
                if not self._mark_exec_seen(cl_ord_id, exec_id):
                    logger.info(
                        f"[OMSCallback] Duplicate fill (sync path), skipping count: cl_ord_id={cl_ord_id}, exec_id={exec_id}"
                    )
                else:
                    execution_data = {
                        "cl_ord_id": cl_ord_id,
                        "exec_id": exec_id,
                        "ts_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                        "fill_qty": str(broker_order.filled_quantity),
                        "fill_price": str(broker_order.average_price),
                        "fee": None,
                        "fee_currency": None,
                        # 兼容旧字段
                        "symbol": signal.symbol,
                        "side": side.value,
                        "quantity": str(broker_order.filled_quantity),
                        "price": str(broker_order.average_price),
                        "strategy_id": strategy_id,
                        "venue": self._broker.broker_name,
                    }
                    self._storage.create_execution(execution_data)
                    # Task 19: Track fill count (only first occurrence)
                    self._fill_latency_count += 1

                    # ==================== Lot 级持仓追踪（策略隔离） ====================
                    if self._position_lot_manager is not None:
                        try:
                            fee_qty = Decimal(str(execution_data.get("fee_qty") or 0))
                            lot_events = self._lot_mgr.on_fill(
                                strategy_id,
                                signal.symbol,
                                side.value,
                                Decimal(str(broker_order.filled_quantity)),
                                broker_order.average_price,
                                fee_qty=fee_qty,
                            )
                            for evt in lot_events:
                                logger.info(
                                    f"[OMSCallback] Lot event: {evt.event_type.value} "
                                    f"strategy={strategy_id} symbol={signal.symbol}"
                                )
                            # 从 ledger 读取最新 realized_pnl 供后续使用
                            ledger = self._lot_mgr.get(strategy_id, signal.symbol)
                            ledger_realized_pnl = (
                                str(ledger.realized_pnl) if ledger else "0"
                            )
                        except Exception as e:
                            logger.warning(f"[OMSCallback] Lot tracking failed: {e}")
                            ledger_realized_pnl = "0"
                    else:
                        ledger_realized_pnl = "0"

                # ==================== 更新持仓 ====================
                try:
                    pos_lock = self._get_position_lock(strategy_id, signal.symbol)
                    async with pos_lock:
                        current_positions = self._storage.list_positions(
                            account_id=None,
                            venue=self._broker.broker_name,
                            strategy_id=strategy_id,
                            instrument=signal.symbol,
                        )
                        current_qty = Decimal("0")
                        current_avg_cost = Decimal("0")
                        for pos in current_positions:
                            current_qty = Decimal(str(pos.get("qty", "0")))
                            current_avg_cost = Decimal(str(pos.get("avg_cost", "0")))
                            break

                        new_qty = current_qty
                        new_avg_cost = broker_order.average_price

                        if side.value == "BUY":
                            if current_qty > 0:
                                total_cost = current_avg_cost * current_qty + broker_order.average_price * quantity
                                new_qty = current_qty + quantity
                                new_avg_cost = total_cost / new_qty if new_qty > 0 else broker_order.average_price
                            else:
                                new_qty = current_qty + quantity
                        else:
                            if quantity > current_qty:
                                logger.error(
                                    f"[OMSCallback] SELL qty {quantity} > position qty {current_qty} "
                                    f"for {signal.symbol}, strategy={strategy_id}"
                                )
                            new_qty = max(current_qty - quantity, Decimal("0"))
                            if new_qty <= 0:
                                new_avg_cost = Decimal("0")
                                new_qty = Decimal("0")

                        unrealized_pnl = Decimal("0")
                        if new_qty > 0 and new_avg_cost > 0:
                            unrealized_pnl = new_qty * (broker_order.average_price - new_avg_cost)

                        self._storage.upsert_position({
                            "account_id": "SYSTEM",
                            "venue": self._broker.broker_name,
                            "instrument": signal.symbol,
                            "strategy_id": strategy_id,
                            "qty": str(new_qty),
                            "avg_cost": str(new_avg_cost),
                            "mark_price": str(broker_order.average_price),
                            "realized_pnl": ledger_realized_pnl,
                            "unrealized_pnl": str(unrealized_pnl),
                        })
                        logger.info(
                            f"[OMSCallback] Position updated: {signal.symbol} strategy={strategy_id} "
                            f"qty={new_qty}, avg_cost={new_avg_cost}, unrealized_pnl={unrealized_pnl}"
                        )
                except Exception as e:
                    logger.warning(f"[OMSCallback] Failed to update position: {e}")

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

            # Task 19: Track successful submission
            self._order_submit_ok += 1

            # Task 9.11: Broadcast SSE update for real-time frontend updates
            # Non-blocking: fire-and-forget to avoid slowing down order processing
            try:
                from trader.api.routes.sse import broadcast_monitor_update, broadcast_order_update
                # Use create_task to schedule broadcast without awaiting
                asyncio.create_task(broadcast_order_update(cl_ord_id, {
                    "type": "order_update",
                    "strategy_id": strategy_id,
                    "symbol": signal.symbol,
                    "side": side.value,
                    "quantity": str(quantity),
                    "filled_qty": str(broker_order.filled_quantity),
                    "status": broker_order.status.value,
                }))
                asyncio.create_task(broadcast_monitor_update({
                    "type": "order_update",
                    "order_id": cl_ord_id,
                }))
            except Exception:
                pass  # SSE broadcast is non-critical, don't fail order processing

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

            # Task 19: Track order error
            self._order_submit_error += 1

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
        获取交易对的 stepSize（带 TTL 缓存）

        Args:
            symbol: 交易对符号

        Returns:
            stepSize 或 Decimal("0")
        """
        cached = self._step_size_cache.get(symbol)
        if cached is not None:
            value, cached_at = cached
            if time.time() - cached_at < self._symbol_cache_ttl_seconds:
                return value

        try:
            step_size = await self._broker.get_symbol_step_size(symbol)
            self._step_size_cache[symbol] = (step_size, time.time())
            return step_size
        except Exception as e:
            logger.warning(f"[OMSCallback] Failed to get stepSize for {symbol}: {e}")
            return Decimal("0")

    async def _get_min_notional(self, symbol: str) -> Decimal:
        """
        获取交易对的最小名义金额（带 TTL 缓存）

        从交易所 exchangeInfo 的 NOTIONAL 或 MIN_NOTIONAL 过滤器中读取。
        如果获取失败，回退到默认值 Decimal("10")。

        Args:
            symbol: 交易对符号

        Returns:
            最小名义金额
        """
        cached = self._min_notional_cache.get(symbol)
        if cached is not None:
            value, cached_at = cached
            if time.time() - cached_at < self._symbol_cache_ttl_seconds:
                return value

        try:
            data = await self._broker.get_exchange_info(symbol=symbol)
            symbols = data.get("symbols", [])
            if not symbols:
                logger.warning(f"[OMSCallback] Symbol {symbol} not found in exchangeInfo, using default minNotional")
                self._min_notional_cache[symbol] = (Decimal("10"), time.time())
                return Decimal("10")

            filters = symbols[0].get("filters", [])
            for f in filters:
                filter_type = f.get("filterType", "")
                if filter_type in ("NOTIONAL", "MIN_NOTIONAL"):
                    min_notional_str = f.get("minNotional") or f.get("notionalMin")
                    if min_notional_str:
                        min_notional = Decimal(str(min_notional_str))
                        self._min_notional_cache[symbol] = (min_notional, time.time())
                        return min_notional

            logger.warning(f"[OMSCallback] No NOTIONAL/MIN_NOTIONAL filter for {symbol}, using default")
            self._min_notional_cache[symbol] = (Decimal("10"), time.time())
            return Decimal("10")
        except Exception as e:
            logger.warning(f"[OMSCallback] Failed to get minNotional for {symbol}: {e}, using default")
            self._min_notional_cache[symbol] = (Decimal("10"), time.time())
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
    position_lot_manager: Any = None,
) -> tuple[Callable, Callable, "OMSCallbackHandler"]:
    """
    创建OMS回调函数和成交处理器

    Args:
        broker: Binance broker实例
        live_trading_enabled: 是否允许真实下单（支持 bool 或 Callable[[], bool]）
        event_callback: 事件发布回调
        fill_callback: 成交回调函数 (strategy_id, order_id, symbol, side, quantity, price)
        position_lot_manager: None = 使用全局 PositionLedgerManager 单例

    Returns:
        tuple: (oms_callback 函数, fill_handler 函数, handler 实例)
        - oms_callback: 直接传给 StrategyRunner
        - fill_handler: 注册到 PrivateStreamManager 或 BinanceConnector
        - handler: OMSCallbackHandler 实例（用于获取 metrics）
    """
    handler = OMSCallbackHandler(
        broker=broker,
        live_trading_enabled=live_trading_enabled,
        event_callback=event_callback,
        fill_callback=fill_callback,
        position_lot_manager=position_lot_manager,
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
                quantity = float(update.qty)
                price = float(update.price)
                symbol = getattr(update, "symbol", None) or ""
                exec_id = str(
                    getattr(update, "exec_id", None)
                    or getattr(update, "trade_id", "")
                ).strip()
                fee = str(getattr(update, "commission", "")) or None

                # 从 cl_ord_id 提取 strategy_id（格式: strategy_id_uuid）
                strategy_id = handler._safe_strategy_id_from_cl_ord(cl_ord_id)

                if not cl_ord_id or not exec_id:
                    logger.warning(
                        "[OMSCallback] Skip fill without idempotency key: cl_ord_id=%s, exec_id=%s",
                        cl_ord_id,
                        exec_id,
                    )
                    return

                is_new_fill = handler._mark_exec_seen(cl_ord_id, exec_id)
                if not is_new_fill:
                    logger.info(
                        "[OMSCallback] Duplicate fill ignored: cl_ord_id=%s, exec_id=%s",
                        cl_ord_id,
                        exec_id,
                    )
                    return

                # 写执行记录（按 cl_ord_id + exec_id 幂等）
                execution_data = {
                    "cl_ord_id": cl_ord_id,
                    "exec_id": exec_id,
                    "ts_ms": int(time.time() * 1000),
                    "fill_qty": str(quantity),
                    "fill_price": str(price),
                    "fee": fee,
                    "fee_currency": None,
                    # 兼容旧字段
                    "symbol": symbol,
                    "side": side,
                    "quantity": str(quantity),
                    "price": str(price),
                    "strategy_id": strategy_id,
                    "venue": handler._broker.broker_name,
                }
                handler._storage.create_execution(execution_data)

                # 尽力更新订单视图（不阻断主流程）
                existing_order = handler._storage.get_order(cl_ord_id)
                if existing_order is not None:
                    prev_filled = Decimal(str(existing_order.get("filled_qty", "0")))
                    incoming_qty = Decimal(str(quantity))
                    total_filled = prev_filled + incoming_qty
                    existing_order["filled_qty"] = str(total_filled)

                    prev_avg = existing_order.get("avg_price")
                    if prev_avg is None or prev_filled <= 0:
                        existing_order["avg_price"] = str(price)
                    else:
                        prev_avg_dec = Decimal(str(prev_avg))
                        incoming_price = Decimal(str(price))
                        weighted_avg = (
                            (prev_avg_dec * prev_filled) + (incoming_price * incoming_qty)
                        ) / total_filled
                        existing_order["avg_price"] = str(weighted_avg)

                    total_qty_raw = existing_order.get("qty") or existing_order.get("quantity") or "0"
                    total_qty = Decimal(str(total_qty_raw))
                    if total_qty > 0 and total_filled >= total_qty:
                        existing_order["status"] = OrderStatus.FILLED.value
                    elif total_filled > 0:
                        existing_order["status"] = OrderStatus.PARTIALLY_FILLED.value
                    existing_order["updated_ts_ms"] = int(time.time() * 1000)

                    if not symbol:
                        symbol = str(existing_order.get("instrument") or existing_order.get("symbol") or "")

                # Note: fill_latency_count is NOT incremented here.
                # The sync path (execute_signal) already incremented it
                # when broker_place_order returned with filled_quantity > 0.
                # WS fill updates are the same fill arriving via different path,
                # counting them would double-count.

                # Lot 级持仓追踪（WS 路径）
                if handler._lot_mgr and strategy_id:
                    try:
                        lot_events = handler._lot_mgr.on_fill(
                            strategy_id,
                            symbol,
                            side,
                            Decimal(str(quantity)),
                            Decimal(str(price)),
                        )
                        for evt in lot_events:
                            logger.info(
                                f"[OMSCallback] WS Lot event: {evt.event_type.value} "
                                f"strategy={strategy_id} symbol={symbol}"
                            )
                    except Exception as e:
                        logger.warning(f"[OMSCallback] WS Lot tracking failed: {e}")

                if fill_callback and strategy_id:
                    # 创建异步任务来运行协程，避免阻塞同步调用链
                    asyncio.create_task(fill_callback(strategy_id, cl_ord_id, symbol, side, quantity, price))
                    logger.info(
                        "[OMSCallback] Fill processed: cl_ord_id=%s, exec_id=%s, qty=%s, price=%s",
                        cl_ord_id,
                        exec_id,
                        quantity,
                        price,
                    )
            except Exception as e:
                logger.error(f"[OMSCallback] Fill handler error: {e}", exc_info=True)

        return fill_handler

    return oms_callback, create_fill_handler(), handler
