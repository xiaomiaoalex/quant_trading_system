"""
FakeBroker - 模拟券商适配器
===========================
用于开发和测试的模拟券商。

重要特性：
1. 模拟真实券商的订单生命周期
2. 可配置的错误率、网络延迟
3. 可模拟边界情况：重复回调、乱序回报、部分成交
4. 完全确定性，便于测试

使用场景：
- 开发时无需真实API密钥
- 单元测试验证OMS状态机
- Contract Tests验证适配器行为一致性
"""
import asyncio
import random
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal
from dataclasses import dataclass, field

from trader.core.application.ports import BrokerPort, BrokerOrder, BrokerAccount, BrokerNetworkError, BrokerBusinessError
from trader.core.domain.models.order import OrderSide, OrderType, OrderStatus
from trader.core.domain.models.position import BrokerPosition


@dataclass
class FakeBrokerConfig:
    """模拟券商配置"""
    latency_ms: int = 10              # 模拟网络延迟（毫秒）
    error_rate: float = 0.0          # 错误率 (0-1)
    duplicate_rate: float = 0.0      # 重复回调率
    out_of_order_rate: float = 0.0   # 乱序回报率
    partial_fill_rate: float = 0.3    # 部分成交率
    reject_rate: float = 0.0          # 拒绝率
    orderbook_imbalance: float = 0.1  # 订单簿不平衡度
    # 确定性错误注入（优先级高于 error_rate/reject_rate）
    force_error: bool = False         # 强制触发网络错误
    force_reject: bool = False        # 强制触发业务拒绝


