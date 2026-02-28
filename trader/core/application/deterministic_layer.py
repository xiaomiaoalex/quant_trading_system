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
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any, Set, Union
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

TERMINAL_STATUSES = {"CANCELLED", "REJECTED", "FILLED", "EXPIRED"}


# ==================== TTL Set 实现 ====================

class TTLSet:
    """
    带 TTL 的 Set 实现，用于成交去重。

    使用简单的时间戳+值的方式实现，支持 900s TTL。
    """

    def __init__(self, ttl_s: int = 900):
        self._ttl_s = ttl_s
        self._data: Dict[str, float] = {}
        self._lock = threading.RLock()

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
            if time.time() >= expiry:
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


# ==================== PendingBuffer（新增）====================

from collections import deque


@dataclass
class PendingItem:
    """待处理项：存储无法 resolve cl_ord_id 的事件"""
    kind: str  # "order" | "fill"
    payload: object
    first_seen_ts: float


class PendingBuffer:
    """
    PendingBuffer：缓存"无法 resolve cl_ord_id"的事件。
    - key: broker_order_id
    - value: deque[PendingItem]
    """

    def __init__(self, ttl_s: int = 120, max_keys: int = 100_000, max_items_per_key: int = 200):
        self.ttl_s = ttl_s
        self.max_keys = max_keys
        self.max_items_per_key = max_items_per_key
        self._buf: dict[str, deque[PendingItem]] = {}
        self._order: deque[str] = deque()

    def add(self, broker_order_id: str, item: PendingItem) -> None:
        """添加事件到 buffer"""
        now = time.time()
        self.cleanup(now)

        if broker_order_id not in self._buf:
            if len(self._buf) >= self.max_keys:
                old = self._order.popleft()
                self._buf.pop(old, None)
            self._buf[broker_order_id] = deque()
            self._order.append(broker_order_id)

        dq = self._buf[broker_order_id]
        if len(dq) >= self.max_items_per_key:
            dq.popleft()
        dq.append(item)

    def pop_all(self, broker_order_id: str) -> list[PendingItem]:
        """获取并移除所有与 broker_order_id 相关的项"""
        dq = self._buf.pop(broker_order_id, None)
        if dq is None:
            return []
        try:
            self._order.remove(broker_order_id)
        except ValueError:
            pass
        return list(dq)

    def cleanup(self, now: float | None = None) -> None:
        """清理过期项"""
        now = now or time.time()
        expired_keys = []
        for k, dq in self._buf.items():
            if dq and (now - dq[0].first_seen_ts) > self.ttl_s:
                expired_keys.append(k)
        for k in expired_keys:
            self._buf.pop(k, None)
            try:
                self._order.remove(k)
            except ValueError:
                pass

    def size(self) -> int:
        """返回 buffer 大小"""
        return len(self._buf)

    def clear(self) -> None:
        """清空 buffer"""
        self._buf.clear()
        self._order.clear()


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

def compute_exec_key(fill: "RawFillUpdate") -> str:
    """
    计算成交键：用于去重。
    优先使用 cl_ord_id + exec_id；若 exec_id 缺失，使用稳定哈希（不依赖 local_ts）。
    """
    cl = fill.cl_ord_id or (fill.broker_order_id if fill.broker_order_id else "unknown")
    if fill.exec_id:
        return f"{cl}:{fill.exec_id}"

    # exec_id 缺失：用稳定哈希（避免 local_receive_ts 变化导致无法去重）
    exch_ts = fill.exchange_event_ts_ms or 0
    payload = f"{cl}|{fill.broker_order_id or ''}|{fill.fill_qty}|{fill.fill_price}|{exch_ts}|{fill.source}"
    h = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"{cl}:noexec:{h}"


