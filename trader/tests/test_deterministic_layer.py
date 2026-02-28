"""
Unit Tests - Deterministic Layer
================================
订单版本确定性层的单元测试，覆盖核心功能和边界情况。

测试覆盖场景：
1. 正常生命周期：NEW -> PARTIALLY_FILLED -> FILLED
2. WS 优先场景：WS 先到，REST 后到但状态滞后 -> 丢弃 REST
3. 时间戳仲裁：相同 Rank 但时间戳更旧 -> 丢弃
4. 终态不可回滚：FILLED 后不能再变为 PARTIALLY_FILLED
5. 成交去重：同一 exec_id 重复发送 -> 只发一次
6. 并发安全：多协程同时更新同一订单 -> 结果确定
7. Finality Override：REST 终态可以 override
8. 无 cl_ord_id 映射处理
"""
import pytest
import asyncio
from decimal import Decimal
from trader.core.application.deterministic_layer import (
    DeterministicApplier,
    RawOrderUpdate,
    RawFillUpdate,
    OrderVersionVector,
    ShadowState,
    ShadowOrder,
    TTLSet,
    STATUS_RANK,
    TERMINAL_MIN_RANK,
    cas_apply_order,
    cas_apply_fill,
    compute_exec_key,
    resolve_cl_ord_id,
    OrderStatus,
)


# ==================== TTLSet Tests ====================

class TestTTLSet:
    """TTLSet 的单元测试"""

    def test_add_and_contains(self):
        """测试添加和包含检查"""
        ttl_set = TTLSet(ttl_s=1)  # 1秒 TTL
        ttl_set.add("key1")
        assert "key1" in ttl_set

    def test_expired_key(self):
        """测试过期键被清理"""
        import time
        ttl_set = TTLSet(ttl_s=0)  # 使用0秒TTL，立即过期
        ttl_set.add("key1")
        # 手动触发清理
        assert "key1" not in ttl_set

    def test_duplicate_add(self):
        """测试重复添加"""
        ttl_set = TTLSet(ttl_s=10)
        ttl_set.add("key1")
        ttl_set.add("key1")  # 不应报错
        assert "key1" in ttl_set
        assert len(ttl_set) == 1

    def test_len(self):
        """测试长度"""
        ttl_set = TTLSet(ttl_s=10)
        assert len(ttl_set) == 0
        ttl_set.add("key1")
        ttl_set.add("key2")
        assert len(ttl_set) == 2


# ==================== Data Structure Tests ====================

class TestOrderVersionVector:
    """OrderVersionVector 的单元测试"""

    def test_default_values(self):
        """测试默认值"""
        vv = OrderVersionVector()
        assert vv.last_status_rank == 0
        assert vv.last_exchange_ts_ms == 0
        assert vv.last_source == "UNKNOWN"

    def test_ttl_set_initialization(self):
        """测试 TTL 集合初始化"""
        vv = OrderVersionVector()
        assert vv.seen_exec_keys is not None
        assert len(vv.seen_exec_keys) == 0


class TestShadowState:
    """ShadowState 的单元测试"""

    def test_add_order(self):
        """测试添加订单"""
        shadow = ShadowState()
        order = shadow.add_order("cl_001", "broker_001")
        assert order.cl_ord_id == "cl_001"
        assert order.broker_order_id == "broker_001"

    def test_get_by_cl_ord_id(self):
        """测试通过 cl_ord_id 获取"""
        shadow = ShadowState()
        shadow.add_order("cl_001", "broker_001")
        order = shadow.get_by_cl_ord_id("cl_001")
        assert order is not None
        assert order.cl_ord_id == "cl_001"

    def test_get_by_broker_order_id(self):
        """测试通过 broker_order_id 获取"""
        shadow = ShadowState()
        shadow.add_order("cl_001", "broker_001")
        order = shadow.get_by_broker_order_id("broker_001")
        assert order is not None
        assert order.cl_ord_id == "cl_001"

    def test_get_nonexistent(self):
        """测试获取不存在的订单"""
        shadow = ShadowState()
        assert shadow.get_by_cl_ord_id("nonexistent") is None


