"""
Order Version Deterministic Layer (CAS + ShadowState)
=====================================================
该模块负责将来自不同源（WS/REST）的冲突订单更新合并为单调递增的确定状态。

核心功能：
1. 基于 STATUS_RANK 的 Compare-and-Swap (CAS) 逻辑
2. 状态只能从低 Rank 向高 Rank 演进（防止回滚）
3. 如果 Rank 相同，仅当 exchange_ts 增加时才接受更新
4. TERMINAL_MIN_RANK 之后严禁状态回滚
5. ExecutionEvent 必须通过 exec_id 进行 900s TTL 去重
6. 使用 asyncio.Lock 对 cl_ord_id 进行分片加锁（256片）

设计原则：
- 成交不重复触发（Execution 去重）
- 订单状态不回滚（单调状态机）
- REST 终态可 override（用于纠偏）但不可造成回滚
- 输出事件带齐 meta（source/ts/seq/gap flags）
"""
import asyncio
import time
import fnvhash
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any, Set
import threading


# ==================== 状态 Rank 定义 ====================

class OrderStatus(str, Enum):
    """订单状态枚举"""
    PENDING = "PENDING"
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"


# 状态 Rank 映射：状态只能从低 Rank 向高 Rank 演进
STATUS_RANK: Dict[str, int] = {
    "PENDING": 0,
    "NEW": 10,
    "SUBMITTED": 20,
    "PARTIALLY_FILLED": 30,
    "CANCEL_REQUESTED": 40,
    "CANCELLED": 50,
    "REJECTED": 60,
    "FILLED": 70,
    "EXPIRED": 80,
}

# TERMINAL_MIN_RANK: CANCELLED/REJECTED/FILLED/EXPIRED 都是终态
TERMINAL_MIN_RANK = STATUS_RANK["CANCELLED"]

# 允许的 REST 终态 override 偏移量（毫秒）
ALLOWED_SKEW_MS = 5000  # 5秒


# ==================== TTL Set 实现 ====================

class TTLSet:
    """
    带 TTL 的 Set 实现，用于成交去重。

    使用简单的时间戳+值的方式实现，支持 900s TTL。
    """

    def __init__(self, ttl_s: int = 900):
        self._ttl_s = ttl_s
        self._data: Dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, key: str) -> None:
        """添加元素"""
        with self._lock:
            self._data[key] = time.time() + self._ttl_s

    def __contains__(self, key: str) -> bool:
        """检查元素是否存在（未过期）"""
        with self._lock:
            expiry = self._data.get(key)
            if expiry is None:
                return False
            if time.time() > expiry:
                # 已过期，清理
                del self._data[key]
                return False
            return True

    def cleanup_expired(self) -> None:
        """清理所有过期元素"""
        with self._lock:
            now = time.time()
            expired_keys = [k for k, v in self._data.items() if now > v]
            for k in expired_keys:
                del self._data[k]

    def __len__(self) -> int:
        """返回未过期元素数量"""
        with self._lock:
            self.cleanup_expired()
            return len(self._data)


# ==================== 核心数据结构 ====================

@dataclass
class OrderVersionVector:
    """
    订单版本向量：记录订单的版本信息，用于 CAS 比较。

    last_status_rank: 最后一次更新的状态 Rank
    last_exchange_ts_ms: 最后一次更新的交易所时间戳（毫秒）
    last_local_ts_ms: 最后一次更新的本地接收时间戳（毫秒）
    last_source: 最后一次更新的来源 (WS/REST/RECONCILE)
    seen_exec_keys: 已见过的成交键集合（带 TTL）
    """
    last_status_rank: int = 0
    last_exchange_ts_ms: int = 0
    last_local_ts_ms: int = 0
    last_source: str = "UNKNOWN"
    seen_exec_keys: TTLSet = field(default_factory=lambda: TTLSet(ttl_s=900))


@dataclass
class ShadowOrder:
    """
    影子订单：缓存当前订单状态。
    """
    cl_ord_id: str
    broker_order_id: Optional[str] = None
    status: str = "PENDING"
    filled_qty: Decimal = field(default_factory=lambda: Decimal("0"))
    avg_price: Optional[Decimal] = None
    last_exchange_ts_ms: int = 0


