"""
Ports - 端口接口定义
====================
端口是核心业务逻辑与外部世界的边界。

核心原则：
1. Port是抽象接口，定义了"做什么"而非"怎么做"
2. 外部系统（券商、数据库、行情源）通过Adapter实现Port
3. 核心业务只依赖Port，不依赖具体实现

关键端口：
- BrokerPort: 券商交易接口（下单、撤单、查询）
- MarketDataPort: 行情数据接口（K线、实时行情）
- StoragePort: 存储接口（事件、订单持久化）
- ClockPort: 时间接口（用于测试）
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from datetime import datetime
from decimal import Decimal
from dataclasses import dataclass

from trader.core.domain.models.order import Order, OrderSide, OrderType
from trader.core.domain.models.position import Position, BrokerPosition
from trader.core.domain.models.orderbook import OrderBook


# ==================== Broker Exceptions ====================

class BrokerError(Exception):
    """Broker 异常基类"""
    pass


class BrokerNetworkError(BrokerError):
    """网络错误，可重试"""
    pass


class BrokerBusinessError(BrokerError):
    """业务错误，不应重试"""
    pass


# ==================== Broker Port ====================

@dataclass
class BrokerOrder:
    """券商订单响应"""
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    filled_quantity: Decimal
    average_price: Decimal
    status: Any  # OrderStatus
    created_at: datetime


@dataclass
class BrokerAccount:
    """券商账户信息"""
    total_equity: Decimal
    available_cash: Decimal
    currency: str = "USDT"


class BrokerPort(ABC):
    """
    券商交易端口

    定义交易系统与券商的交互接口。
    所有券商（Binance、XTP、QMT、CTP）都通过Adapter实现此接口。
    """

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """券商名称"""
        pass

    @property
    @abstractmethod
    def supported_features(self) -> List[str]:
        """支持的特性列表"""
        pass

    @abstractmethod
    async def connect(self) -> None:
        """建立连接"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass

    @abstractmethod
    async def is_connected(self) -> bool:
        """检查连接状态"""
        pass

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        client_order_id: Optional[str] = None
    ) -> BrokerOrder:
        """
        下单

        Args:
            symbol: 交易标的
            side: 买卖方向
            order_type: 订单类型
            quantity: 委托数量
            price: 委托价格（限价单）
            client_order_id: 客户端订单ID（用于幂等）

        Returns:
            BrokerOrder: 券商订单响应
        """
        pass

    @abstractmethod
    async def cancel_order(
        self,
        client_order_id: str,
        broker_order_id: Optional[str] = None
    ) -> bool:
        """撤单"""
        pass

    @abstractmethod
    async def get_order(
        self,
        client_order_id: str,
        broker_order_id: Optional[str] = None
    ) -> Optional[BrokerOrder]:
        """查询订单状态"""
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """查询未结订单"""
        pass

    @abstractmethod
    async def get_positions(self) -> List[BrokerPosition]:
        """查询持仓"""
        pass

    @abstractmethod
    async def get_account(self) -> BrokerAccount:
        """查询账户信息"""
        pass


# ==================== Market Data Port ====================

@dataclass
class MarketKline:
    """K线数据"""
    symbol: str
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool = True


@dataclass
class MarketTicker:
    """实时行情"""
    symbol: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    high_24h: Decimal
    low_24h: Decimal
    volume_24h: Decimal
    timestamp: datetime


class MarketDataPort(ABC):
    """
    行情数据端口

    定义交易系统获取市场数据的接口。
    支持REST拉取和WebSocket推送两种模式。
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称"""
        pass

    @abstractmethod
    async def connect(self) -> None:
        """建立连接"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass

    @abstractmethod
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500
    ) -> List[MarketKline]:
        """
        获取K线数据

        Args:
            symbol: 交易标的
            interval: K线周期 (1m, 5m, 1h, 1d等)
            limit: 返回数量

        Returns:
            List[MarketKline]: K线列表
        """
        pass

    @abstractmethod
    async def get_ticker(self, symbol: str) -> MarketTicker:
        """获取实时行情"""
        pass

    @abstractmethod
    async def get_orderbook(self, symbol: str) -> Optional[OrderBook]:
        """
        获取订单簿快照

        Args:
            symbol: 交易标的

        Returns:
            Optional[OrderBook]: 订单簿快照
        """
        pass


# ==================== Storage Port ====================

class StoragePort(ABC):
    """
    存储端口

    定义交易系统数据持久化的接口。
    可以实现为内存存储、SQLite、PostgreSQL等。
    """

    @abstractmethod
    async def connect(self) -> None:
        """建立连接"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass

    # ==================== 事件存储 ====================

    @abstractmethod
    async def save_event(self, event) -> str:
        """保存事件"""
        pass

    @abstractmethod
    async def get_events(
        self,
        aggregate_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 1000
    ) -> List:
        """查询事件"""
        pass

    # ==================== 订单存储 ====================

    @abstractmethod
    async def save_order(self, order: Order) -> None:
        """保存订单"""
        pass

    @abstractmethod
    async def get_order(self, order_id: str) -> Optional[Order]:
        """获取订单"""
        pass

    @abstractmethod
    async def get_orders(
        self,
        symbol: Optional[str] = None,
        status: Optional[Any] = None,
        limit: int = 100
    ) -> List[Order]:
        """查询订单"""
        pass

    # ==================== 持仓存储 ====================

    @abstractmethod
    async def save_position(self, position: Position) -> None:
        """保存持仓"""
        pass

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Position]:
        """获取持仓"""
        pass

    @abstractmethod
    async def get_all_positions(self) -> List[Position]:
        """获取所有持仓"""
        pass


# ==================== Clock Port ====================

class ClockPort(ABC):
    """
    时间端口

    用于获取当前时间。
    测试时可以用模拟时钟控制时间。
    """

    @abstractmethod
    def now(self) -> datetime:
        """获取当前时间"""
        pass

    @abstractmethod
    def utcnow(self) -> datetime:
        """获取当前UTC时间"""
        pass


# ==================== Event Bus Port ====================

class EventBusPort(ABC):
    """
    事件总线端口

    用于发布和订阅领域事件。
    """

    @abstractmethod
    async def publish(self, event) -> None:
        """发布事件"""
        pass

    @abstractmethod
    def subscribe(self, event_type: str, handler) -> None:
        """订阅事件"""
        pass
