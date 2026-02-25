#!/usr/bin/env python3
"""
简化版验证脚本 - 直接测试核心功能
"""
import sys
sys.path.insert(0, '.')

from trader.core.application.deterministic_layer import (
    DeterministicApplier, RawOrderUpdate, RawFillUpdate,
    OrderVersionVector, ShadowState, TTLSet,
    cas_apply_order, cas_apply_fill,
    STATUS_RANK, TERMINAL_MIN_RANK
)
from decimal import Decimal

def test_ttl_set():
    """测试TTLSet"""
    print("=== Test TTLSet ===")
    ttl = TTLSet(ttl_s=10)
    ttl.add("key1")
    assert "key1" in ttl, "TTL add failed"
    print("TTL add: PASS")

    # 测试不存在
    ttl2 = TTLSet(ttl_s=0)
    ttl2.add("key1")
    assert "key1" not in ttl2, "TTL expiry failed"
    print("TTL expiry: PASS")
    print()

def test_status_rank():
    """测试STATUS_RANK"""
    print("=== Test STATUS_RANK ===")
    assert STATUS_RANK["PENDING"] == 0
    assert STATUS_RANK["NEW"] == 10
    assert STATUS_RANK["FILLED"] == 70
    assert TERMINAL_MIN_RANK == 50
    print("STATUS_RANK: PASS")
    print()

def test_cas_order_normal():
    """测试正常订单生命周期"""
    print("=== Test CAS Order Normal ===")
    vv = OrderVersionVector()
    shadow = ShadowState()

    # NEW
    update1 = RawOrderUpdate(
        cl_ord_id="cl_001",
        status="NEW",
        exchange_event_ts_ms=1000,
        local_receive_ts_ms=1000,
        source="WS"
    )
    events = cas_apply_order(vv, shadow, "cl_001", update1)
    assert len(events) == 1, "NEW should be accepted"
    assert events[0].status == "NEW"
    print("NEW: PASS")

    # PARTIALLY_FILLED
    update2 = RawOrderUpdate(
        cl_ord_id="cl_001",
        status="PARTIALLY_FILLED",
        filled_qty=Decimal("5"),
        exchange_event_ts_ms=2000,
        local_receive_ts_ms=2000,
        source="WS"
    )
    events = cas_apply_order(vv, shadow, "cl_001", update2)
    assert len(events) == 1, "PARTIALLY_FILLED should be accepted"
    print("PARTIALLY_FILLED: PASS")

    # FILLED
    update3 = RawOrderUpdate(
        cl_ord_id="cl_001",
        status="FILLED",
        filled_qty=Decimal("10"),
        exchange_event_ts_ms=3000,
        local_receive_ts_ms=3000,
        source="WS"
    )
    events = cas_apply_order(vv, shadow, "cl_001", update3)
    assert len(events) == 1, "FILLED should be accepted"
    assert vv.last_status_rank == STATUS_RANK["FILLED"]
    print("FILLED: PASS")
    print()

def test_cas_order_rollback():
    """测试订单回滚保护"""
    print("=== Test CAS Order Rollback ===")
    vv = OrderVersionVector()
    shadow = ShadowState()

    # 先设为 FILLED
    update1 = RawOrderUpdate(
        cl_ord_id="cl_001",
        status="FILLED",
        exchange_event_ts_ms=1000,
        local_receive_ts_ms=1000,
        source="WS"
    )
    cas_apply_order(vv, shadow, "cl_001", update1)

    # 尝试回滚到 PARTIALLY_FILLED
    update2 = RawOrderUpdate(
        cl_ord_id="cl_001",
        status="PARTIALLY_FILLED",
        exchange_event_ts_ms=2000,
        local_receive_ts_ms=2000,
        source="WS"
    )
    events = cas_apply_order(vv, shadow, "cl_001", update2)
    assert len(events) == 0, "Rollback should be rejected"
    print("Rollback rejected: PASS")
    print()

def test_cas_fill_dedup():
    """测试成交去重"""
    print("=== Test CAS Fill Deduplication ===")
    vv = OrderVersionVector()
    shadow = ShadowState()

    fill1 = RawFillUpdate(
        cl_ord_id="cl_001",
        exec_id="exec_001",
        fill_qty=Decimal("5"),
        fill_price=Decimal("100"),
        exchange_event_ts_ms=1000,
        local_receive_ts_ms=1000,
        source="WS"
    )
    events1 = cas_apply_fill(vv, shadow, "cl_001", fill1)
    assert len(events1) == 1, "First fill should be accepted"
    print("First fill: PASS")

    # 重复成交
    events2 = cas_apply_fill(vv, shadow, "cl_001", fill1)
    assert len(events2) == 0, "Duplicate fill should be rejected"
    print("Duplicate rejected: PASS")
    print()

