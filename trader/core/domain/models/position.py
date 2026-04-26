"""
Position - 持仓领域模型
=======================
持仓模型区分"券商真实持仓"与"内部账本持仓",为对账机制提供基础.

三层持仓架构:
- Account Level: 账户总持仓(Broker API),唯一真相源
- Strategy Level: 每个 strategy_id × symbol 独立持仓(OMS 事件累积)
- Lot Level: 每笔买入的精确记录,支持 FIFO / 费用追踪

关键概念:
- Position: 内部账本持仓(从成交事件累积)
- BrokerPosition: 券商真实持仓(来自券商API查询)
- PositionLot: 批次追踪,每笔买入一条记录
- PositionLedger: 策略级账本,管理某策略某标的的所有 Lot
- 两者的差异就是需要对齐的地方
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
import logging
import uuid

logger = logging.getLogger(__name__)


# ==================== 枚举定义 ====================

class PositionStatus(Enum):
    """持仓状态"""
    HISTORICAL = "HISTORICAL"   # 程序启动前已存在(成本来自 broker 或未知)
    ACTIVE = "ACTIVE"           # 正常交易中
    CLOSED = "CLOSED"           # 已平仓


class PositionSource(Enum):
    """持仓来源"""
    STRATEGY = "STRATEGY"           # 策略产生的持仓(有精确成本)
    HISTORICAL = "HISTORICAL"       # 历史持仓(程序启动前已存在)
    RECONCILIATION = "RECONCILIATION"  # 对账调整产生的持仓


class CostBasisMethod(Enum):
    """成本基础计算方法"""
    AVERAGE_COST = "average_cost"          # 加权平均(默认)
    FIFO = "fifo"                            # 先进先出
    SPECIFIC_IDENTIFICATION = "specific_id"  # 指定批次


# ==================== 批次追踪 ====================

@dataclass(slots=True)
class PositionLot:
    """
    批次 — 每笔买入的精确记录.

    支持部分成交、部分平仓、费用追踪.
    为 FIFO / Specific Identification 提供数据基础.
    """
    lot_id: str
    strategy_id: str
    symbol: str
    original_qty: Decimal          # 原始成交数量
    remaining_qty: Decimal        # 剩余可平仓数量
    fill_price: Decimal           # 成交价格
    fee_qty: Decimal = field(default_factory=lambda: Decimal("0"))  # 手续费数量(base asset)
    fee_asset: Optional[str] = None
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    filled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None
    is_closed: bool = False

    def __post_init__(self):
        for attr in ['original_qty', 'remaining_qty', 'fill_price', 'fee_qty', 'realized_pnl']:
            val = getattr(self, attr)
            if isinstance(val, (int, float, str)):
                object.__setattr__(self, attr, Decimal(str(val)))

    def apply_fee(self) -> None:
        if self.fee_qty > 0 and self.remaining_qty >= self.fee_qty:
            self.remaining_qty -= self.fee_qty
        elif self.fee_qty > 0 and self.remaining_qty < self.fee_qty:
            logger.warning(
                f"Fee qty {self.fee_qty} exceeds remaining_qty {self.remaining_qty} "
                f"for lot {self.lot_id}, fee not deducted"
            )


@dataclass(slots=True)
class PositionLedger:
    """
    策略级持仓账本.

    管理一个 strategy_id × symbol 的所有 Lot.
    提供 add_lot / reduce / avg_cost / total_qty 等操作.

    每个 (strategy_id, symbol) 是独立聚合根,天然线程安全.
    """
    position_id: str  # {strategy_id}:{symbol}
    strategy_id: str
    symbol: str
    lots: List[PositionLot] = field(default_factory=list)
    closed_lots: List[PositionLot] = field(default_factory=list)
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    status: PositionStatus = PositionStatus.ACTIVE
    cost_basis_method: CostBasisMethod = CostBasisMethod.AVERAGE_COST
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_qty(self) -> Decimal:
        """当前总持仓数量 = 所有 open lot 的 remaining_qty 之和"""
        return sum((lot.remaining_qty for lot in self.lots), Decimal("0"))

    @property
    def avg_cost(self) -> Decimal:
        """加权平均成本(仅 AVERAGE_COST 方法有效;FIFO 下始终返回 0)"""
        if self.cost_basis_method != CostBasisMethod.AVERAGE_COST:
            return Decimal("0")
        total_cost = sum((lot.remaining_qty * lot.fill_price for lot in self.lots), Decimal("0"))
        total_qty = self.total_qty
        return total_cost / total_qty if total_qty > 0 else Decimal("0")

    def add_lot(
        self,
        quantity: Decimal,
        fill_price: Decimal,
        fee_qty: Decimal = Decimal("0"),
        fee_asset: Optional[str] = None,
        filled_at: Optional[datetime] = None,
    ) -> PositionLot:
        """开仓或加仓:创建新批次"""
        lot = PositionLot(
            lot_id=str(uuid.uuid4()),
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            original_qty=quantity,
            remaining_qty=quantity,
            fill_price=fill_price,
            fee_qty=fee_qty,
            fee_asset=fee_asset,
            filled_at=filled_at or datetime.now(timezone.utc),
        )
        lot.apply_fee()
        self.lots.append(lot)
        self._update_status()
        self.updated_at = datetime.now(timezone.utc)
        return lot

    def reduce(
        self,
        quantity: Decimal,
        price: Decimal,
    ) -> Tuple[Decimal, List[Tuple[str, Decimal, Decimal]]]:
        if quantity <= 0:
            raise ValueError(f"Reduce quantity must be positive, got {quantity}")

        if not self.lots and quantity > 0:
            logger.warning(
                f"Attempted to reduce {quantity} on empty ledger "
                f"{self.strategy_id}:{self.symbol}"
            )

        remaining = quantity
        realized = Decimal("0")
        reduced_lots: List[Tuple[str, Decimal, Decimal]] = []

        for lot in sorted(self.lots, key=lambda l: l.filled_at):
            if remaining <= 0 or lot.remaining_qty <= 0:
                continue
            reduce_qty = min(remaining, lot.remaining_qty)
            pnl = (price - lot.fill_price) * reduce_qty
            realized += pnl
            lot.remaining_qty -= reduce_qty
            lot.realized_pnl += pnl
            remaining -= reduce_qty
            reduced_lots.append((lot.lot_id, reduce_qty, lot.fill_price))
            if lot.remaining_qty <= 0:
                lot.is_closed = True
                lot.closed_at = datetime.now(timezone.utc)
                self.closed_lots.append(lot)

        # 移除已关闭的 lot
        self.lots = [l for l in self.lots if not l.is_closed]
        self.realized_pnl += realized
        self._update_status()
        self.updated_at = datetime.now(timezone.utc)
        return realized, reduced_lots

    def update_unrealized(self, current_price: Decimal) -> None:
        """按当前市场价格更新未实现盈亏"""
        if self.total_qty > 0 and self.avg_cost > 0:
            self.unrealized_pnl = self.total_qty * (current_price - self.avg_cost)
        else:
            self.unrealized_pnl = Decimal("0")
        self.updated_at = datetime.now(timezone.utc)

    def _update_status(self) -> None:
        if self.total_qty > 0:
            self.status = PositionStatus.ACTIVE
        else:
            self.status = PositionStatus.CLOSED

    def to_summary_dict(self) -> Dict[str, Any]:
        """序列化为摘要字典(用于 API 响应)"""
        return {
            "position_id": self.position_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "total_qty": str(self.total_qty),
            "avg_cost": str(self.avg_cost),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "status": self.status.value,
            "lot_count": len(self.lots),
            "closed_lot_count": len(self.closed_lots),
            "cost_basis_method": self.cost_basis_method.value,
            "updated_at": self.updated_at.isoformat(),
        }


# ==================== 账户/策略级持仓 ====================

@dataclass(slots=True)
class Position:
    """
    持仓领域模型

    记录策略/账户对某个标的的持仓情况.
    通过成交事件累积计算,而不是直接存储.

    向后兼容:strategy_id 默认为空字符串(表示无策略隔离的旧数据).
    新代码应始终传入 strategy_id.
    """
    position_id: str = ""                    # 持仓唯一ID
    symbol: str = ""                         # 交易标的
    strategy_id: str = ""                    # 策略ID(Batch 1 新增)
    quantity: Decimal = Decimal("0")          # 持仓数量
    avg_price: Decimal = Decimal("0")        # 平均持仓成本

    # 实时行情(由外部更新)
    current_price: Decimal = Decimal("0")

    # 盈亏计算
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))   # 已实现盈亏
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0")) # 未实现盈亏

    # Batch 1 新增字段
    status: PositionStatus = PositionStatus.ACTIVE  # 持仓状态
    position_source: PositionSource = PositionSource.STRATEGY  # 持仓来源

    # 时间戳
    opened_at: Optional[datetime] = None    # 建仓时间
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # 扩展
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.position_id:
            object.__setattr__(self, 'position_id', str(uuid.uuid4()))

        # 确保Decimal类型
        for attr in ['quantity', 'avg_price', 'current_price', 'realized_pnl', 'unrealized_pnl']:
            val = getattr(self, attr)
            if isinstance(val, (int, float, str)):
                object.__setattr__(self, attr, Decimal(str(val)))

    # ==================== 核心属性 ====================

    @property
    def market_value(self) -> Decimal:
        """市值 = 数量 * 当前价格"""
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> Decimal:
        """成本基础 = 数量 * 平均价格"""
        return self.quantity * self.avg_price

    @property
    def is_long(self) -> bool:
        """是否多头"""
        return self.quantity > 0

    @property
    def is_empty(self) -> bool:
        """是否空仓"""
        return self.quantity == 0

    # ==================== 持仓操作 ====================

    def update_price(self, current_price: Decimal) -> None:
        """更新当前价格,计算未实现盈亏"""
        self.current_price = current_price
        if self.quantity > 0:
            self.unrealized_pnl = (current_price - self.avg_price) * self.quantity
        else:
            self.unrealized_pnl = Decimal("0")
        self.updated_at = datetime.now(timezone.utc)

    def open(self, quantity: Decimal, price: Decimal) -> None:
        """
        开仓

        Args:
            quantity: 开仓数量
            price: 开仓价格
        """
        if quantity <= 0:
            raise ValueError("开仓数量必须大于0")

        if self.quantity == 0:
            # 新开仓
            self.quantity = quantity
            self.avg_price = price
            self.opened_at = datetime.now(timezone.utc)
            self.status = PositionStatus.ACTIVE
        else:
            # 加仓
            self.add(quantity, price)

        self.update_price(price)
        self.updated_at = datetime.now(timezone.utc)

    def add(self, add_quantity: Decimal, add_price: Decimal) -> None:
        """加仓"""
        if add_quantity <= 0:
            raise ValueError("加仓数量必须大于0")

        # 计算新的加权平均价
        total_cost = self.cost_basis + (add_quantity * add_price)
        self.quantity += add_quantity
        self.avg_price = total_cost / self.quantity if self.quantity > 0 else Decimal("0")
        self.updated_at = datetime.now(timezone.utc)

    def reduce(self, reduce_quantity: Decimal, reduce_price: Decimal) -> Decimal:
        """
        减仓

        Args:
            reduce_quantity: 减仓数量
            reduce_price: 减仓价格

        Returns:
            实现的盈亏
        """
        if reduce_quantity <= 0:
            raise ValueError("减仓数量必须大于0")

        # 限制减仓数量不能超过持仓
        actual_reduce = min(reduce_quantity, self.quantity)

        # 计算实现的盈亏
        cost = actual_reduce * self.avg_price
        proceeds = actual_reduce * reduce_price
        realized = proceeds - cost

        self.realized_pnl += realized
        self.quantity -= actual_reduce

        # 更新均价(如果是全平,均价归零)
        if self.quantity > 0:
            # 持仓数量减少,但平均成本不变
            pass
        else:
            self.avg_price = Decimal("0")
            self.unrealized_pnl = Decimal("0")
            self.status = PositionStatus.CLOSED

        self.updated_at = datetime.now(timezone.utc)
        return realized

    def close(self, price: Decimal) -> Decimal:
        """平仓"""
        return self.reduce(self.quantity, price)

    def __repr__(self) -> str:
        return (f"Position({self.symbol}, strategy={self.strategy_id or 'N/A'}, "
                f"qty={self.quantity}, avg={self.avg_price}, "
                f"pnl={self.realized_pnl + self.unrealized_pnl})")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Position):
            return NotImplemented
        return self.position_id == other.position_id

    def __hash__(self) -> int:
        return hash(self.position_id)


@dataclass(slots=True)
class BrokerPosition:
    """
    券商真实持仓

    来自券商API的实时持仓数据,作为对账的source of truth.
    """
    symbol: str = ""
    quantity: Decimal = Decimal("0")          # 持仓数量
    avg_price: Decimal = Decimal("0")        # 持仓成本
    unrealized_pnl: Decimal = Decimal("0")  # 未实现盈亏
    frozen_quantity: Decimal = Decimal("0")   # 冻结数量(如用于申购)

    # A股特有
    yesterday_quantity: Decimal = Decimal("0")  # 昨仓(用于T+1)

    def __post_init__(self):
        for attr in ['quantity', 'avg_price', 'unrealized_pnl', 'frozen_quantity', 'yesterday_quantity']:
            val = getattr(self, attr)
            if isinstance(val, (int, float, str)):
                object.__setattr__(self, attr, Decimal(str(val)))

    @property
    def available_quantity(self) -> Decimal:
        """可用数量 = 持仓 - 冻结"""
        return self.quantity - self.frozen_quantity


@dataclass(slots=True)
class PositionReconciliation:
    """
    持仓对账结果

    比较券商持仓与内部账本持仓的差异.
    """
    symbol: str = ""
    broker_quantity: Decimal = Decimal("0")  # 券商持仓
    ledger_quantity: Decimal = Decimal("0") # 账本持仓
    difference: Decimal = Decimal("0")      # 差异
    status: str = "CONSISTENT"              # CONSISTENT / DISCREPANCY
    action: Optional[str] = None             # 修复建议