class TestHelperFunctions:
    """辅助函数的单元测试"""

    def test_compute_exec_key(self):
        """测试成交键计算"""
        fill = RawFillUpdate(
            cl_ord_id="cl_001",
            exec_id="exec_001",
            fill_qty=Decimal("1"),
            fill_price=Decimal("100"),
            local_receive_ts_ms=1000,
        )
        key = compute_exec_key(fill)
        assert key == "cl_001:exec_001"

    def test_compute_exec_key_no_cl_ord_id(self):
        """测试无 cl_ord_id 时的成交键计算"""
        fill = RawFillUpdate(
            broker_order_id="broker_001",
            exec_id="exec_001",
            fill_qty=Decimal("1"),
            fill_price=Decimal("100"),
            local_receive_ts_ms=1000,
        )
        key = compute_exec_key(fill)
        assert key == "broker_001:exec_001"

    def test_resolve_cl_ord_id_direct(self):
        """测试直接解析 cl_ord_id"""
        shadow = ShadowState()
        update = RawOrderUpdate(cl_ord_id="cl_001", status="NEW")
        cl = resolve_cl_ord_id(update, shadow)
        assert cl == "cl_001"

    def test_resolve_cl_ord_id_via_broker(self):
        """测试通过 broker_order_id 解析"""
        shadow = ShadowState()
        shadow.add_order("cl_001", "broker_001")
        update = RawOrderUpdate(broker_order_id="broker_001", status="NEW")
        cl = resolve_cl_ord_id(update, shadow)
        assert cl == "cl_001"

    def test_resolve_cl_ord_id_not_found(self):
        """测试找不到映射"""
        shadow = ShadowState()
        update = RawOrderUpdate(broker_order_id="unknown", status="NEW")
        cl = resolve_cl_ord_id(update, shadow)
        assert cl is None


# ==================== CAS Algorithm Tests ====================

class TestCASApplyOrder:
    """cas_apply_order 的单元测试"""

    def test_normal_lifecycle(self):
        """测试正常生命周期：NEW -> PARTIALLY_FILLED -> FILLED"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        # NEW
        update1 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update1)
        assert len(events) == 1
        assert events[0].status == "NEW"
        assert vv.last_status_rank == STATUS_RANK["NEW"]

        # PARTIALLY_FILLED
        update2 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="PARTIALLY_FILLED",
            filled_qty=Decimal("5"),
            exchange_event_ts_ms=2000,
            local_receive_ts_ms=2000,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update2)
        assert len(events) == 1
        assert events[0].status == "PARTIALLY_FILLED"
        assert vv.last_status_rank == STATUS_RANK["PARTIALLY_FILLED"]

        # FILLED
        update3 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            filled_qty=Decimal("10"),
            exchange_event_ts_ms=3000,
            local_receive_ts_ms=3000,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update3)
        assert len(events) == 1
        assert events[0].status == "FILLED"
        assert vv.last_status_rank == STATUS_RANK["FILLED"]

    def test_stale_rest_rejected(self):
        """测试 WS 终态后 REST 滞后状态被拒绝"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        # WS: FILLED
        ws_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            filled_qty=Decimal("10"),
            exchange_event_ts_ms=3000,
            local_receive_ts_ms=3000,
            source="WS",
        )
        cas_apply_order(vv, shadow, "cl_001", ws_update)
        assert vv.last_status_rank == STATUS_RANK["FILLED"]

        # REST: PARTIALLY_FILLED (stale)
        rest_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="PARTIALLY_FILLED",
            filled_qty=Decimal("5"),
            exchange_event_ts_ms=2000,  # 更早的时间戳
            local_receive_ts_ms=4000,
            source="REST",
        )
        events = cas_apply_order(vv, shadow, "cl_001", rest_update)
        assert len(events) == 0  # 应该被拒绝
        assert shadow.get_by_cl_ord_id("cl_001").status == "FILLED"

    def test_same_rank_timestamp_update(self):
        """测试相同 Rank 但时间戳更新"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        # 第一次 NEW
        update1 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            filled_qty=Decimal("0"),
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        cas_apply_order(vv, shadow, "cl_001", update1)

        # 相同状态但有更新
        update2 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            filled_qty=Decimal("5"),  # 更新了 filled_qty
            exchange_event_ts_ms=1500,  # 更晚的时间戳
            local_receive_ts_ms=1500,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update2)
        assert len(events) == 1
        assert events[0].filled_qty == Decimal("5")

    def test_same_rank_stale_timestamp_rejected(self):
        """测试相同 Rank 但时间戳更旧被拒绝"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        update1 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            exchange_event_ts_ms=1500,
            local_receive_ts_ms=1500,
            source="WS",
        )
        cas_apply_order(vv, shadow, "cl_001", update1)

        # 相同状态但时间戳更旧
        update2 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=2000,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update2)
        assert len(events) == 0

    def test_terminal_state_immutable(self):
        """测试终态不可改变"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        # FILLED
        update1 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            filled_qty=Decimal("10"),
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        cas_apply_order(vv, shadow, "cl_001", update1)

        # 尝试再次更新 FILLED（相同 Rank）
        update2 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            filled_qty=Decimal("10"),
            exchange_event_ts_ms=2000,
            local_receive_ts_ms=2000,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update2)
        assert len(events) == 0

    def test_finality_override(self):
        """测试终态 override"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        # WS: PARTIALLY_FILLED
        ws_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="PARTIALLY_FILLED",
            filled_qty=Decimal("5"),
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        cas_apply_order(vv, shadow, "cl_001", ws_update)

        # REST: FILLED with finality_override
        rest_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            filled_qty=Decimal("10"),
            exchange_event_ts_ms=800,  # 更早的时间戳，但有 finality_override
            local_receive_ts_ms=2000,
            source="REST",
            finality_override=True,
        )
        events = cas_apply_order(vv, shadow, "cl_001", rest_update)
        assert len(events) == 1
        assert events[0].status == "FILLED"

    def test_reconciliation_override(self):
        """测试对账 override"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        # WS: NEW
        update1 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        cas_apply_order(vv, shadow, "cl_001", update1)

        # RECONCILE: CANCELLED (override)
        recon_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="CANCELLED",
            exchange_event_ts_ms=500,
            local_receive_ts_ms=2000,
            source="RECONCILE",
            finality_override=True,
        )
        events = cas_apply_order(vv, shadow, "cl_001", recon_update)
        assert len(events) == 1
        assert events[0].status == "CANCELLED"

    def test_rank_downgrade_rejected(self):
        """测试 Rank 降低被拒绝"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        # FILLED
        update1 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        cas_apply_order(vv, shadow, "cl_001", update1)

        # 尝试回滚到 PARTIALLY_FILLED
        update2 = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="PARTIALLY_FILLED",
            exchange_event_ts_ms=2000,
            local_receive_ts_ms=2000,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update2)
        assert len(events) == 0


