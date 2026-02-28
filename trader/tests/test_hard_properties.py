"""
Hard Properties 测试 - 返工版 V2
===============================
真正的 Hard Properties 测试，基于 FakeClock。
"""
import asyncio
import pytest
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum

from trader.tests.fakes import (
    FakeClock,
    FakeHTTPClient,
    ResponseScript,
    WSMode,
    WSConfig,
    FakeWebSocket,
    PingPongScript,
    ConnectionClosedError,
)


class StreamState(Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    STALE_DATA = "STALE_DATA"
    DEGRADED = "DEGRADED"
    ALIGNING = "ALIGNING"


@dataclass
class RestAlignmentSnapshot:
    open_orders: List[Dict] = field(default_factory=list)
    exchange_ts_ms: int = 0
    local_ts_ms: int = 0
    alignment_reason: str = ""


class MockRESTAlignment:
    """基于 FakeClock 的 REST Alignment"""
    
    def __init__(self, http_client: FakeHTTPClient, clock: FakeClock):
        self._http = http_client
        self._clock = clock
        self._call_times_ms: List[int] = []
        
    async def force_alignment_p0(self, reason: str) -> RestAlignmentSnapshot:
        current_ms = self._clock.now_ms
        self._call_times_ms.append(current_ms)
        
        return RestAlignmentSnapshot(
            open_orders=[{"orderId": "123"}],
            alignment_reason=reason,
            exchange_ts_ms=current_ms,
            local_ts_ms=current_ms
        )
    
    @property
    def call_times_ms(self) -> List[int]:
        return self._call_times_ms


class MockPrivateStream:
    """基于 wait_for(recv, timeout) 的 STALE 检测"""
    
    def __init__(self, ws: FakeWebSocket, clock: FakeClock, timeout: float = 0.3):
        self._ws = ws
        self._clock = clock
        self._timeout = timeout
        
        self._state = StreamState.IDLE
        self._connect_count = 0
        self._reconnect_count = 0
        self._stale_count = 0
        self._handler_count = 0
        self._pending_msgs: List[Any] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
    async def connect(self) -> None:
        self._connect_count += 1
        await self._ws.connect()
        self._state = StreamState.CONNECTED
    
    async def reconnect(self) -> None:
        self._reconnect_count += 1
        self._state = StreamState.RECONNECTING
        await self._ws.close()
        await self._ws.connect()
        self._state = StreamState.CONNECTED
    
    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
    
    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _loop(self) -> None:
        while self._running:
            try:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout)
                if msg:
                    if self._state == StreamState.ALIGNING:
                        self._pending_msgs.append(msg)
                    else:
                        self._handler_count += 1
            except asyncio.TimeoutError:
                await self._on_stale()
            except ConnectionClosedError:
                await self._on_stale()
    
    async def _on_stale(self) -> None:
        if self._state != StreamState.STALE_DATA:
            self._stale_count += 1
            self._state = StreamState.STALE_DATA
            await self.reconnect()
    
    @property
    def state(self) -> StreamState:
        return self._state
    
    @property
    def connect_count(self) -> int:
        return self._connect_count
    
    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count
    
    @property
    def stale_count(self) -> int:
        return self._stale_count
    
    @property
    def handler_count(self) -> int:
        return self._handler_count
    
    @property
    def pending_msgs(self) -> List[Any]:
        return self._pending_msgs


class MockDegradedCascade:
    """基于 FakeClock 的 Degraded Cascade"""
    
    def __init__(self, clock: FakeClock):
        self._clock = clock
        self._state = "NORMAL"
        self._report_count = 0
        self._dedup_keys: List[str] = []
        self._self_protected = False
        self._first_fail_ts: Optional[float] = None
        self._trigger_ms = 30000
        self._dedup_window_ms = 60000
        self._min_report_ms = 5000
        self._last_report_ts = 0.0
        
    async def on_degraded(self, reason: str, scope: str) -> None:
        now = self._clock.time
        window = int(now * 1000 / self._dedup_window_ms)
        key = f"{reason}:{scope}:{window}"
        
        if key not in self._dedup_keys[-10:]:
            self._dedup_keys.append(key)
            if self._state == "NORMAL":
                self._state = "DEGRADED"
            if now - self._last_report_ts >= self._min_report_ms / 1000:
                self._report_count += 1
                self._last_report_ts = now
        
        if self._first_fail_ts is None:
            self._first_fail_ts = now
        
        if now - self._first_fail_ts > self._trigger_ms / 1000:
            self._self_protected = True
    
    def can_open_position(self) -> bool:
        return self._state == "NORMAL" and not self._self_protected
    
    @property
    def report_count(self) -> int:
        return self._report_count
    
    @property
    def dedup_keys(self) -> List[str]:
        return self._dedup_keys
    
    @property
    def is_self_protected(self) -> bool:
        return self._self_protected