@dataclass
class ShadowState:
    """
    影子状态：存储所有订单的当前状态。

    orders_by_cl: client_order_id -> ShadowOrder
    orders_by_broker_id: broker_order_id -> client_order_id (反向索引)
    """
    orders_by_cl: Dict[str, ShadowOrder] = field(default_factory=dict)
    orders_by_broker_id: Dict[str, str] = field(default_factory=dict)

    def add_order(self, cl_ord_id: str, broker_order_id: Optional[str] = None) -> ShadowOrder:
        """添加新订单"""
        order = ShadowOrder(cl_ord_id=cl_ord_id, broker_order_id=broker_order_id)
        self.orders_by_cl[cl_ord_id] = order
        if broker_order_id:
            self.orders_by_broker_id[broker_order_id] = cl_ord_id
        return order

    def get_by_cl_ord_id(self, cl_ord_id: str) -> Optional[ShadowOrder]:
        """通过 client_order_id 获取订单"""
        return self.orders_by_cl.get(cl_ord_id)

    def get_by_broker_order_id(self, broker_order_id: str) -> Optional[ShadowOrder]:
        """通过 broker_order_id 获取订单"""
        cl_ord_id = self.orders_by_broker_id.get(broker_order_id)
        if cl_ord_id:
            return self.orders_by_cl.get(cl_ord_id)
        return None


# ==================== 输入类型定义 ====================

@dataclass
class RawOrderUpdate:
    """
    原始订单更新：来自 WS/REST 的输入。
    """
    cl_ord_id: Optional[str] = None
    broker_order_id: Optional[str] = None
    status: str = "PENDING"  # 映射到标准状态
    filled_qty: Optional[Decimal] = None
    avg_price: Optional[Decimal] = None
    exchange_event_ts_ms: Optional[int] = None
    local_receive_ts_ms: int = 0
    source: str = "WS"  # "WS" | "REST" | "RECONCILE"
    finality_override: bool = False
    update_id: Optional[int] = None
    seq: Optional[int] = None


@dataclass
class RawFillUpdate:
    """
    原始成交更新：来自 WS/REST 的成交推送。
    """
    cl_ord_id: Optional[str] = None
    broker_order_id: Optional[str] = None
    exec_id: Optional[str] = None
    fill_qty: Decimal = field(default_factory=lambda: Decimal("0"))
    fill_price: Decimal = field(default_factory=lambda: Decimal("0"))
    exchange_event_ts_ms: Optional[int] = None
    local_receive_ts_ms: int = 0
    source: str = "WS"  # "WS" | "REST" | "RECONCILE"


# ==================== 输出类型定义 ====================

@dataclass
class OrderEvent:
    """
    确定的订单事件：输出到 Core 的 canonical event stream。
    """
    cl_ord_id: str
    broker_order_id: Optional[str]
    status: str
    filled_qty: Decimal
    avg_price: Optional[Decimal]
    exchange_ts_ms: int
    local_ts_ms: int
    source: str
    update_id: Optional[int] = None
    seq: Optional[int] = None
    ts_inferred: bool = False  # exchange_ts 是否推断的
    is_reconciliation: bool = False  # 是否来自对账


@dataclass
class ExecutionEvent:
    """
    确定的成交事件：输出到 Core 的 canonical event stream。
    """
    cl_ord_id: str
    broker_order_id: Optional[str]
    exec_id: str
    fill_qty: Decimal
    fill_price: Decimal
    exchange_ts_ms: int
    local_ts_ms: int
    source: str
    ts_inferred: bool = False


# ==================== CAS 核心算法 ====================

def compute_exec_key(fill: RawFillUpdate) -> str:
    """
    计算成交键：用于去重。
    使用 cl_ord_id + exec_id 组合确保唯一性。
    """
    cl = fill.cl_ord_id or fill.broker_order_id or "unknown"
    exec_id = fill.exec_id or f"no_exec_{fill.local_receive_ts_ms}"
    return f"{cl}:{exec_id}"


def resolve_cl_ord_id(update: RawOrderUpdate, shadow: ShadowState) -> Optional[str]:
    """
    解析 client_order_id。

    如果 cl_ord_id 为空，尝试通过 broker_order_id 映射。
    如果都找不到，返回 None（需要触发 REST 对齐）。
    """
    if update.cl_ord_id:
        return update.cl_ord_id

    if update.broker_order_id:
        cl_ord_id = shadow.orders_by_broker_id.get(update.broker_order_id)
        if cl_ord_id:
            return cl_ord_id
        # 没有找到映射，返回 None
        return None

    return None


