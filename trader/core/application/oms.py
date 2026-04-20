"""
OMS - 订单管理系统
===================
订单管理系统（Order Management System）是交易系统的核心组件。

核心职责：
1. 管理订单的完整生命周期
2. 保证订单状态的正确性
3. 处理成交回报（部分成交、完全成交）
4. 记录所有状态变更事件（用于审计）
5. 保证幂等性（防止重复下单）

关键设计原则：
1. 所有订单必须通过OMS管理，策略不能直接调用券商API
2. 状态转换必须是原子性的
3. 所有状态变更都记录事件
4. 支持幂等重试

架构约束：
- Core Plane禁止IO（网络/DB/文件IO）
- 重试逻辑由Adapter层处理
"""
import logging
from typing import List, Optional, Dict, Any, Callable
from datetime import datetime
from decimal import Decimal

from trader.core.application.ports import BrokerPort, StoragePort, EventBusPort, BrokerNetworkError, BrokerBusinessError
from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
from trader.core.domain.models.events import (
    DomainEvent, EventType,
    create_order_created_event
)

logger = logging.getLogger(__name__)


class OMSError(Exception):
    """OMS异常基类"""
    pass


class OrderNotFoundError(OMSError):
    """订单未找到"""
    pass


class InvalidStateTransitionError(OMSError):
    """无效状态转换"""
    pass