class TestCASApplyFill:
    """cas_apply_fill 的单元测试"""

    def test_fill_deduplication(self):
        """测试成交去重"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        fill1 = RawFillUpdate(
            cl_ord_id="cl_001",
            exec_id="exec_001",
            fill_qty=Decimal("5"),
            fill_price=Decimal("100"),
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )

        # 第一次成交
        events1 = cas_apply_fill(vv, shadow, "cl_001", fill1)
        assert len(events1) == 1
        assert events1[0].exec_id == "exec_001"

        # 重复成交
        events2 = cas_apply_fill(vv, shadow, "cl_001", fill1)
        assert len(events2) == 0  # 应该被去重

    def test_multiple_fills(self):
        """测试多次成交"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        # 第一次成交
        fill1 = RawFillUpdate(
            cl_ord_id="cl_001",
            exec_id="exec_001",
            fill_qty=Decimal("5"),
            fill_price=Decimal("100"),
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        cas_apply_fill(vv, shadow, "cl_001", fill1)

        # 第二次成交
        fill2 = RawFillUpdate(
            cl_ord_id="cl_001",
            exec_id="exec_002",
            fill_qty=Decimal("5"),
            fill_price=Decimal("105"),
            exchange_event_ts_ms=2000,
            local_receive_ts_ms=2000,
            source="WS",
        )
        events = cas_apply_fill(vv, shadow, "cl_001", fill2)
        assert len(events) == 1

        # 验证累计持仓
        order = shadow.get_by_cl_ord_id("cl_001")
        assert order.filled_qty == Decimal("10")

    def test_fill_creates_order_if_not_exists(self):
        """测试成交自动创建订单"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        fill = RawFillUpdate(
            cl_ord_id="cl_001",
            exec_id="exec_001",
            fill_qty=Decimal("10"),
            fill_price=Decimal("100"),
            local_receive_ts_ms=1000,
            source="WS",
        )
        events = cas_apply_fill(vv, shadow, "cl_001", fill)
        assert len(events) == 1
        assert shadow.get_by_cl_ord_id("cl_001") is not None


# ==================== DeterministicApplier Tests ====================

class TestDeterministicApplier:
    """DeterministicApplier 的单元测试"""

    def setup_method(self):
        """每个测试前重置"""
        self.applier = DeterministicApplier(partitions=16)

    @pytest.mark.asyncio
    async def test_apply_order_update(self):
        """测试应用订单更新"""
        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        events = await self.applier.apply_order_update(update)
        assert len(events) == 1
        assert events[0].status == "NEW"

    @pytest.mark.asyncio
    async def test_apply_fill_update(self):
        """测试应用成交更新"""
        fill = RawFillUpdate(
            cl_ord_id="cl_001",
            exec_id="exec_001",
            fill_qty=Decimal("10"),
            fill_price=Decimal("100"),
            local_receive_ts_ms=1000,
            source="WS",
        )
        events = await self.applier.apply_fill_update(fill)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_ws_first_rest_late(self):
        """模拟 WS 先到，REST 后到但状态滞后的场景"""
        # WS: FILLED
        ws_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            filled_qty=Decimal("10"),
            exchange_event_ts_ms=3000,
            local_receive_ts_ms=3000,
            source="WS",
        )
        await self.applier.apply_order_update(ws_update)

        # REST: PARTIALLY_FILLED (stale)
        rest_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="PARTIALLY_FILLED",
            filled_qty=Decimal("5"),
            exchange_event_ts_ms=2000,
            local_receive_ts_ms=4000,
            source="REST",
        )
        events = await self.applier.apply_order_update(rest_update)
        assert len(events) == 0

        # 验证最终状态是 FILLED
        order = self.applier.get_shadow_order("cl_001")
        assert order.status == "FILLED"

    @pytest.mark.asyncio
    async def test_no_cl_ord_id_returns_empty(self):
        """测试无 cl_ord_id 返回空"""
        update = RawOrderUpdate(
            broker_order_id="unknown",
            status="NEW",
            local_receive_ts_ms=1000,
            source="WS",
        )
        events = await self.applier.apply_order_update(update)
        assert events == []

    @pytest.mark.asyncio
    async def test_get_shadow_order(self):
        """测试获取影子订单"""
        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            broker_order_id="broker_001",
            status="FILLED",
            filled_qty=Decimal("10"),
            local_receive_ts_ms=1000,
            source="WS",
        )
        await self.applier.apply_order_update(update)

        order = self.applier.get_shadow_order("cl_001")
        assert order is not None
        assert order.status == "FILLED"

    @pytest.mark.asyncio
    async def test_reset(self):
        """测试重置"""
        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            local_receive_ts_ms=1000,
            source="WS",
        )
        await self.applier.apply_order_update(update)
        assert self.applier.get_shadow_order("cl_001") is not None

        self.applier.reset()
        assert self.applier.get_shadow_order("cl_001") is None


# ==================== Concurrency Tests ====================

class TestConcurrency:
    """并发测试"""

    @pytest.mark.asyncio
    async def test_concurrent_updates_same_order(self):
        """测试并发更新同一订单"""
        applier = DeterministicApplier(partitions=16)
        cl_ord_id = "cl_concurrent_001"

        async def update_task(rank: int):
            """更新任务"""
            statuses = ["NEW", "PARTIALLY_FILLED", "FILLED"]
            status = statuses[min(rank, 2)]
            update = RawOrderUpdate(
                cl_ord_id=cl_ord_id,
                status=status,
                exchange_event_ts_ms=1000 + rank * 100,
                local_receive_ts_ms=1000 + rank * 100,
                source="WS",
            )
            await applier.apply_order_update(update)

        # 并发执行 10 个更新
        tasks = [update_task(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # 验证最终状态
        order = applier.get_shadow_order(cl_ord_id)
        assert order is not None
        # 最终状态应该是 FILLED（最高 rank）
        assert order.status == "FILLED"

    @pytest.mark.asyncio
    async def test_concurrent_fills_deduplication(self):
        """测试并发成交去重"""
        applier = DeterministicApplier(partitions=16)

        async def fill_task(exec_id: str):
            """成交任务"""
            fill = RawFillUpdate(
                cl_ord_id="cl_002",
                exec_id=exec_id,
                fill_qty=Decimal("1"),
                fill_price=Decimal("100"),
                local_receive_ts_ms=1000,
                source="WS",
            )
            return await applier.apply_fill_update(fill)

        # 模拟同一个 exec_id 并发发送
        tasks = [fill_task("exec_dup") for _ in range(5)]
        results = await asyncio.gather(*tasks)

        # 只有一次应该成功
        total_events = sum(len(r) for r in results)
        assert total_events == 1

    @pytest.mark.asyncio
    async def test_concurrent_different_orders(self):
        """测试不同订单并发更新"""
        applier = DeterministicApplier(partitions=16)

        async def update_order(cl_ord_id: str):
            """更新订单"""
            update = RawOrderUpdate(
                cl_ord_id=cl_ord_id,
                status="NEW",
                exchange_event_ts_ms=1000,
                local_receive_ts_ms=1000,
                source="WS",
            )
            return await applier.apply_order_update(update)

        # 并发更新不同订单
        tasks = [update_order(f"cl_{i}") for i in range(10)]
        results = await asyncio.gather(*tasks)

        # 所有更新都应该成功
        total_events = sum(len(r) for r in results)
        assert total_events == 10


# ==================== Edge Cases Tests ====================

class TestEdgeCases:
    """边界情况测试"""

    def test_unknown_status_uses_rank_0(self):
        """测试未知状态使用 Rank 0"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="UNKNOWN_STATUS",  # 未知状态
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update)
        # 未知状态应该被接受（Rank 0）
        assert len(events) == 1
        assert vv.last_status_rank == 0

    @pytest.mark.asyncio
    async def test_broker_order_id_mapping(self):
        """测试 broker_order_id 映射"""
        applier = DeterministicApplier()

        # 先创建订单建立映射
        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            broker_order_id="broker_001",
            status="NEW",
            local_receive_ts_ms=500,
            source="WS",
        )
        await applier.apply_order_update(update)

        # 通过 broker_order_id 的成交
        fill = RawFillUpdate(
            broker_order_id="broker_001",
            exec_id="exec_001",
            fill_qty=Decimal("10"),
            fill_price=Decimal("100"),
            local_receive_ts_ms=1000,
            source="WS",
        )
        await applier.apply_fill_update(fill)

        # 验证订单存在（通过 cl_ord_id 查找）
        order = applier.get_shadow_order("cl_001")
        assert order is not None
        assert order.filled_qty == Decimal("10")

    @pytest.mark.asyncio
    async def test_multiple_broker_order_ids(self):
        """测试多个 broker_order_id 映射"""
        applier = DeterministicApplier()

        # 先创建订单建立映射
        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            broker_order_id="broker_001",
            status="NEW",
            local_receive_ts_ms=500,
            source="WS",
        )
        await applier.apply_order_update(update)

        # 第一个成交
        fill1 = RawFillUpdate(
            broker_order_id="broker_001",
            exec_id="exec_001",
            fill_qty=Decimal("5"),
            fill_price=Decimal("100"),
            local_receive_ts_ms=1000,
            source="WS",
        )
        await applier.apply_fill_update(fill1)

        # 第二个成交（同一个 broker_order_id）
        fill2 = RawFillUpdate(
            broker_order_id="broker_001",
            exec_id="exec_002",
            fill_qty=Decimal("5"),
            fill_price=Decimal("105"),
            local_receive_ts_ms=2000,
            source="WS",
        )
        await applier.apply_fill_update(fill2)

        # 验证累计（通过 cl_ord_id 查找）
        order = applier.get_shadow_order("cl_001")
        assert order.filled_qty == Decimal("10")

    def test_exchange_ts_none_handling(self):
        """测试 exchange_ts 为 None 的处理"""
        vv = OrderVersionVector()
        shadow = ShadowState()

        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            exchange_event_ts_ms=None,  # 无交易所时间戳
            local_receive_ts_ms=1000,
            source="WS",
        )
        events = cas_apply_order(vv, shadow, "cl_001", update)
        assert len(events) == 1
        # 应该使用本地时间戳
        assert events[0].exchange_ts_ms == 1000
        assert events[0].ts_inferred is True