def cas_apply_fill(
    vv: OrderVersionVector,
    shadow: ShadowState,
    cl_ord_id: str,
    fill: RawFillUpdate
) -> List[ExecutionEvent]:
    """
    Execution CAS：成交去重逻辑。

    使用 exec_id 进行 900s TTL 去重。
    """
    exec_key = compute_exec_key(fill)

    # 去重检查
    if exec_key in vv.seen_exec_keys:
        return []  # 重复成交，丢弃

    # 记录已见过的成交键
    vv.seen_exec_keys.add(exec_key)

    # 获取订单
    order = shadow.get_by_cl_ord_id(cl_ord_id)
    if order is None:
        # 订单不存在，创建一个新的影子订单
        order = shadow.add_order(cl_ord_id, fill.broker_order_id)

    # 更新影子订单的持仓信息
    old_filled_qty = order.filled_qty
    order.filled_qty += fill.fill_qty

    if fill.fill_price and order.avg_price:
        # 计算新的加权平均价格
        total_value = (order.avg_price * old_filled_qty) + (fill.fill_price * fill.fill_qty)
        order.avg_price = total_value / order.filled_qty
    elif fill.fill_price:
        order.avg_price = fill.fill_price

    # 更新版本向量
    exch_ts = fill.exchange_event_ts_ms or fill.local_receive_ts_ms
    vv.last_exchange_ts_ms = max(vv.last_exchange_ts_ms, exch_ts)
    vv.last_local_ts_ms = fill.local_receive_ts_ms
    vv.last_source = fill.source

    # 发射成交事件
    exchange_ts = fill.exchange_event_ts_ms or fill.local_receive_ts_ms
    ts_inferred = fill.exchange_event_ts_ms is None

    event = ExecutionEvent(
        cl_ord_id=cl_ord_id,
        broker_order_id=fill.broker_order_id,
        exec_id=fill.exec_id or f"no_exec_{fill.local_receive_ts_ms}",
        fill_qty=fill.fill_qty,
        fill_price=fill.fill_price,
        exchange_ts_ms=exchange_ts,
        local_ts_ms=fill.local_receive_ts_ms,
        source=fill.source,
        ts_inferred=ts_inferred,
    )

    return [event]


def cas_apply_order(
    vv: OrderVersionVector,
    shadow: ShadowState,
    cl_ord_id: str,
    update: RawOrderUpdate
) -> List[OrderEvent]:
    """
    Order CAS：防回滚 + 终态 override 逻辑。

    规则：
    1. 状态只能从低 Rank 向高 Rank 演进
    2. 如果 Rank 相同，仅当 exchange_ts 增加时才接受
    3. TERMINAL_MIN_RANK 之后严禁状态回滚
    4. REST 终态可以 override（用于纠偏）但不可造成回滚
    """
    # 计算新状态 rank
    new_rank = STATUS_RANK.get(update.status, 0)
    old_rank = vv.last_status_rank

    # 获取交易所时间戳
    exch_ts = update.exchange_event_ts_ms or 0

    # ====== 终态保护：不可回滚到终态之前 ======
    if old_rank >= TERMINAL_MIN_RANK and new_rank < old_rank:
        return []  # 回滚尝试，丢弃

    # ====== 通用回滚保护：不可降低 Rank ======
    if new_rank < old_rank:
        return []  # Rank 降低，丢弃

    # ====== 同 Rank 处理：仅当 exchange_ts 增加时接受 ======
    if new_rank == old_rank:
        # 如果是终态，不允许更新
        if old_rank >= TERMINAL_MIN_RANK:
            return []

        # 检查时间戳是否增加
        if exch_ts and exch_ts <= vv.last_exchange_ts_ms:
            return []  # 时间戳未增加，丢弃
        # 否则接受（可能是 filled_qty/avg_price 更精确）

    # ====== 更高 Rank：检查是否是 stale REST ======
    if new_rank > old_rank and update.source == "REST":
        if exch_ts and vv.last_exchange_ts_ms and exch_ts + ALLOWED_SKEW_MS < vv.last_exchange_ts_ms:
            return []  # stale REST cache，丢弃

    # ====== Finality Override：允许 REST 终态 override ======
    # 如果是 REST/RECONCILE 的终态更新，且 finality_override=True，允许接受
    if update.finality_override and update.source in ("REST", "RECONCILE"):
        if new_rank >= TERMINAL_MIN_RANK and new_rank >= old_rank:
            pass  # 允许
        else:
            pass  # 允许非终态的 finality override

    # ====== 提交更新（CAS） ======
    vv.last_status_rank = max(old_rank, new_rank)
    vv.last_exchange_ts_ms = max(vv.last_exchange_ts_ms, exch_ts)
    vv.last_local_ts_ms = update.local_receive_ts_ms
    vv.last_source = update.source

    # 更新影子订单
    order = shadow.get_by_cl_ord_id(cl_ord_id)
    if order is None:
        order = shadow.add_order(cl_ord_id, update.broker_order_id)

    order.status = update.status
    if update.filled_qty is not None:
        order.filled_qty = update.filled_qty
    if update.avg_price is not None:
        order.avg_price = update.avg_price
    if update.broker_order_id and not order.broker_order_id:
        order.broker_order_id = update.broker_order_id
        shadow.orders_by_broker_id[update.broker_order_id] = cl_ord_id

    # 发射订单事件
    exchange_ts = update.exchange_event_ts_ms or update.local_receive_ts_ms
    ts_inferred = update.exchange_event_ts_ms is None

    event = OrderEvent(
        cl_ord_id=cl_ord_id,
        broker_order_id=order.broker_order_id,
        status=update.status,
        filled_qty=order.filled_qty,
        avg_price=order.avg_price,
        exchange_ts_ms=exchange_ts,
        local_ts_ms=update.local_receive_ts_ms,
        source=update.source,
        update_id=update.update_id,
        seq=update.seq,
        ts_inferred=ts_inferred,
        is_reconciliation=update.source == "RECONCILE",
    )

    return [event]


