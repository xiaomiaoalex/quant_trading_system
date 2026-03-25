#!/usr/bin/env python3
"""
Smoke Test for Binance Announcement Crawler (v2)
================================================
验证真实 Binance API 调用和公告分类逻辑。

v2 调整:
- WS smoke test 改为验证 connect + subscribe + timed receive

Usage:
    python scripts/smoke_test_announcements.py

Note:
    需要网络连接 Binance API。如果 API 不可用，测试会降级处理。
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone

# Add trader to path
sys.path.insert(0, ".")

from trader.adapters.announcements.binance_crawler import (
    BinanceAnnouncementCrawler,
    AnnouncementType,
)
from trader.adapters.announcements.ws_source import BinanceWsAnnouncementSource
from trader.adapters.announcements.html_source import BinanceHtmlAnnouncementSource
from trader.adapters.persistence.event_store import EventStoreWithFallback
from trader.adapters.persistence.memory.event_store import InMemoryEventStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_ws_source():
    """测试 WebSocket Source - v3 调整
    
    验证:
    1. connect - 建立连接
    2. subscribe - 订阅主题
    3. timed receive - 30s 固定时间窗口
    4. 若收到消息则 parse 并打印摘要
    5. 若超时未收到消息但连接稳定，也视为链路基本通过
    6. 只有连接失败、订阅失败、接收循环异常退出才算 failed
    """
    print("\n" + "=" * 60)
    print("WebSocket Source Smoke Test (v3)")
    print("=" * 60)
    
    ws_source = BinanceWsAnnouncementSource()
    
    # 创建 crawler 实例用于符号提取（WS 源本身不提取符号）
    memory_store = InMemoryEventStore()
    event_store = EventStoreWithFallback(
        pg_event_store=None,
        memory_event_store=memory_store,
    )
    crawler = BinanceAnnouncementCrawler(
        event_store=event_store,
        use_ws_primary=True,
    )
    
    connection_ok = False
    subscribe_ok = False
    receive_ok = False
    
    try:
        # 1. 测试连接
        print("\n[1] Testing WebSocket connect...")
        await ws_source.connect()
        print("    Connected to WebSocket")
        connection_ok = True
        
        # 2. 测试订阅
        print("\n[2] Testing subscribe...")
        await ws_source.subscribe()
        print("    Subscribed to com_announcement_en")
        subscribe_ok = True
        
        # 3. 测试定时接收 (30 秒时间窗口)
        print("\n[3] Testing recv_one (30s window)...")
        try:
            ann = await asyncio.wait_for(ws_source.recv_one(), timeout=30.0)
            if ann:
                print(f"    Received announcement:")
                print(f"      Title: {(ann.title or 'N/A')[:60]}")
                if ann.publish_time:
                    print(f"      Date: {ann.publish_time}")
                # symbols 需要通过 crawler 的 _extract_symbols 提取
                if ann.title:
                    symbols = crawler._extract_symbols(ann.title, ann.body or "")
                    if symbols:
                        print(f"      Symbols: {symbols}")
                print("    WS smoke test: PASSED (received data)")
            receive_ok = True
        except asyncio.TimeoutError:
            print("    Timed out after 30s - no announcements received")
            print("    (This is OK if market is closed or no new announcements)")
            # 连接稳定但未收到消息，不应视为完全成功
            # receive_ok 保持 False，因为这是 smoke test
            receive_ok = False
        
    except ConnectionError as e:
        print(f"    WS test FAILED: Connection error: {e}")
        return False
    except Exception as e:
        print(f"    WS test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # 清理
        print("\n[4] Cleanup...")
        await crawler.close()
        await ws_source.disconnect()
        print("    Disconnected")
    
    # 只有 connection_ok/subscribe_ok/receive_ok 都为 True 才返回 True
    return connection_ok and subscribe_ok and receive_ok


async def test_html_source():
    """测试 HTML Source"""
    print("\n" + "=" * 60)
    print("HTML Source Smoke Test")
    print("=" * 60)
    
    html_source = BinanceHtmlAnnouncementSource()
    
    try:
        print("\n[1] Fetching initial announcements from HTML source...")
        announcements = await html_source.fetch_initial(max_results=20)
        print(f"    Fetched {len(announcements)} announcements")
        
        if announcements:
            print("\n[2] Sample announcement:")
            sample = announcements[0]
            print(f"    Title: {(sample.title or 'N/A')[:50]}...")
            print(f"    Source: {sample.source}")
            print(f"    URL: {sample.detail_url}")
        
        print("\nHTML source smoke test: PASSED")
        return True
        
    except Exception as e:
        print(f"    HTML test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        await html_source.close()


async def test_crawler_integration():
    """测试 Crawler 集成"""
    print("\n" + "=" * 60)
    print("Crawler Integration Smoke Test")
    print("=" * 60)
    
    # Create event store (memory mode for testing)
    memory_store = InMemoryEventStore()
    event_store = EventStoreWithFallback(
        pg_event_store=None,
        memory_event_store=memory_store,
    )
    
    # Create crawler
    crawler = BinanceAnnouncementCrawler(
        event_store=event_store,
        poll_interval_seconds=300,
        use_ws_primary=False,  # 使用 HTML 源因为 WS 需要更长测试时间
    )
    
    print("\n[1] Fetching and processing announcements...")
    try:
        processed_count = await crawler.fetch_and_process()
        print(f"    Processed {processed_count} announcements")
        
        # Check if events were written
        events = await event_store.read_stream("announcements")
        print(f"    Events in store: {len(events)}")
        
        if events:
            print("\n[2] Sample event structure:")
            sample_event = events[0]
            print(f"    Event type: {sample_event.event_type}")
            print(f"    Aggregate ID: {sample_event.aggregate_id}")
            print(f"    Data keys: {list(sample_event.data.keys())}")
            
            if "type" in sample_event.data:
                print(f"    Original type: {sample_event.data['type']}")
            
            if "symbols" in sample_event.data:
                print(f"    Symbols: {sample_event.data['symbols']}")
        
        print("\n[3] Testing symbol extraction...")
        sample_titles = [
            "Binance Will List PIXEL and Open Trading Pairs for PIXEL/USDT",
            "Binance Will Launch Pre-Market for JTO and Open JTO/USDT Spot Trading",
            "Binance Will Delist Multiple Trading Pairs",
            "Binance System Maintenance Notice",
        ]
        
        for title in sample_titles:
            symbols = crawler._extract_symbols(title)
            ann_type = crawler._classify_announcement(title)
            print(f"    Title: {title[:40]}...")
            print(f"      -> Type: {ann_type.value}, Symbols: {symbols}")
        
        print("\nCrawler integration smoke test: PASSED")
        return True
        
    except Exception as e:
        print(f"    Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        await crawler.close()


async def main():
    """Smoke Test 主函数"""
    print("=" * 60)
    print("Binance Announcement Crawler Smoke Test (v2)")
    print("=" * 60)
    
    results = {}
    
    # 1. 先测试 HTML 源 (更快更可靠)
    results["html_source"] = await test_html_source()
    
    # 2. 测试 WebSocket 源
    print("\n" + "-" * 60)
    print("Note: WS test may timeout if no new announcements")
    print("-" * 60)
    results["ws_source"] = await test_ws_source()
    
    # 3. 测试 Crawler 集成
    print("\n" + "-" * 60)
    results["crawler_integration"] = await test_crawler_integration()
    
    # 总结
    print("\n" + "=" * 60)
    print("Smoke Test Summary")
    print("=" * 60)
    for test_name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        print(f"  {test_name}: {status}")
    
    all_passed = all(results.values())
    if all_passed:
        print("\nAll smoke tests: PASSED")
    else:
        print("\nSome smoke tests: FAILED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nSmoke Test: INTERRUPTED")
    except Exception as e:
        print(f"\n\nSmoke Test: FAILED with error: {e}")
        import traceback
        traceback.print_exc()