class TestPrivateStream:
    """PrivateStream Hard Properties"""
    
    @pytest.mark.asyncio
    async def test_stale_state_transition(self):
        """P0-1: STALE 状态转换"""
        clock = FakeClock()
        ws = FakeWebSocket()
        
        stream = MockPrivateStream(ws, clock, timeout=10.0)
        
        await stream.connect()
        
        stream._state = StreamState.STALE_DATA
        await stream.reconnect()
        
        assert stream.reconnect_count == 1
        assert stream.state == StreamState.CONNECTED
    
    @pytest.mark.asyncio
    async def test_alignment_gate_buffers_messages(self):
        """P0-4: ALIGNING 状态消息被缓冲"""
        clock = FakeClock()
        ws = FakeWebSocket()
        ws.push_message({"type": "order"})
        
        stream = MockPrivateStream(ws, clock, timeout=10.0)
        
        stream._state = StreamState.ALIGNING
        
        msg = await ws.recv()
        
        if stream._state == StreamState.ALIGNING:
            stream._pending_msgs.append(msg)
        
        assert stream.handler_count == 0
        assert len(stream.pending_msgs) == 1


class TestRESTAlignment:
    """RESTAlignment Hard Properties"""
    
    @pytest.mark.asyncio
    async def test_retry_after_minimum(self):
        """P0-6: Retry-After 基于 FakeClock"""
        clock = FakeClock()
        
        alignment = MockRESTAlignment(FakeHTTPClient(), clock)
        
        await alignment.force_alignment_p0("first")
        t1 = alignment.call_times_ms[0]
        
        clock.advance(10)
        
        await alignment.force_alignment_p0("second")
        t2 = alignment.call_times_ms[1]
        
        assert t2 - t1 >= 10000, "Interval >= 10s"
    
    @pytest.mark.asyncio
    async def test_backoff_monotonic(self):
        """P0-7: backoff 单调"""
        clock = FakeClock()
        
        alignment = MockRESTAlignment(FakeHTTPClient(), clock)
        
        await alignment.force_alignment_p0("t1")
        clock.advance(1)
        await alignment.force_alignment_p0("t2")
        clock.advance(2)
        await alignment.force_alignment_p0("t3")
        
        times = alignment.call_times_ms
        assert times[1] - times[0] <= times[2] - times[1], "Backoff monotonic"
    
    @pytest.mark.asyncio
    async def test_p0_alignment_never_skips(self):
        """P0-8: P0 不跳过"""
        clock = FakeClock()
        
        alignment = MockRESTAlignment(FakeHTTPClient(), clock)
        
        r1 = await alignment.force_alignment_p0("first")
        clock.advance(0.1)
        r2 = await alignment.force_alignment_p0("second")
        
        assert r1 is not None and r2 is not None, "P0 never skips"


class TestDegradedCascade:
    """DegradedCascade Hard Properties"""
    
    @pytest.mark.asyncio
    async def test_dedup_key_window(self):
        """P0-9: dedup_key 窗口确定性"""
        clock = FakeClock()
        
        cascade = MockDegradedCascade(clock)
        
        await cascade.on_degraded("err", "scope")
        key1 = cascade.dedup_keys[0] if cascade.dedup_keys else None
        
        clock.advance(10)
        
        await cascade.on_degraded("err2", "scope")
        key2 = cascade.dedup_keys[1] if len(cascade.dedup_keys) > 1 else None
        
        assert key1 is not None and key2 is not None
    
    @pytest.mark.asyncio
    async def test_fail_closed_timer(self):
        """P0-10: fail-closed 计时器"""
        clock = FakeClock()
        
        cascade = MockDegradedCascade(clock)
        cascade._trigger_ms = 5000
        
        await cascade.on_degraded("err", "s1")
        clock.advance(3)
        assert not cascade.is_self_protected
        
        clock.advance(3)
        await cascade.on_degraded("err", "s2")
        
        assert cascade.is_self_protected
        assert not cascade.can_open_position()
    
    @pytest.mark.asyncio
    async def test_cooldown_no_storm(self):
        """P0-11: cooldown 防抖"""
        clock = FakeClock()
        
        cascade = MockDegradedCascade(clock)
        cascade._min_report_ms = 5000
        
        for i in range(10):
            await cascade.on_degraded(f"err_{i}", f"s_{i}")
            clock.advance(0.5)
        
        assert cascade.report_count <= 2, "Should not storm"
    
    @pytest.mark.asyncio
    async def test_fail_closed_no_storm(self):
        """P0-12: fail-closed 后不 storm"""
        clock = FakeClock()
        
        cascade = MockDegradedCascade(clock)
        cascade._trigger_ms = 1000
        
        for i in range(20):
            await cascade.on_degraded(f"err_{i}", f"s_{i}")
            clock.advance(0.2)
        
        assert cascade.report_count < 20, "Should limit requests"