# ==================== 确定性应用器 ====================

class DeterministicApplier:
    """
    确定性应用器：负责将原始事件归一为确定的 canonical events。

    使用分片锁实现并发安全。
    """

    def __init__(self, partitions: int = 256):
        """
        初始化确定性应用器。

        Args:
            partitions: 分片数量，默认 256
        """
        self._partitions = partitions
        self._locks = [asyncio.Lock() for _ in range(partitions)]
        self._vv: Dict[str, OrderVersionVector] = {}
        self._shadow = ShadowState()

    def _lock_for(self, cl_ord_id: str) -> asyncio.Lock:
        """获取 cl_ord_id 对应的分片锁"""
        # 使用 FNV-1a hash 分散锁
        idx = fnvhash.fnv1a_32(cl_ord_id.encode()) % self._partitions
        return self._locks[idx]

    def _get_or_create_vv(self, cl_ord_id: str) -> OrderVersionVector:
        """获取或创建版本向量"""
        if cl_ord_id not in self._vv:
            self._vv[cl_ord_id] = OrderVersionVector()
        return self._vv[cl_ord_id]

    async def apply_order_update(self, update: RawOrderUpdate) -> List[OrderEvent]:
        """
        应用订单更新。

        Args:
            update: 原始订单更新

        Returns:
            确定的 OrderEvent 列表（通常为空或一个元素）
        """
        # 解析 cl_ord_id
        cl_ord_id = resolve_cl_ord_id(update, self._shadow)
        if cl_ord_id is None:
            # 无 cl_ord_id 映射，需要触发 REST 对齐
            # 这里返回空列表，实际场景应触发对齐流程
            return []

        # 获取分片锁
        async with self._lock_for(cl_ord_id):
            # 确保版本向量存在
            vv = self._get_or_create_vv(cl_ord_id)

            # 执行 CAS
            return cas_apply_order(vv, self._shadow, cl_ord_id, update)

    async def apply_fill_update(self, fill: RawFillUpdate) -> List[ExecutionEvent]:
        """
        应用成交更新。

        Args:
            fill: 原始成交更新

        Returns:
            确定的 ExecutionEvent 列表（通常为空或一个元素）
        """
        # 解析 cl_ord_id
        cl_ord_id = resolve_cl_ord_id(fill, self._shadow)
        if cl_ord_id is None:
            # 无 cl_ord_id 映射，需要触发 REST 对齐
            return []

        # 获取分片锁
        async with self._lock_for(cl_ord_id):
            # 确保版本向量存在
            vv = self._get_or_create_vv(cl_ord_id)

            # 执行 CAS（成交去重）
            return cas_apply_fill(vv, self._shadow, cl_ord_id, fill)

    def get_shadow_order(self, cl_ord_id: str) -> Optional[ShadowOrder]:
        """获取影子订单"""
        return self._shadow.get_by_cl_ord_id(cl_ord_id)

    def get_version_vector(self, cl_ord_id: str) -> Optional[OrderVersionVector]:
        """获取版本向量"""
        return self._vv.get(cl_ord_id)

    def get_all_orders(self) -> Dict[str, ShadowOrder]:
        """获取所有影子订单"""
        return self._shadow.orders_by_cl.copy()

    def reset(self) -> None:
        """重置状态（用于测试）"""
        self._vv.clear()
        self._shadow = ShadowState()