class OMS:
    """
    订单管理系统

    管理订单的完整生命周期。
    """

    def __init__(
        self,
        broker: BrokerPort,
        storage: StoragePort,
        event_bus: Optional[EventBusPort] = None,
    ):
        self._broker = broker
        self._storage = storage
        self._event_bus = event_bus

        # 内存缓存
        self._orders: Dict[str, Order] = {}  # client_order_id -> Order

        # 回调
        self._order_handlers: Dict[str, List[Callable]] = {
            "on_order_submitted": [],
            "on_order_filled": [],
            "on_order_cancelled": [],
            "on_order_rejected": [],
        }

        # 统计
        self._stats = {
            "orders_created": 0,
            "orders_submitted": 0,
            "orders_filled": 0,
            "orders_cancelled": 0,
            "orders_rejected": 0,
        }

    # ==================== 订单操作 ====================

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        strategy_name: str = "",
        metadata: Optional[Dict] = None,
        client_order_id: Optional[str] = None
    ) -> Order:
        """
        创建订单

        这是订单的入口点。订单创建后处于PENDING状态，
        需要调用submit_order发送到券商。
        """
        # 创建订单对象
        order = Order(
            order_id="",  # 将由系统生成
            client_order_id=client_order_id or "",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            strategy_name=strategy_name,
            metadata=metadata or {},
            status=OrderStatus.PENDING
        )

        # 保存到缓存和存储
        self._orders[order.client_order_id] = order
        await self._storage.save_order(order)

        # 发布事件
        await self._publish_event(create_order_created_event(order))

        self._stats["orders_created"] += 1

        logger.info(f"[OMS] 订单创建: {order.client_order_id}")
        return order

    async def submit_order(self, client_order_id: str) -> Order:
        """
        提交订单到券商

        这是OMS的核心方法。包含：
        1. 幂等检查
        2. 提交到券商
        3. 状态更新
        4. 事件记录
        """
        order = self._get_order(client_order_id)

        if not order:
            raise OrderNotFoundError(f"订单不存在: {client_order_id}")

        if order.status != OrderStatus.PENDING:
            logger.warning(f"[OMS] 订单状态错误: {order.client_order_id}, 状态: {order.status}")
            return order

        try:
            broker_order = await self._broker.place_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                price=order.price,
                client_order_id=order.client_order_id
            )

            order.submit()
            order.broker_order_id = broker_order.broker_order_id

            if broker_order.status == OrderStatus.FILLED:
                order.fill(broker_order.filled_quantity, broker_order.average_price)
                self._stats["orders_filled"] += 1

            await self._storage.save_order(order)
            await self._publish_order_event(order, EventType.ORDER_SUBMITTED)

            self._stats["orders_submitted"] += 1
            logger.info(f"[OMS] 订单提交成功: {client_order_id}")

            await self._trigger_handler("on_order_submitted", order)

            return order

        except BrokerNetworkError as e:
            logger.error(f"[OMS] 订单提交失败 网络错误: {e}")
            order.reject(f"网络错误: {e}")
            await self._storage.save_order(order)
            await self._publish_order_event(order, EventType.ORDER_REJECTED)
            self._stats["orders_rejected"] += 1
            await self._trigger_handler("on_order_rejected", order)
            return order

        except BrokerBusinessError as e:
            logger.warning(f"[OMS] 订单提交失败 业务错误: {e}")
            order.reject(str(e))
            await self._storage.save_order(order)
            await self._publish_order_event(order, EventType.ORDER_REJECTED)
            self._stats["orders_rejected"] += 1
            await self._trigger_handler("on_order_rejected", order)
            return order

        except Exception as e:
            logger.error(f"[OMS] 订单提交失败 未知错误: {e}")
            order.reject(f"未知错误: {e}")
            await self._storage.save_order(order)
            await self._publish_order_event(order, EventType.ORDER_REJECTED)
            self._stats["orders_rejected"] += 1
            await self._trigger_handler("on_order_rejected", order)
            return order

    async def cancel_order(self, client_order_id: str) -> bool:
        """
        撤销订单
        """
        order = self._get_order(client_order_id)

        if not order:
            logger.warning(f"[OMS] 撤销订单失败，订单不存在: {client_order_id}")
            return False

        # 检查是否可以撤销
        if not order.can_cancel():
            logger.warning(f"[OMS] 订单不可撤销: {client_order_id}, 状态: {order.status}")
            return False

        try:
            success = await self._broker.cancel_order(
                client_order_id=client_order_id,
                broker_order_id=order.broker_order_id
            )

            if success:
                order.cancel()
                await self._storage.save_order(order)
                await self._publish_order_event(order, EventType.ORDER_CANCELLED)

                self._stats["orders_cancelled"] += 1
                logger.info(f"[OMS] 订单撤销成功: {client_order_id}")

                # 触发回调
                await self._trigger_handler("on_order_cancelled", order)

            return success

        except Exception as e:
            logger.error(f"[OMS] 撤销订单失败: {client_order_id}, 错误: {e}")
            return False

    async def sync_order(self, client_order_id: str) -> Order:
        """
        同步订单状态

        从券商获取最新订单状态，更新本地订单。
        用于对账和恢复。
        """
        order = self._get_order(client_order_id)

        if not order:
            raise OrderNotFoundError(f"订单不存在: {client_order_id}")

        # 查询券商
        broker_order = await self._broker.get_order(
            client_order_id=client_order_id,
            broker_order_id=order.broker_order_id
        )

        if not broker_order:
            return order

        # 更新本地订单状态
        old_status = order.status

        if broker_order.status != order.status:
            if broker_order.status == OrderStatus.FILLED:
                order.fill(broker_order.filled_quantity, broker_order.average_price)
                self._stats["orders_filled"] += 1
                await self._trigger_handler("on_order_filled", order)
            elif broker_order.status == OrderStatus.CANCELLED:
                order.cancel()
                self._stats["orders_cancelled"] += 1

            await self._storage.save_order(order)

            # 如果状态变化，发布事件
            if old_status != order.status:
                event_type = EventType.ORDER_FILLED if broker_order.status == OrderStatus.FILLED else None
                if event_type:
                    await self._publish_order_event(order, event_type)

        return order

    # ==================== 查询操作 ====================

    def _get_order(self, client_order_id: str) -> Optional[Order]:
        """获取订单（从缓存）"""
        return self._orders.get(client_order_id)

    def get_order(self, client_order_id: str) -> Optional[Order]:
        """获取订单"""
        return self._get_order(client_order_id)

    async def get_order_from_storage(self, client_order_id: str) -> Optional[Order]:
        """从存储获取订单"""
        return await self._storage.get_order(client_order_id)

    def get_pending_orders(self) -> List[Order]:
        """获取待处理订单"""
        return [o for o in self._orders.values() if not o.is_terminal()]

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """获取未结订单"""
        orders = [o for o in self._orders.values() if o.status in [OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED]]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    # ==================== 事件处理 ====================

    async def handle_broker_callback(self, broker_order_data: Dict) -> None:
        """
        处理券商回调

        这是处理成交回报的入口。
        需要处理：部分成交、完全成交、撤销等
        """
        client_order_id = broker_order_data.get("client_order_id")
        if not client_order_id:
            return

        order = self._get_order(client_order_id)
        if not order:
            logger.warning(f"[OMS] 收到未知订单回调: {client_order_id}")
            return

        status = broker_order_data.get("status")
        filled_qty = Decimal(str(broker_order_data.get("filled_quantity", 0)))
        avg_price = Decimal(str(broker_order_data.get("average_price", 0)))

        # 处理状态更新
        # Task 17: 终端状态单调性检查 - 不允许从终态回退
        if order.is_terminal():
            logger.warning(
                f"[OMS] 订单已终态，忽略回调: client_order_id={client_order_id}, "
                f"current_status={order.status}, callback_status={status}"
            )
            return

        if status == "FILLED" and order.status != OrderStatus.FILLED:
            order.fill(filled_qty, avg_price)
            await self._storage.save_order(order)
            await self._publish_order_event(order, EventType.ORDER_FILLED)

            self._stats["orders_filled"] += 1
            await self._trigger_handler("on_order_filled", order)

            logger.info(f"[OMS] 订单成交: {client_order_id}, 数量: {filled_qty}, 价格: {avg_price}")

        elif status == "PARTIALLY_FILLED" and order.status == OrderStatus.SUBMITTED:
            order.fill(filled_qty, avg_price)
            await self._storage.save_order(order)
            await self._publish_order_event(order, EventType.ORDER_PARTIALLY_FILLED)

            logger.info(f"[OMS] 订单部分成交: {client_order_id}, 数量: {filled_qty}")

        elif status == "CANCELLED":
            order.cancel()
            await self._storage.save_order(order)
            await self._publish_order_event(order, EventType.ORDER_CANCELLED)

            self._stats["orders_cancelled"] += 1

    def register_handler(self, event: str, handler: Callable) -> None:
        """注册订单事件处理器"""
        if event in self._order_handlers:
            self._order_handlers[event].append(handler)

    async def _trigger_handler(self, event: str, order: Order) -> None:
        """触发事件处理器"""
        for handler in self._order_handlers.get(event, []):
            try:
                await handler(order)
            except Exception as e:
                logger.error(f"[OMS] 事件处理器执行失败: {event}, 错误: {e}")

    async def _publish_order_event(self, order: Order, event_type: EventType) -> None:
        """发布订单事件"""
        event = DomainEvent(
            event_type=event_type,
            aggregate_id=order.order_id,
            aggregate_type="Order",
            data={
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": order.quantity,
                "filled_quantity": order.filled_quantity,
                "average_price": order.average_price,
                "status": order.status.value,
            }
        )

        # 保存到存储
        await self._storage.save_event(event)

        # 发布到事件总线
        if self._event_bus:
            await self._event_bus.publish(event)

    async def _publish_event(self, event: DomainEvent) -> None:
        """发布通用事件"""
        await self._storage.save_event(event)
        if self._event_bus:
            await self._event_bus.publish(event)

    # ==================== 统计 ====================

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return self._stats.copy()

    # ==================== 恢复 ====================

    async def recover(self) -> None:
        """
        恢复订单状态

        系统启动时调用，从存储加载未完成订单，
        并尝试与券商同步状态。
        """
        logger.info("[OMS] 开始恢复订单状态...")

        # 从存储加载所有未完成订单
        pending_orders = await self._storage.get_orders(status=OrderStatus.PENDING)
        open_orders = await self._storage.get_orders(status=OrderStatus.SUBMITTED)
        partial_orders = await self._storage.get_orders(status=OrderStatus.PARTIALLY_FILLED)

        all_orders = pending_orders + open_orders + partial_orders

        for order in all_orders:
            self._orders[order.client_order_id] = order

        # 尝试与券商同步
        for order in open_orders + partial_orders:
            try:
                await self.sync_order(order.client_order_id)
            except Exception as e:
                logger.error(f"[OMS] 同步订单失败: {order.client_order_id}, 错误: {e}")

        logger.info(f"[OMS] 订单恢复完成，共 {len(all_orders)} 个未完成订单")