def resolve_cl_ord_id(
    update: Union["RawOrderUpdate", "RawFillUpdate"],
    shadow: ShadowState
) -> Optional[str]:
    """
    解析 client_order_id。
    支持 RawOrderUpdate 和 RawFillUpdate 两种类型。
    如果 cl_ord_id 为空，尝试通过 broker_order_id 映射。
    """
    if getattr(update, "cl_ord_id", None):
        return update.cl_ord_id

    broker_id = getattr(update, "broker_order_id", None)
    if broker_id:
        return shadow.orders_by_broker_id.get(broker_id)

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
    不污染 order 的 last_exchange_ts_ms。
    """
    exec_key = compute_exec_key(fill)
    if exec_key in vv.seen_exec_keys:
        return []
    vv.seen_exec_keys.add(exec_key)

    order = shadow.get_by_cl_ord_id(cl_ord_id)
    if order is None:
        order = shadow.add_order(cl_ord_id, fill.broker_order_id)

    old_filled_qty = order.filled_qty
    order.filled_qty += fill.fill_qty

    if order.avg_price is not None:
        total_value = (order.avg_price * old_filled_qty) + (fill.fill_price * fill.fill_qty)
        order.avg_price = total_value / order.filled_qty
    else:
        order.avg_price = fill.fill_price

    # version vector: do not infer exchange ts into vv.last_exchange_ts_ms
    if fill.exchange_event_ts_ms is not None:
        vv.last_exchange_ts_ms = max(vv.last_exchange_ts_ms, fill.exchange_event_ts_ms)
    vv.last_local_ts_ms = fill.local_receive_ts_ms
    vv.last_source = fill.source

    exchange_ts_out = fill.exchange_event_ts_ms or fill.local_receive_ts_ms
    ts_inferred = fill.exchange_event_ts_ms is None

    ev = ExecutionEvent(
        cl_ord_id=cl_ord_id,
        broker_order_id=fill.broker_order_id,
        exec_id=fill.exec_id or exec_key.split(":")[-1],
        fill_qty=fill.fill_qty,
        fill_price=fill.fill_price,
        exchange_ts_ms=exchange_ts_out,
        local_ts_ms=fill.local_receive_ts_ms,
        source=fill.source,
        ts_inferred=ts_inferred,
    )
    return [ev]


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
    5. exchange_ts 必须单调递增
    6. filled_qty 数值必须单调递增
    """
    new_rank = STATUS_RANK.get(update.status, 0)
    old_rank = vv.last_status_rank

    exch_ts = update.exchange_event_ts_ms
    has_exch_ts = exch_ts is not None

    order = shadow.get_by_cl_ord_id(cl_ord_id)
    if order is None:
        order = shadow.add_order(cl_ord_id, update.broker_order_id)

    # ---------- Finality override: ONLY for terminal statuses ----------
    if update.finality_override and update.source in ("REST", "RECONCILE"):
        if update.status not in TERMINAL_STATUSES:
            return []
        if new_rank < old_rank:
            return []
        accept_override = True
    else:
        accept_override = False

    # ---------- Rank rollback protection ----------
    if old_rank >= TERMINAL_MIN_RANK and new_rank < old_rank:
        return []
    if new_rank < old_rank:
        return []

    # ---------- Same-rank handling ----------
    if new_rank == old_rank:
        if old_rank >= TERMINAL_MIN_RANK:
            return []

        if not has_exch_ts:
            if update.filled_qty is None:
                return []
            if update.filled_qty <= order.filled_qty:
                return []

        if has_exch_ts and vv.last_exchange_ts_ms and exch_ts <= vv.last_exchange_ts_ms:
            return []

    # ---------- Higher-rank stale REST protection ----------
    if (new_rank > old_rank) and update.source == "REST" and (not accept_override):
        if has_exch_ts and vv.last_exchange_ts_ms and exch_ts + ALLOWED_SKEW_MS < vv.last_exchange_ts_ms:
            return []

    # ---------- Numeric monotonicity protection ----------
    if update.filled_qty is not None and update.filled_qty < order.filled_qty:
        return []

    # ---------- Commit CAS ----------
    vv.last_status_rank = max(old_rank, new_rank)

    if has_exch_ts:
        vv.last_exchange_ts_ms = max(vv.last_exchange_ts_ms, exch_ts)
    vv.last_local_ts_ms = update.local_receive_ts_ms
    vv.last_source = update.source

    order.status = update.status
    if update.filled_qty is not None:
        order.filled_qty = update.filled_qty
    if update.avg_price is not None:
        if (update.filled_qty is not None and update.filled_qty > Decimal("0")) or order.avg_price is None:
            order.avg_price = update.avg_price
    if update.broker_order_id and not order.broker_order_id:
        order.broker_order_id = update.broker_order_id
        shadow.orders_by_broker_id[update.broker_order_id] = cl_ord_id

    exchange_ts_out = update.exchange_event_ts_ms or update.local_receive_ts_ms
    ts_inferred = update.exchange_event_ts_ms is None

    event = OrderEvent(
        cl_ord_id=cl_ord_id,
        broker_order_id=order.broker_order_id,
        status=order.status,
        filled_qty=order.filled_qty,
        avg_price=order.avg_price,
        exchange_ts_ms=exchange_ts_out,
        local_ts_ms=update.local_receive_ts_ms,
        source=update.source,
        update_id=update.update_id,
        seq=update.seq,
        ts_inferred=ts_inferred,
        is_reconciliation=(update.source == "RECONCILE") or accept_override,
    )

    return [event]


# ==================== 确定性应用器 ====================