def test_finality_override():
    """测试终态override"""
    print("=== Test Finality Override ===")
    vv = OrderVersionVector()
    shadow = ShadowState()

    # WS: PARTIALLY_FILLED
    update1 = RawOrderUpdate(
        cl_ord_id="cl_001",
        status="PARTIALLY_FILLED",
        filled_qty=Decimal("5"),
        exchange_event_ts_ms=1000,
        local_receive_ts_ms=1000,
        source="WS"
    )
    cas_apply_order(vv, shadow, "cl_001", update1)

    # REST: FILLED with finality_override (older timestamp)
    update2 = RawOrderUpdate(
        cl_ord_id="cl_001",
        status="FILLED",
        filled_qty=Decimal("10"),
        exchange_event_ts_ms=800,  # 更早的时间戳
        local_receive_ts_ms=2000,
        source="REST",
        finality_override=True
    )
    events = cas_apply_order(vv, shadow, "cl_001", update2)
    assert len(events) == 1, "Finality override should be accepted"
    print("Finality override: PASS")
    print()

def test_applier():
    """测试DeterministicApplier"""
    print("=== Test DeterministicApplier ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)

        # 订单更新
        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            exchange_event_ts_ms=1000,
            local_receive_ts_ms=1000,
            source="WS"
        )
        events = await applier.apply_order_update(update)
        assert len(events) == 1, "Order update failed"
        print("apply_order_update: PASS")

        # 成交更新
        fill = RawFillUpdate(
            cl_ord_id="cl_001",
            exec_id="exec_001",
            fill_qty=Decimal("10"),
            fill_price=Decimal("100"),
            local_receive_ts_ms=1000,
            source="WS"
        )
        events = await applier.apply_fill_update(fill)
        assert len(events) == 1, "Fill update failed"
        print("apply_fill_update: PASS")

        return applier

    applier = asyncio.run(run_test())

    # 验证状态
    order = applier.get_shadow_order("cl_001")
    assert order is not None
    assert order.status == "NEW"
    assert order.filled_qty == Decimal("10")
    print("get_shadow_order: PASS")
    print()

def test_stale_rest():
    """测试stale REST拒绝"""
    print("=== Test Stale REST Rejection ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)

        # WS: FILLED
        ws_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            filled_qty=Decimal("10"),
            exchange_event_ts_ms=3000,
            local_receive_ts_ms=3000,
            source="WS"
        )
        await applier.apply_order_update(ws_update)

        # REST: PARTIALLY_FILLED (stale, older timestamp)
        rest_update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="PARTIALLY_FILLED",
            filled_qty=Decimal("5"),
            exchange_event_ts_ms=2000,  # 更早
            local_receive_ts_ms=4000,
            source="REST"
        )
        events = await applier.apply_order_update(rest_update)
        assert len(events) == 0, "Stale REST should be rejected"

        # 验证最终状态
        order = applier.get_shadow_order("cl_001")
        assert order.status == "FILLED"
        print("Stale REST rejected: PASS")

    asyncio.run(run_test())
    print()

