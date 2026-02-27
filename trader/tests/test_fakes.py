"""
Fakes 组件 pytest 测试
=====================
测试 FakeClock, FakeWebSocket, FakeHTTPClient 的核心功能。
"""
import asyncio
import pytest
from trader.tests.fakes import (
    FakeClock,
    ClockContext,
    FakeHTTPClient,
    ResponseScript,
    WSMode,
    WSConfig,
    FakeWebSocket,
    PingPongScript,
    ConnectionClosedError,
)


class TestFakeClock:
    """FakeClock 测试"""
    
    @pytest.mark.asyncio
    async def test_sleep_scheduling(self):
        """测试 sleep 任务调度"""
        clock = FakeClock()
        
        t1 = asyncio.create_task(clock.sleep(1.0))
        t2 = asyncio.create_task(clock.sleep(2.0))
        t3 = asyncio.create_task(clock.sleep(0.5))
        
        await asyncio.sleep(0.01)
        
        assert clock.scheduled_count() == 3
        
        await asyncio.gather(t1, t2, t3)
    
    @pytest.mark.asyncio
    async def test_advance_sequential_wake(self):
        """测试 advance 按顺序唤醒"""
        clock = FakeClock()
        
        t1 = asyncio.create_task(clock.sleep(1.0))
        t2 = asyncio.create_task(clock.sleep(2.0))
        t3 = asyncio.create_task(clock.sleep(0.5))
        
        await asyncio.sleep(0.01)
        
        result = clock.advance(0.6)
        assert len(result) == 1
        assert result[0]["sleep_duration"] == 0.5
        
        result = clock.advance(0.5)
        assert len(result) == 1
        assert result[0]["sleep_duration"] == 1.0
        
        result = clock.advance(1.0)
        assert len(result) == 1
        assert result[0]["sleep_duration"] == 2.0
        
        assert clock.total_awakened == 3
        assert clock.advance_count == 3
        
        await asyncio.gather(t1, t2, t3)
    
    @pytest.mark.asyncio
    async def test_clock_context(self):
        """测试 ClockContext 劫持"""
        clock = FakeClock(start_time=1000.0)
        
        async with ClockContext(clock):
            import time
            assert clock.time == 1000.0
            
            await asyncio.sleep(1.0)
            assert clock.scheduled_count() == 1
            
            clock.advance(1.5)
            assert clock.scheduled_count() == 0


class TestFakeWebSocket:
    """FakeWebSocket 测试"""
    
    @pytest.mark.asyncio
    async def test_hang_mode(self):
        """测试 HANG 模式（默认 raises）"""
        ws = FakeWebSocket(WSConfig(mode=WSMode.HANG))
        await ws.connect()
        
        async def try_recv():
            try:
                await asyncio.wait_for(ws.recv(), timeout=0.1)
                return False
            except asyncio.TimeoutError:
                return True
        
        result = await try_recv()
        assert result, "Hang should trigger timeout"
        
        ws.release_hang()
        
        with pytest.raises(ConnectionClosedError):
            await ws.recv()
        
        await ws.close()
    
    @pytest.mark.asyncio
    async def test_hang_mode_no_raise(self):
        """测试 HANG 模式不抛错（hang_raises_on_release=False）"""
        ws = FakeWebSocket(WSConfig(mode=WSMode.HANG, hang_raises_on_release=False))
        await ws.connect()
        
        recv_task = asyncio.create_task(ws.recv())
        await asyncio.sleep(0.01)
        
        ws.release_hang()
        
        result = await recv_task
        assert result is None, "Should return None when hang_raises_on_release=False"
    
    @pytest.mark.asyncio
    async def test_hang_future_reuse(self):
        """测试 HANG future 复用（不被覆盖）"""
        ws = FakeWebSocket(WSConfig(mode=WSMode.HANG))
        await ws.connect()
        
        recv_task1 = asyncio.create_task(ws.recv())
        await asyncio.sleep(0.01)
        
        recv_task2 = asyncio.create_task(ws.recv())
        await asyncio.sleep(0.01)
        
        ws.release_hang()
        
        try:
            await asyncio.wait_for(recv_task1, timeout=0.1)
        except Exception:
            pass
        
        try:
            await asyncio.wait_for(recv_task2, timeout=0.1)
        except Exception:
            pass
        
        assert ws._hang_future is not None
    
    @pytest.mark.asyncio
    async def test_ping_returns_waiter(self):
        """测试 ping 返回可 await 的 waiter（非 async）"""
        ws = FakeWebSocket()
        
        waiter = ws.ping(b"test")
        assert waiter.done(), "Normal ping should return completed future"
    
    @pytest.mark.asyncio
    async def test_ping_timeout(self):
        """测试 ping 超时"""
        ws = FakeWebSocket()
        script = PingPongScript().set_timeout_after(2)
        ws.set_ping_pong_script(script)
        
        waiter1 = ws.ping(b"test1")
        assert waiter1.done(), "First ping should complete (before threshold)"
        
        waiter2 = ws.ping(b"test2")
        assert not waiter2.done(), "Second ping should timeout (Nth)"
    
    @pytest.mark.asyncio
    async def test_ping_delay(self):
        """测试 ping 延迟"""
        ws = FakeWebSocket()
        script = PingPongScript().set_delay(0.1)
        ws.set_ping_pong_script(script)
        
        waiter = ws.ping(b"test")
        assert not waiter.done(), "Delay should make waiter pending"
        
        await asyncio.sleep(0.2)
        assert waiter.done(), "After delay, waiter should complete"
    
    @pytest.mark.asyncio
    async def test_pong_triggers_waiter(self):
        """测试 pong 触发 waiter"""
        ws = FakeWebSocket()
        script = PingPongScript().set_timeout_after(1)
        ws.set_ping_pong_script(script)
        
        waiter = ws.ping(b"test")
        assert not waiter.done()
        
        await ws.pong(b"pong")
        assert waiter.done(), "Pong should complete waiter"
    
    @pytest.mark.asyncio
    async def test_message_queue(self):
        """测试消息队列"""
        ws = FakeWebSocket()
        ws.push_message("msg1")
        ws.push_message("msg2")
        
        await ws.connect()
        
        msg1 = await ws.recv()
        assert msg1 == "msg1"
        
        msg2 = await ws.recv()
        assert msg2 == "msg2"


