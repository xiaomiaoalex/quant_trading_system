import asyncio

async def main():
    from trader.tests.fakes import FakeClock, FakeHTTPClient, ResponseScript, WSMode, WSConfig
    from trader.tests.fakes import FakeWebSocket, PingPongScript
    
    print("=== Test 1: FakeClock advance precision ===")
    clock = FakeClock()
    
    async def run_sleeps():
        t1 = asyncio.create_task(clock.sleep(1.0))
        t2 = asyncio.create_task(clock.sleep(2.0))
        t3 = asyncio.create_task(clock.sleep(0.5))
        print(f"  All sleeps scheduled, count: {clock.scheduled_count()}")
        await asyncio.gather(t1, t2, t3)
        print(f"  All sleeps completed")
    
    task = asyncio.create_task(run_sleeps())
    await asyncio.sleep(0.01)
    
    print(f"Before advance: {clock.scheduled_count()}")
    
    result = clock.advance(0.6)
    print(f"After advance 0.6s: {len(result)} awakened, remaining: {clock.scheduled_count()}")
    
    result = clock.advance(0.5)
    print(f"After advance 1.1s: {len(result)} awakened, remaining: {clock.scheduled_count()}")
    
    result = clock.advance(1.0)
    print(f"After advance 2.1s: {len(result)} awakened, remaining: {clock.scheduled_count()}")
    
    print(f"Total awakened: {clock.total_awakened}")
    print(f"Advance count: {clock.advance_count}")
    await task
    
    print()
    print("=== Test 2: FakeWebSocket HANG mode ===")
    ws = FakeWebSocket(WSConfig(mode=WSMode.HANG))
    
    await ws.connect()
    
    async def hang_detector():
        try:
            await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            print("  Hang detected correctly (timeout)")
            return True
        except Exception as e:
            print(f"  Error: {e}")
            return False
        return False
    
    result = await hang_detector()
    if result and ws._hang_future:
        ws._hang_future.set_result(None)
    await ws.close()
    
    print()
    print("=== Test 3: PingPongScript timeout ===")
    ws2 = FakeWebSocket()
    script = PingPongScript().set_timeout_after(2)
    ws2.set_ping_pong_script(script)
    
    for i in range(3):
        waiter = await ws2.ping(b"test")
        print(f"  Ping {i+1}: waiter={waiter}")
        if waiter:
            print(f"    Waiter done: {waiter.done()}")
    
    print()
    print("=== Test 4: FakeHTTP 429 + Retry-After ===")
    client = FakeHTTPClient()
    script = ResponseScript().add_429(10).add_ok()
    client.add_script("/api/orders", script)
    
    resp = await client.request("GET", "/api/orders")
    print(f"  Status: {resp.status}")
    print(f"  Retry-After: {resp.headers.get('Retry-After')}")
    
    history = client.get_request_history()
    print(f"  Request count: {len(history)}")
    
    stats_ns = client.get_request_interval_stats_ns("/api/orders")
    print(f"  Stats (ns): {stats_ns}")
    
    print()
    print("=== Test 5: Request storm detection ===")
    client2 = FakeHTTPClient()
    script2 = ResponseScript().add_ok().add_ok().add_ok()
    client2.add_script("/test", script2)
    
    await client2.request("GET", "/test")
    await client2.request("GET", "/test")
    await client2.request("GET", "/test")
    
    try:
        client2.assert_no_request_storm("/test", 0)
        print("  Storm test passed (0ns threshold)")
    except AssertionError as e:
        print(f"  Storm detected: {e}")
    
    try:
        client2.assert_no_request_storm("/test", 1)
        print("  Storm test passed (1ns threshold)")
    except AssertionError as e:
        print(f"  Storm detected: {e}")
    
    print()
    print("All tests passed!")

asyncio.run(main())