def test_concurrency():
    """测试并发安全"""
    print("=== Test Concurrency ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)
        cl_ord_id = "cl_concurrent_001"

        async def update_task(rank: int):
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
        print("Concurrent updates: PASS")

    asyncio.run(run_test())
    print()

def test_concurrent_fill_dedup():
    """测试并发成交去重"""
    print("=== Test Concurrent Fill Deduplication ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)

        async def fill_task(exec_id: str):
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
        assert total_events == 1, f"Expected 1 event, got {total_events}"
        print("Concurrent fill dedup: PASS")

    asyncio.run(run_test())
    print()

def test_fill_with_price_update():
    """测试成交价格更新"""
    print("=== Test Fill Price Update ===")
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
        source="WS"
    )
    cas_apply_fill(vv, shadow, "cl_001", fill1)

    # 第二次成交（更新平均价格）
    fill2 = RawFillUpdate(
        cl_ord_id="cl_001",
        exec_id="exec_002",
        fill_qty=Decimal("5"),
        fill_price=Decimal("110"),
        exchange_event_ts_ms=2000,
        local_receive_ts_ms=2000,
        source="WS"
    )
    events = cas_apply_fill(vv, shadow, "cl_001", fill2)
    assert len(events) == 1

    order = shadow.get_by_cl_ord_id("cl_001")
    # 平均价格应该是 105
    assert order.avg_price == Decimal("105"), f"Expected 105, got {order.avg_price}"
    print("Fill price update: PASS")
    print()

def test_get_all_orders():
    """测试获取所有订单"""
    print("=== Test Get All Orders ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)

        # 添加多个订单
        for i in range(3):
            update = RawOrderUpdate(
                cl_ord_id=f"cl_{i}",
                status="NEW",
                local_receive_ts_ms=1000 + i,
                source="WS"
            )
            await applier.apply_order_update(update)

        orders = applier.get_all_orders()
        assert len(orders) == 3, f"Expected 3 orders, got {len(orders)}"
        print("get_all_orders: PASS")

    asyncio.run(run_test())
    print()

def test_get_version_vector():
    """测试获取版本向量"""
    print("=== Test Get Version Vector ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)

        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="FILLED",
            local_receive_ts_ms=1000,
            source="WS"
        )
        await applier.apply_order_update(update)

        vv = applier.get_version_vector("cl_001")
        assert vv is not None
        assert vv.last_status_rank == STATUS_RANK["FILLED"]
        print("get_version_vector: PASS")

    asyncio.run(run_test())
    print()

def test_apply_fill_no_cl_ord_id():
    """测试无cl_ord_id的成交"""
    print("=== Test Apply Fill No cl_ord_id ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)

        # 使用 broker_order_id 但没有映射
        fill = RawFillUpdate(
            broker_order_id="unknown_broker",
            exec_id="exec_001",
            fill_qty=Decimal("10"),
            fill_price=Decimal("100"),
            local_receive_ts_ms=1000,
            source="WS"
        )
        events = await applier.apply_fill_update(fill)
        assert len(events) == 0, "Should return empty for unknown broker"
        print("Apply fill no cl_ord_id: PASS")

    asyncio.run(run_test())
    print()

def test_apply_order_no_cl_ord_id():
    """测试无cl_ord_id的订单"""
    print("=== Test Apply Order No cl_ord_id ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)

        # 使用 broker_order_id 但没有映射
        update = RawOrderUpdate(
            broker_order_id="unknown_broker",
            status="NEW",
            local_receive_ts_ms=1000,
            source="WS"
        )
        events = await applier.apply_order_update(update)
        assert len(events) == 0, "Should return empty for unknown broker"
        print("Apply order no cl_ord_id: PASS")

    asyncio.run(run_test())
    print()

def test_resolve_cl_ord_id_via_broker():
    """测试通过broker_order_id解析cl_ord_id"""
    print("=== Test Resolve cl_ord_id via broker ===")
    from trader.core.application.deterministic_layer import resolve_cl_ord_id
    shadow = ShadowState()

    # 先添加订单建立映射
    shadow.add_order("cl_001", "broker_001")

    # 通过 broker_order_id 解析
    update = RawOrderUpdate(broker_order_id="broker_001", status="NEW")
    cl = resolve_cl_ord_id(update, shadow)
    assert cl == "cl_001", f"Expected cl_001, got {cl}"
    print("Resolve via broker: PASS")
    print()

def test_reset_method():
    """测试重置方法"""
    print("=== Test Reset Method ===")
    import asyncio

    async def run_test():
        applier = DeterministicApplier(partitions=16)

        update = RawOrderUpdate(
            cl_ord_id="cl_001",
            status="NEW",
            local_receive_ts_ms=1000,
            source="WS"
        )
        await applier.apply_order_update(update)
        assert applier.get_shadow_order("cl_001") is not None

        applier.reset()
        assert applier.get_shadow_order("cl_001") is None
        assert len(applier.get_all_orders()) == 0
        print("Reset method: PASS")

    asyncio.run(run_test())
    print()

if __name__ == "__main__":
    print("Running deterministic layer validation tests...\n")

    try:
        test_ttl_set()
        test_status_rank()
        test_cas_order_normal()
        test_cas_order_rollback()
        test_cas_fill_dedup()
        test_finality_override()
        test_applier()
        test_stale_rest()
        test_concurrency()
        test_concurrent_fill_dedup()
        test_fill_with_price_update()
        test_get_all_orders()
        test_get_version_vector()
        test_apply_fill_no_cl_ord_id()
        test_apply_order_no_cl_ord_id()
        test_resolve_cl_ord_id_via_broker()
        test_reset_method()

        print("=" * 50)
        print("ALL TESTS PASSED!")
        print("=" * 50)
    except AssertionError as e:
        print(f"TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