class DeterministicApplier:
    """
    确定性应用器：负责将原始事件归一为确定的 canonical events。

    使用分片锁实现并发安全。
    """

    def __init__(self, partitions: int = 256, pending_buffer_size: int = 10000):
        self._partitions = partitions
        self._locks = [asyncio.Lock() for _ in range(partitions)]
        self._vv: Dict[str, OrderVersionVector] = {}
        self._shadow = ShadowState()
        self._pending = PendingBuffer(ttl_s=120, max_keys=100_000, max_items_per_key=200)
        self._order_last_touch: Dict[str, float] = {}
        self._retention_s_terminal = 3600
        self._max_orders = 1_000_000
        self._on_resync_callback = None
        self._eviction_counter = 0

    def _lock_for(self, cl_ord_id: str) -> asyncio.Lock:
        """获取 cl_ord_id 对应的分片锁"""
        # 使用 FNV-1a hash 分散锁
        idx = fnvhash.fnv1a_32(cl_ord_id.encode()) % self._partitions
        return self._locks[idx]

    def _get_or_create_vv(self, cl_ord_id: str) -> OrderVersionVector:
        if cl_ord_id not in self._vv:
            self._vv[cl_ord_id] = OrderVersionVector()
        return self._vv[cl_ord_id]

    def set_resync_callback(self, callback) -> None:
        self._on_resync_callback = callback

    def _touch(self, cl: str) -> None:
        self._order_last_touch[cl] = time.time()

    def _evict_if_needed(self) -> None:
        now = time.time()
        to_del = []
        for cl, order in self._shadow.orders_by_cl.items():
            if order.status in TERMINAL_STATUSES:
                last = self._order_last_touch.get(cl, now)
                if now - last > self._retention_s_terminal:
                    to_del.append(cl)
        for cl in to_del:
            o = self._shadow.orders_by_cl.pop(cl, None)
            if o and o.broker_order_id:
                self._shadow.orders_by_broker_id.pop(o.broker_order_id, None)
            self._vv.pop(cl, None)
            self._order_last_touch.pop(cl, None)
        if len(self._shadow.orders_by_cl) > self._max_orders:
            oldest = sorted(self._order_last_touch.items(), key=lambda x: x[1])[: (len(self._shadow.orders_by_cl) - self._max_orders)]
            for cl, _ in oldest:
                o = self._shadow.orders_by_cl.pop(cl, None)
                if o and o.broker_order_id:
                    self._shadow.orders_by_broker_id.pop(o.broker_order_id, None)
                self._vv.pop(cl, None)
                self._order_last_touch.pop(cl, None)

    async def _flush_pending_async(self, broker_order_id: str) -> List:
        out: List = []
        items = self._pending.pop_all(broker_order_id)
        for it in items:
            if it.kind == "order":
                out.extend(await self.apply_order_update(it.payload))
            else:
                out.extend(await self.apply_fill_update(it.payload))
        return out

    async def apply_order_update(self, update: RawOrderUpdate) -> List[OrderEvent]:
        cl_ord_id = resolve_cl_ord_id(update, self._shadow)
        if cl_ord_id is None:
            if update.broker_order_id:
                self._pending.add(update.broker_order_id, PendingItem("order", update, time.time()))
            if self._on_resync_callback:
                await self._on_resync_callback(f"unknown_order:{update.broker_order_id}")
            return []
        async with self._lock_for(cl_ord_id):
            vv = self._get_or_create_vv(cl_ord_id)
            events = cas_apply_order(vv, self._shadow, cl_ord_id, update)
            if events and update.broker_order_id:
                self._shadow.orders_by_broker_id[update.broker_order_id] = cl_ord_id
                self._touch(cl_ord_id)
                events.extend(await self._flush_pending_async(update.broker_order_id))
            self._eviction_counter += 1
            if self._eviction_counter % 256 == 0:
                self._evict_if_needed()
            return events

    async def apply_fill_update(self, fill: RawFillUpdate) -> List[ExecutionEvent]:
        cl_ord_id = resolve_cl_ord_id(fill, self._shadow)
        if cl_ord_id is None:
            if fill.broker_order_id:
                self._pending.add(fill.broker_order_id, PendingItem("fill", fill, time.time()))
            if self._on_resync_callback:
                await self._on_resync_callback(f"unknown_fill:{fill.broker_order_id}")
            return []
        async with self._lock_for(cl_ord_id):
            vv = self._get_or_create_vv(cl_ord_id)
            events = cas_apply_fill(vv, self._shadow, cl_ord_id, fill)
            if events and fill.broker_order_id:
                self._shadow.orders_by_broker_id[fill.broker_order_id] = cl_ord_id
                self._touch(cl_ord_id)
                events.extend(await self._flush_pending_async(fill.broker_order_id))
            self._eviction_counter += 1
            if self._eviction_counter % 256 == 0:
                self._evict_if_needed()
            return events

    def get_shadow_order(self, cl_ord_id: str) -> Optional[ShadowOrder]:
        """获取影子订单"""
        return self._shadow.get_by_cl_ord_id(cl_ord_id)

    def get_version_vector(self, cl_ord_id: str) -> Optional[OrderVersionVector]:
        """获取版本向量"""
        return self._vv.get(cl_ord_id)

    def get_all_orders(self) -> Dict[str, ShadowOrder]:
        return self._shadow.orders_by_cl.copy()

    def get_pending_count(self) -> int:
        return self._pending.size()

    def reset(self) -> None:
        self._vv.clear()
        self._shadow = ShadowState()
        self._pending.clear()
        self._order_last_touch.clear()