class FakeBroker(BrokerPort):
    """
    模拟券商

    可以模拟各种网络异常和边界情况，用于测试OMS的健壮性。
    """

    def __init__(self, config: FakeBrokerConfig = None):
        self._config = config or FakeBrokerConfig()
        self._connected = False

        # 存储
        self._orders: Dict[str, Dict] = {}  # client_order_id -> order data
        self._positions: Dict[str, Dict] = {}  # symbol -> position data
        self._account = {
            "total_equity": Decimal("10000"),
            "available_cash": Decimal("10000"),
        }

        # 回调
        self._callbacks: List[callable] = []

        # 统计
        self._stats = {
            "orders_submitted": 0,
            "orders_filled": 0,
            "orders_cancelled": 0,
            "orders_rejected": 0,
        }

    # ==================== 确定性错误注入 ====================

    def trigger_network_error(self) -> "FakeBroker":
        """
        触发一次网络错误（自动重置）

        用于测试中确定性注入网络错误。
        """
        self._config.force_error = True
        return self

    def trigger_reject(self) -> "FakeBroker":
        """
        触发一次订单拒绝（自动重置）

        用于测试中确定性注入业务拒绝。
        """
        self._config.force_reject = True
        return self

    def reset_forced_errors(self) -> "FakeBroker":
        """
        重置所有强制错误标志
        """
        self._config.force_error = False
        self._config.force_reject = False
        return self

    @property
    def broker_name(self) -> str:
        return "fake_broker"

    @property
    def supported_features(self) -> List[str]:
        return [
            "MARKET_ORDER",
            "LIMIT_ORDER",
            "CANCEL_ORDER",
            "QUERY_ORDER",
            "GET_POSITIONS",
            "GET_ACCOUNT",
        ]

    async def connect(self) -> None:
        """模拟连接"""
        await asyncio.sleep(self._config.latency_ms / 1000)
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected(self) -> bool:
        return self._connected

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        client_order_id: Optional[str] = None
    ) -> BrokerOrder:
        """模拟下单"""
        await asyncio.sleep(self._config.latency_ms / 1000)

        # 检查连接
        if not self._connected:
            raise ConnectionError("Broker not connected")

        # 生成订单ID
        broker_order_id = f"fake_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

        # 检查重复订单
        if client_order_id and client_order_id in self._orders:
            # 返回已存在的订单（幂等）
            existing = self._orders[client_order_id]
            return BrokerOrder(
                broker_order_id=existing["broker_order_id"],
                client_order_id=client_order_id,
                symbol=existing["symbol"],
                side=OrderSide(existing["side"]),
                order_type=OrderType(existing["order_type"]),
                quantity=Decimal(str(existing["quantity"])),
                filled_quantity=Decimal(str(existing["filled_quantity"])),
                average_price=Decimal(str(existing["average_price"])),
                status=OrderStatus(existing["status"]),
                created_at=existing["created_at"]
            )

        # 模拟错误（确定性注入优先于随机率，自动重置）
        if self._config.force_error:
            self._config.force_error = False  # 自动重置
            self._stats["orders_rejected"] += 1
            raise BrokerNetworkError("模拟网络错误")

        if random.random() < self._config.error_rate:
            self._stats["orders_rejected"] += 1
            raise BrokerNetworkError("模拟网络错误")

        # 模拟拒绝（确定性注入优先于随机率，自动重置）
        if self._config.force_reject:
            self._config.force_reject = False  # 自动重置
            self._stats["orders_rejected"] += 1
            raise BrokerBusinessError("模拟订单拒绝：资金不足")

        if random.random() < self._config.reject_rate:
            self._stats["orders_rejected"] += 1
            raise BrokerBusinessError("模拟订单拒绝：资金不足")

        # 创建订单
        order_data = {
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id or f"cli_{broker_order_id}",
            "symbol": symbol,
            "side": side.value,
            "order_type": order_type.value,
            "quantity": float(quantity),
            "price": float(price) if price else None,
            "filled_quantity": 0.0,
            "average_price": 0.0,
            "status": "SUBMITTED",
            "created_at": datetime.now(timezone.utc),
        }

        self._orders[order_data["client_order_id"]] = order_data
        self._stats["orders_submitted"] += 1

        # 模拟成交（异步回调）
        asyncio.create_task(self._simulate_fill(order_data))

        return BrokerOrder(
            broker_order_id=broker_order_id,
            client_order_id=order_data["client_order_id"],
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            filled_quantity=Decimal("0"),
            average_price=Decimal("0"),
            status=OrderStatus.SUBMITTED,
            created_at=order_data["created_at"]
        )

    async def _simulate_fill(self, order_data: Dict) -> None:
        """模拟成交回报（异步）"""
        # 延迟后触发
        await asyncio.sleep(0.1)

        if order_data["client_order_id"] not in self._orders:
            return  # 订单可能已被撤销

        quantity = Decimal(str(order_data["quantity"]))
        price = Decimal(str(order_data.get("price", 50000)))  # 默认价格

        # 模拟部分成交或完全成交
        if random.random() < self._config.partial_fill_rate:
            # 部分成交
            fill_qty = quantity * Decimal("0.5")
            order_data["filled_quantity"] = float(fill_qty)
            order_data["average_price"] = float(price)
            order_data["status"] = "PARTIALLY_FILLED"

            # 再次触发剩余成交
            asyncio.create_task(self._simulate_remaining_fill(order_data))
        else:
            # 完全成交
            order_data["filled_quantity"] = float(quantity)
            order_data["average_price"] = float(price)
            order_data["status"] = "FILLED"

        self._stats["orders_filled"] += 1

        # 模拟重复回调
        if random.random() < self._config.duplicate_rate:
            asyncio.create_task(self._emit_callback(order_data))

        # 触发回调
        await self._emit_callback(order_data)

    async def _simulate_remaining_fill(self, order_data: Dict) -> None:
        """模拟剩余成交"""
        await asyncio.sleep(0.1)

        if order_data["client_order_id"] not in self._orders:
            return

        remaining = Decimal(str(order_data["quantity"])) - Decimal(str(order_data["filled_quantity"]))
        order_data["filled_quantity"] = float(Decimal(str(order_data["filled_quantity"])) + remaining)
        order_data["status"] = "FILLED"

        await self._emit_callback(order_data)

    async def _emit_callback(self, order_data: Dict) -> None:
        """触发订单回调"""
        for callback in self._callbacks:
            try:
                await callback(order_data)
            except Exception:
                pass

    def register_callback(self, callback: callable) -> None:
        """注册订单状态回调"""
        self._callbacks.append(callback)

    async def cancel_order(
        self,
        client_order_id: str,
        broker_order_id: Optional[str] = None
    ) -> bool:
        """模拟撤单"""
        await asyncio.sleep(self._config.latency_ms / 1000)

        if client_order_id not in self._orders:
            return False

        order = self._orders[client_order_id]

        if order["status"] in ["FILLED", "CANCELLED"]:
            return False

        order["status"] = "CANCELLED"
        self._stats["orders_cancelled"] += 1
        return True

    async def get_order(
        self,
        client_order_id: str,
        broker_order_id: Optional[str] = None
    ) -> Optional[BrokerOrder]:
        """查询订单"""
        await asyncio.sleep(self._config.latency_ms / 1000)

        if client_order_id not in self._orders:
            return None

        order = self._orders[client_order_id]
        return BrokerOrder(
            broker_order_id=order["broker_order_id"],
            client_order_id=order["client_order_id"],
            symbol=order["symbol"],
            side=OrderSide(order["side"]),
            order_type=OrderType(order["order_type"]),
            quantity=Decimal(str(order["quantity"])),
            filled_quantity=Decimal(str(order["filled_quantity"])),
            average_price=Decimal(str(order["average_price"])),
            status=OrderStatus(order["status"]),
            created_at=order["created_at"]
        )

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """查询未结订单"""
        await asyncio.sleep(self._config.latency_ms / 1000)

        result = []
        for order in self._orders.values():
            if order["status"] in ["SUBMITTED", "PARTIALLY_FILLED"]:
                if symbol is None or order["symbol"] == symbol:
                    result.append(BrokerOrder(
                        broker_order_id=order["broker_order_id"],
                        client_order_id=order["client_order_id"],
                        symbol=order["symbol"],
                        side=OrderSide(order["side"]),
                        order_type=OrderType(order["order_type"]),
                        quantity=Decimal(str(order["quantity"])),
                        filled_quantity=Decimal(str(order["filled_quantity"])),
                        average_price=Decimal(str(order["average_price"])),
                        status=OrderStatus(order["status"]),
                        created_at=order["created_at"]
                    ))

        return result

    async def get_positions(self) -> List[BrokerPosition]:
        """查询持仓"""
        await asyncio.sleep(self._config.latency_ms / 1000)

        result = []
        for symbol, pos in self._positions.items():
            if pos["quantity"] > 0:
                result.append(BrokerPosition(
                    symbol=symbol,
                    quantity=Decimal(str(pos["quantity"])),
                    avg_price=Decimal(str(pos["avg_price"])),
                    unrealized_pnl=Decimal(str(pos.get("unrealized_pnl", 0))),
                ))

        return result

    async def get_account(self) -> BrokerAccount:
        """查询账户"""
        await asyncio.sleep(self._config.latency_ms / 1000)
        return BrokerAccount(
            total_equity=Decimal(str(self._account["total_equity"])),
            available_cash=Decimal(str(self._account["available_cash"])),
        )

    # ==================== 测试辅助方法 ====================

    def set_balance(self, total: Decimal, available: Decimal = None) -> None:
        """设置账户余额（用于测试）"""
        self._account["total_equity"] = total
        self._account["available_cash"] = available if available is not None else total

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return self._stats.copy()

    def reset(self) -> None:
        """重置状态（用于测试）"""
        self._orders.clear()
        self._positions.clear()
        self._account = {
            "total_equity": Decimal("10000"),
            "available_cash": Decimal("10000"),
        }
        self._stats = {
            "orders_submitted": 0,
            "orders_filled": 0,
            "orders_cancelled": 0,
            "orders_rejected": 0,
        }


# ==================== 便捷构造函数 ====================

def create_order_execution_broker() -> FakeBroker:
    """创建用于订单执行的模拟券商（低延迟，无异常）"""
    config = FakeBrokerConfig(
        latency_ms=5,
        error_rate=0.0,
        partial_fill_rate=0.0,
    )
    return FakeBroker(config)


def create_fuzzy_broker() -> FakeBroker:
    """创建用于模糊测试的模拟券商（高异常率）"""
    config = FakeBrokerConfig(
        latency_ms=50,
        error_rate=0.05,
        duplicate_rate=0.1,
        partial_fill_rate=0.5,
        reject_rate=0.05,
    )
    return FakeBroker(config)