class TestFakeHTTPClient:
    """FakeHTTPClient 测试"""
    
    @pytest.mark.asyncio
    async def test_429_retry_after(self):
        """测试 429 + Retry-After"""
        client = FakeHTTPClient()
        script = ResponseScript().add_429(10).add_ok()
        client.add_script("/api/orders", script)
        
        resp = await client.request("GET", "/api/orders")
        
        assert resp.status == 429
        assert resp.headers.get("Retry-After") == "10"
    
    @pytest.mark.asyncio
    async def test_request_history(self):
        """测试请求历史记录"""
        client = FakeHTTPClient()
        script = ResponseScript().add_ok().add_ok()
        client.add_script("/test", script)
        
        await client.request("GET", "/test")
        await client.request("GET", "/test")
        
        history = client.get_request_history()
        assert len(history) == 2
        assert all(r.method == "GET" for r in history)
    
    @pytest.mark.asyncio
    async def test_no_request_storm(self):
        """测试请求风暴检测"""
        client = FakeHTTPClient()
        script = ResponseScript().add_ok().add_ok().add_ok()
        client.add_script("/test", script)
        
        await client.request("GET", "/test")
        await client.request("GET", "/test")
        await client.request("GET", "/test")
        
        assert client.assert_no_request_storm("/test", 0)
        
        with pytest.raises(AssertionError, match="Request storm detected"):
            client.assert_no_request_storm("/test", 1_000_000_000)
    
    @pytest.mark.asyncio
    async def test_inject_now_fn(self):
        """测试注入 now_fn"""
        times = [1000.0, 1001.0, 1002.0]
        
        def mock_now():
            return times.pop(0)
        
        client = FakeHTTPClient(now_fn=mock_now)
        
        await client.request("GET", "/test")
        history = client.get_request_history()
        
        assert history[0].timestamp == 1000.0


class TestPingPongScript:
    """PingPongScript 测试"""
    
    def test_timeout_after_n(self):
        """测试第 N 次超时"""
        script = PingPongScript().set_timeout_after(2)
        
        assert not script.should_timeout(), "1st call: 1 < 2, should not timeout"
        assert script.should_timeout(), "2nd call: 2 >= 2, should timeout"
    
    def test_delay(self):
        """测试延迟"""
        script = PingPongScript().set_delay(1.5)
        
        assert script.get_delay() == 1.5
    
    def test_error(self):
        """测试错误"""
        script = PingPongScript().set_error(ValueError("test"))
        
        assert isinstance(script.get_error(), ValueError)
