"""
Replay Runner Tests - 事件重放执行器测试
========================================
测试ReplayRunner的确定性重放、状态重建和错误处理能力。

测试覆盖：
1. 状态机测试：订单状态转换
2. 边界输入测试：空流、单事件、重复事件
3. 错误路径测试：无效事件、缺失字段
4. 集成测试：与DeterministicApplier配合
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Dict, Any

import pytest

from trader.core.application.replay_runner import (
    EventStoreProviderPort,
    StreamEvent,
    ReplayOptions,
    ReplayResult,
    ReplayRunner,
    replay_stream,
    convert_stream_event_to_raw_update,
)
from trader.core.application.deterministic_layer import (
    DeterministicApplier,
    ShadowState,
    ShadowOrder,
    RawOrderUpdate,
    OrderStatus,
)


# ==================== 测试辅助类 ====================

class FakeEventStore:
    """
    内存事件存储（用于测试）
    
    实现EventStoreProviderPort协议。
    """
    
    def __init__(self, events: Optional[List[StreamEvent]] = None):
        self._events: List[StreamEvent] = events or []
    
    def set_events(self, events: List[StreamEvent]) -> None:
        """设置事件列表"""
        self._events = sorted(events, key=lambda e: e.seq)
    
    async def read_stream(
        self,
        stream_key: str,
        from_seq: int = 0,
        limit: int = 1000,
    ) -> List[StreamEvent]:
        """读取事件流
        
        使用 exclusive semantics (seq > from_seq) 与 EventStoreProviderPort 协议
        和 PostgresEventStore 保持一致。
        """
        filtered = [e for e in self._events if e.seq > from_seq]
        return filtered[:limit]
    
    async def get_latest_seq(self, stream_key: str) -> int:
        """获取最新序列号"""
        if not self._events:
            return -1
        return max(e.seq for e in self._events)


# ==================== 创建测试事件的辅助函数 ====================

def make_stream_event(
    seq: int,
    event_type: str,
    aggregate_id: str = "order-1",
    data: Optional[Dict[str, Any]] = None,
    ts_ms: int = 1000000,
) -> StreamEvent:
    """创建测试用StreamEvent"""
    return StreamEvent(
        event_id=f"evt-{seq}",
        stream_key="order-1",
        seq=seq,
        event_type=event_type,
        aggregate_id=aggregate_id,
        aggregate_type="Order",
        timestamp=datetime.now(timezone.utc),
        ts_ms=ts_ms + seq * 1000,
        data=data or {},
        metadata={},
        schema_version=1,
    )


# ==================== 状态机测试 ====================

class TestOrderStateMachine:
    """订单状态转换测试"""
    
    @pytest.mark.asyncio
    async def test_order_created_to_filled(self):
        """测试订单从创建到成交的完整流程"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={
                "client_order_id": "cl-1",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "quantity": "1.0",
            }),
            make_stream_event(1, "ORDER_SUBMITTED", data={
                "client_order_id": "cl-1",
            }),
            make_stream_event(2, "ORDER_FILLED", data={
                "client_order_id": "cl-1",
                "filled_qty": "1.0",
                "avg_price": "50000.0",
            }),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 使用 from_seq=-1 因为协议使用 exclusive semantics (seq > from_seq)
        # from_seq=-1 意味着 seq > -1，即包含所有事件包括 seq=0
        result = await runner.replay_stream(ReplayOptions(
            stream_key="order-1",
            from_seq=-1,
            record_states=True,
        ))
        
        assert result.events_replayed == 3
        assert len(result.errors) == 0
        assert len(result.states) == 3
        
        # 检查最终状态
        final_order = result.final_state.get_by_cl_ord_id("cl-1")
        assert final_order is not None
        assert final_order.status == OrderStatus.FILLED.value
        assert final_order.filled_qty == Decimal("1.0")
        assert final_order.avg_price == Decimal("50000.0")
    
    @pytest.mark.asyncio
    async def test_order_cancelled_flow(self):
        """测试订单取消流程"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-2"}),
            make_stream_event(1, "ORDER_SUBMITTED", data={"client_order_id": "cl-2"}),
            make_stream_event(2, "ORDER_CANCELLED", data={"client_order_id": "cl-2"}),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 使用 from_seq=-1 因为协议使用 exclusive semantics
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1", from_seq=-1))
        
        assert result.events_replayed == 3
        final_order = result.final_state.get_by_cl_ord_id("cl-2")
        assert final_order.status == OrderStatus.CANCELLED.value
    
    @pytest.mark.asyncio
    async def test_order_rejected_flow(self):
        """测试订单拒绝流程"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-3"}),
            make_stream_event(1, "ORDER_REJECTED", data={"client_order_id": "cl-3"}),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 使用 from_seq=-1 因为协议使用 exclusive semantics
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1", from_seq=-1))
        
        assert result.events_replayed == 2
        final_order = result.final_state.get_by_cl_ord_id("cl-3")
        assert final_order.status == OrderStatus.REJECTED.value
    
    @pytest.mark.asyncio
    async def test_order_partially_filled_flow(self):
        """测试订单部分成交流程"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-4"}),
            make_stream_event(1, "ORDER_PARTIALLY_FILLED", data={
                "client_order_id": "cl-4",
                "filled_qty": "0.5",
            }),
            make_stream_event(2, "ORDER_FILLED", data={
                "client_order_id": "cl-4",
                "filled_qty": "1.0",
                "avg_price": "50000.0",
            }),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 使用 from_seq=-1 因为协议使用 exclusive semantics
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1", from_seq=-1))
        
        assert result.events_replayed == 3
        final_order = result.final_state.get_by_cl_ord_id("cl-4")
        assert final_order.status == OrderStatus.FILLED.value
        assert final_order.filled_qty == Decimal("1.0")


# ==================== 边界输入测试 ====================

class TestBoundaryInputs:
    """边界输入测试"""
    
    @pytest.mark.asyncio
    async def test_empty_stream(self):
        """测试空事件流"""
        event_store = FakeEventStore([])
        runner = ReplayRunner(event_store=event_store)
        
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1"))
        
        assert result.events_replayed == 0
        assert result.final_seq == 0
        assert len(result.final_state.orders_by_cl) == 0
    
    @pytest.mark.asyncio
    async def test_single_event(self):
        """测试单个事件"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-single"}),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 使用 from_seq=-1 因为协议使用 exclusive semantics
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1", from_seq=-1))
        
        assert result.events_replayed == 1
        assert "cl-single" in result.final_state.orders_by_cl
    
    @pytest.mark.asyncio
    async def test_replay_from_specific_seq(self):
        """测试从指定序列号开始重放
        
        使用 exclusive semantics (seq > from_seq):
        - from_seq=0 意味着 seq > 0 (跳过 seq=0)
        - from_seq=1 意味着 seq > 1 (跳过 seq=0 和 seq=1)
        """
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-1"}),
            make_stream_event(1, "ORDER_SUBMITTED", data={"client_order_id": "cl-1"}),
            make_stream_event(2, "ORDER_FILLED", data={
                "client_order_id": "cl-1",
                "filled_qty": "1.0",
            }),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 从seq=1开始重放（跳过seq=0，使用 exclusive semantics: seq > 1）
        result = await runner.replay_stream(ReplayOptions(
            stream_key="order-1",
            from_seq=0,  # seq > 0, 所以从 seq=1 开始
        ))
        
        assert result.events_replayed == 2  # seq=1 和 seq=2
        # 但cl-1仍然存在于最终状态，因为状态是从空的开始的
    
    @pytest.mark.asyncio
    async def test_replay_to_specific_seq(self):
        """测试重放到指定序列号
        
        使用 exclusive semantics (seq > from_seq)：
        - from_seq=-1 (seq > -1) 包含所有事件
        - to_seq=1 表示重放到 seq=1（包含）
        """
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-1"}),
            make_stream_event(1, "ORDER_SUBMITTED", data={"client_order_id": "cl-1"}),
            make_stream_event(2, "ORDER_FILLED", data={"client_order_id": "cl-1"}),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 重放到seq=1（包含seq=0和seq=1）
        result = await runner.replay_stream(ReplayOptions(
            stream_key="order-1",
            from_seq=-1,  # seq > -1, 包含所有事件
            to_seq=1,  # 重放到seq=1（包含）
        ))
        
        assert result.events_replayed == 2  # seq=0 和 seq=1
        assert result.final_seq == 1
    
    @pytest.mark.asyncio
    async def test_event_types_filter(self):
        """测试事件类型过滤器"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-1"}),
            make_stream_event(1, "ORDER_SUBMITTED", data={"client_order_id": "cl-1"}),
            make_stream_event(2, "ORDER_FILLED", data={"client_order_id": "cl-1"}),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 只重放FILLED事件
        result = await runner.replay_stream(ReplayOptions(
            stream_key="order-1",
            event_types=["ORDER_FILLED"],
        ))
        
        assert result.events_replayed == 1
    
    @pytest.mark.asyncio
    async def test_limit_option(self):
        """测试最大事件数限制"""
        events = [
            make_stream_event(i, "ORDER_CREATED", data={"client_order_id": f"cl-{i}"})
            for i in range(10)
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        result = await runner.replay_stream(ReplayOptions(
            stream_key="order-1",
            limit=5,
        ))
        
        assert result.events_replayed == 5

    @pytest.mark.asyncio
    async def test_from_seq_exclusive_semantics(self):
        """测试 from_seq 使用 exclusive semantics (seq > from_seq)
        
        验证协议规定的 exclusive semantics:
        - from_seq=0: 返回 seq > 0 的事件 (不包含 seq=0)
        - from_seq=1: 返回 seq > 1 的事件 (不包含 seq=1)
        """
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-boundary"}),
            make_stream_event(1, "ORDER_SUBMITTED", data={"client_order_id": "cl-boundary"}),
            make_stream_event(2, "ORDER_FILLED", data={"client_order_id": "cl-boundary"}),
        ]
        
        # from_seq=0: exclusive - returns seq > 0 (excludes seq=0)
        event_store = FakeEventStore(events)
        result = await event_store.read_stream(
            stream_key="order-1",
            from_seq=0,
        )
        assert len(result) == 2
        assert [e.seq for e in result] == [1, 2]
        
        # from_seq=1: exclusive - returns seq > 1 (excludes seq=0 and seq=1)
        event_store2 = FakeEventStore(events)
        result2 = await event_store2.read_stream(
            stream_key="order-1",
            from_seq=1,
        )
        assert len(result2) == 1
        assert [e.seq for e in result2] == [2]
        
        # from_seq=2: exclusive - returns seq > 2 (excludes all)
        event_store3 = FakeEventStore(events)
        result3 = await event_store3.read_stream(
            stream_key="order-1",
            from_seq=2,
        )
        assert len(result3) == 0


# ==================== 错误路径测试 ====================

class TestErrorPaths:
    """错误路径测试"""
    
    @pytest.mark.asyncio
    async def test_invalid_event_type(self):
        """测试无效事件类型（应该被跳过）"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-1"}),
            make_stream_event(1, "UNKNOWN_EVENT", data={"client_order_id": "cl-1"}),
            make_stream_event(2, "ORDER_FILLED", data={
                "client_order_id": "cl-1",
                "filled_qty": "1.0",
            }),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1"))
        
        # 应该成功重放，不应该因为UNKNOWN_EVENT崩溃
        assert result.events_replayed >= 1
        assert "cl-1" in result.final_state.orders_by_cl
    
    @pytest.mark.asyncio
    async def test_missing_client_order_id(self):
        """测试缺失client_order_id"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={}),  # 没有client_order_id
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 使用 from_seq=-1 因为协议使用 exclusive semantics
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1", from_seq=-1))
        
        # 使用aggregate_id作为fallback
        assert result.events_replayed == 1
    
    @pytest.mark.asyncio
    async def test_event_store_read_error(self):
        """测试事件存储读取错误"""
        class FailingEventStore:
            async def read_stream(self, stream_key, from_seq=0, limit=1000):
                raise RuntimeError("Connection failed")
            
            async def get_latest_seq(self, stream_key):
                return -1
        
        event_store = FailingEventStore()
        runner = ReplayRunner(event_store=event_store)
        
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1"))
        
        assert len(result.errors) == 1
        assert "Connection failed" in result.errors[0]


# ==================== 集成测试 ====================

class TestReplayIntegration:
    """与DeterministicApplier配合的集成测试"""
    
    @pytest.mark.asyncio
    async def test_deterministic_replay(self):
        """测试确定性重放：相同事件序列产生相同状态"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={
                "client_order_id": "cl-det",
                "symbol": "ETHUSDT",
                "side": "BUY",
            }),
            make_stream_event(1, "ORDER_SUBMITTED", data={"client_order_id": "cl-det"}),
            make_stream_event(2, "ORDER_PARTIALLY_FILLED", data={
                "client_order_id": "cl-det",
                "filled_qty": "0.5",
                "avg_price": "3000.0",
            }),
            make_stream_event(3, "ORDER_FILLED", data={
                "client_order_id": "cl-det",
                "filled_qty": "1.0",
                "avg_price": "3000.0",
            }),
        ]
        
        # 第一次重放
        event_store1 = FakeEventStore(events.copy())
        runner1 = ReplayRunner(event_store=event_store1)
        result1 = await runner1.replay_stream(ReplayOptions(stream_key="order-1"))
        
        # 第二次重放
        event_store2 = FakeEventStore(events.copy())
        runner2 = ReplayRunner(event_store=event_store2)
        result2 = await runner2.replay_stream(ReplayOptions(stream_key="order-1"))
        
        # 两次重放结果应该一致
        assert result1.events_replayed == result2.events_replayed
        assert result1.final_seq == result2.final_seq
        
        order1 = result1.final_state.get_by_cl_ord_id("cl-det")
        order2 = result2.final_state.get_by_cl_ord_id("cl-det")
        
        assert order1.status == order2.status
        assert order1.filled_qty == order2.filled_qty
        assert order1.avg_price == order2.avg_price
    
    @pytest.mark.asyncio
    async def test_multiple_orders_replay(self):
        """测试多订单重放"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-A"}),
            make_stream_event(1, "ORDER_CREATED", data={"client_order_id": "cl-B"}),
            make_stream_event(2, "ORDER_FILLED", data={
                "client_order_id": "cl-A",
                "filled_qty": "1.0",
                "avg_price": "50000.0",
            }),
            make_stream_event(3, "ORDER_FILLED", data={
                "client_order_id": "cl-B",
                "filled_qty": "2.0",
                "avg_price": "3000.0",
            }),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 使用 from_seq=-1 因为协议使用 exclusive semantics
        result = await runner.replay_stream(ReplayOptions(stream_key="order-1", from_seq=-1))
        
        assert result.events_replayed == 4
        assert len(result.final_state.orders_by_cl) == 2
        
        order_a = result.final_state.get_by_cl_ord_id("cl-A")
        order_b = result.final_state.get_by_cl_ord_id("cl-B")
        
        assert order_a.status == OrderStatus.FILLED.value
        assert order_a.filled_qty == Decimal("1.0")
        
        assert order_b.status == OrderStatus.FILLED.value
        assert order_b.filled_qty == Decimal("2.0")
    
    @pytest.mark.asyncio
    async def test_replay_stream_convenience_function(self):
        """测试replay_stream便捷函数"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-func"}),
            make_stream_event(1, "ORDER_FILLED", data={
                "client_order_id": "cl-func",
                "filled_qty": "1.0",
            }),
        ]
        
        event_store = FakeEventStore(events)
        
        # 便捷函数使用 from_seq=0 (exclusive semantics), 需要用 from_seq=-1 来包含 seq=0
        result = await replay_stream(
            event_store=event_store,
            stream_key="order-1",
            from_seq=-1,  # 使用 -1 因为协议使用 exclusive semantics
        )
        
        assert result.events_replayed == 2
        assert "cl-func" in result.final_state.orders_by_cl
    
    @pytest.mark.asyncio
    async def test_record_states_option(self):
        """测试record_states选项"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-state"}),
            make_stream_event(1, "ORDER_SUBMITTED", data={"client_order_id": "cl-state"}),
            make_stream_event(2, "ORDER_FILLED", data={"client_order_id": "cl-state"}),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        # 启用状态记录，使用 from_seq=-1 因为协议使用 exclusive semantics
        result_with_states = await runner.replay_stream(ReplayOptions(
            stream_key="order-1",
            from_seq=-1,
            record_states=True,
        ))
        assert len(result_with_states.states) == 3
        
        # 禁用状态记录
        result_without_states = await runner.replay_stream(ReplayOptions(
            stream_key="order-1",
            from_seq=-1,
            record_states=False,
        ))
        assert len(result_without_states.states) == 0


# ==================== 辅助函数测试 ====================

class TestConvertStreamEvent:
    """convert_stream_event_to_raw_update测试"""
    
    def test_convert_order_filled_event(self):
        """测试转换ORDER_FILLED事件"""
        event = make_stream_event(1, "ORDER_FILLED", data={
            "client_order_id": "cl-test",
            "filled_qty": "2.5",
            "avg_price": "100.5",
        })
        
        raw_update = convert_stream_event_to_raw_update(event)
        
        assert raw_update is not None
        assert raw_update.cl_ord_id == "cl-test"
        assert raw_update.status == OrderStatus.FILLED.value
        assert raw_update.filled_qty == Decimal("2.5")
        assert raw_update.avg_price == Decimal("100.5")
        assert raw_update.source == "REPLAY"
    
    def test_convert_with_string_decimal(self):
        """测试字符串形式的Decimal"""
        event = make_stream_event(1, "ORDER_FILLED", data={
            "client_order_id": "cl-test",
            "filled_qty": "1.234",
            "avg_price": "999.99",
        })
        
        raw_update = convert_stream_event_to_raw_update(event)
        
        assert raw_update.filled_qty == Decimal("1.234")
        assert raw_update.avg_price == Decimal("999.99")
    
    def test_convert_with_numeric_decimal(self):
        """测试数值形式的Decimal"""
        event = make_stream_event(1, "ORDER_FILLED", data={
            "client_order_id": "cl-test",
            "filled_qty": 1.5,
            "avg_price": 2500.0,
        })
        
        raw_update = convert_stream_event_to_raw_update(event)
        
        assert raw_update.filled_qty == Decimal("1.5")
        assert raw_update.avg_price == Decimal("2500.0")


# ==================== ShadowState快照测试 ====================

class TestShadowStateSnapshot:
    """ShadowState深拷贝测试"""
    
    @pytest.mark.asyncio
    async def test_state_immutability(self):
        """测试状态不可变性（快照应该是独立的）"""
        events = [
            make_stream_event(0, "ORDER_CREATED", data={"client_order_id": "cl-immutable"}),
            make_stream_event(1, "ORDER_FILLED", data={
                "client_order_id": "cl-immutable",
                "filled_qty": "1.0",
            }),
        ]
        
        event_store = FakeEventStore(events)
        runner = ReplayRunner(event_store=event_store)
        
        result = await runner.replay_stream(ReplayOptions(
            stream_key="order-1",
            record_states=True,
        ))
        
        # 修改中间状态不应该影响最终状态
        if len(result.states) > 0:
            result.states[0].orders_by_cl["cl-immutable"].filled_qty = Decimal("999.0")
        
        final_order = result.final_state.get_by_cl_ord_id("cl-immutable")
        assert final_order.filled_qty == Decimal("1.0")  # 应该是原始值，不是999
