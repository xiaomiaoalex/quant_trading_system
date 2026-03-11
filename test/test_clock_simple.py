import asyncio

async def test_simple_clock():
    print("Creating FakeClock...")
    from trader.tests.fakes import FakeClock
    clock = FakeClock()
    print(f"Clock created, time: {clock.time}")
    
    print("Scheduling sleep(1.0)...")
    task = asyncio.create_task(clock.sleep(1.0))
    await asyncio.sleep(0.01)
    print(f"Scheduled tasks: {clock.scheduled_count()}")
    
    print("Advancing by 0.5s...")
    result = clock.advance(0.5)
    print(f"Awakened: {len(result)}")
    print(f"Remaining: {clock.scheduled_count()}")
    
    print("Advancing by 0.6s (total 1.1s)...")
    result = clock.advance(0.6)
    print(f"Awakened: {len(result)}")
    print(f"Remaining: {clock.scheduled_count()}")
    
    await asyncio.sleep(0.01)
    print("Done!")

asyncio.run(test_simple_clock())
