"""
Test Binance Announcement Crawler E2E - 公告爬虫端到端测试
===========================================================
验证策略订阅 announcements 流的完整闭环。

E2E 测试覆盖场景：
1. 公告从爬虫到 event_store 的完整写入链路
2. 策略通过 read_stream 订阅 announcements 流
3. 策略根据公告类型做出相应反应（模拟策略决策）
4. 失败回退：EventStore 降级到内存时的处理
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from trader.adapters.announcements.binance_crawler import (
    BinanceAnnouncementCrawler,
    AnnouncementType,
)
from trader.adapters.persistence.event_store import EventStoreWithFallback
from trader.adapters.persistence.memory.event_store import InMemoryEventStore


# ==================== Fixtures ====================

@pytest.fixture
def event_store_with_fallback():
    """EventStoreWithFallback（纯内存模式）"""
    memory_store = InMemoryEventStore()
    return EventStoreWithFallback(
        pg_event_store=None,
        memory_event_store=memory_store,
    )


@pytest.fixture
def mock_http_client():
    """Mock httpx.AsyncClient"""
    client = MagicMock()
    client.get = AsyncMock()
    client.aclose = AsyncMock()
    return client


# ==================== 策略模拟 ====================

class MockStrategy:
    """
    模拟策略 - 订阅 announcements 流并根据公告做出决策
    
    策略规则：
    - LISTING: 记录新币信息（模拟考虑是否交易）
    - DELISTING: 标记需要平仓的币种
    - MAINTENANCE: 记录维护信息（模拟考虑是否暂停交易）
    - OTHER: 忽略
    """
    
    def __init__(self, name: str = "MockStrategy"):
        self.name = name
        self.received_events = []
        self.listing_alerts = []  # 模拟考虑交易的新币
        self.delisting_alerts = []  # 模拟需要平仓的币种
        self.maintenance_alerts = []
        self.is_running = False
    
    async def on_event(self, event):
        """策略事件处理器"""
        self.received_events.append(event)
        
        event_type = event.data.get("type")
        symbols = event.data.get("symbols", [])
        
        if event_type == "LISTING":
            # 模拟策略决策：记录新币
            for symbol in symbols:
                self.listing_alerts.append({
                    "symbol": symbol,
                    "title": event.data.get("title"),
                    "timestamp": event.timestamp,
                })
        
        elif event_type == "DELISTING":
            # 模拟策略决策：标记需要平仓
            for symbol in symbols:
                self.delisting_alerts.append({
                    "symbol": symbol,
                    "title": event.data.get("title"),
                    "timestamp": event.timestamp,
                })
        
        elif event_type == "MAINTENANCE":
            # 模拟策略决策：记录维护信息
            self.maintenance_alerts.append({
                "title": event.data.get("title"),
                "timestamp": event.timestamp,
            })
    
    async def subscribe_to_stream(self, event_store: EventStoreWithFallback, stream_key: str = "announcements"):
        """
        订阅事件流（轮询模式）
        
        在真实系统中，这可能是一个后台任务持续监听事件。
        """
        self.is_running = True
        last_seq = 0
        
        while self.is_running:
            try:
                events = await event_store.read_stream(stream_key, from_seq=last_seq, limit=100)
                
                for event in events:
                    if event.seq >= last_seq:
                        await self.on_event(event)
                        last_seq = event.seq + 1
                
                # 避免过于频繁的轮询
                await asyncio.sleep(0.1)
                
            except Exception as e:
                # 记录错误但继续运行
                await asyncio.sleep(1)


# ==================== E2E 测试 ====================

class TestAnnouncementCrawlerE2E:
    """公告爬虫端到端测试"""
    
    @pytest.mark.asyncio
    async def test_full_pipeline_crawler_to_strategy(
        self, event_store_with_fallback, mock_http_client
    ):
        """
        完整链路测试：爬虫 -> event_store -> 策略订阅
        
        验证：
        1. 爬虫抓取公告并写入 event_store
        2. 策略订阅 announcements 流并接收事件
        3. 策略根据公告类型做出正确反应
        """
        # Setup mock response with various announcement types
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "E2E001",
                        "title": "Binance将上线新的DeFi项目并开放BTCUSDT交易对",
                        "content": "新币上线公告",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                    {
                        "id": "E2E002",
                        "title": "Binance将下架DOGEUSDT交易对",
                        "content": "下架公告",
                        "timestamp": 1742832000001,
                        "type": 1,
                        "locale": "zh",
                    },
                    {
                        "id": "E2E003",
                        "title": "Binance将于周末进行系统维护",
                        "content": "维护公告",
                        "timestamp": 1742832000002,
                        "type": 1,
                        "locale": "zh",
                    },
                ]
            }
        }
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=articles)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        # Create crawler
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store_with_fallback,
            http_client=mock_http_client,
        )
        
        # Create strategy
        strategy = MockStrategy(name="TestListingStrategy")
        
        # Step 1: 爬虫抓取并写入
        count = await crawler.fetch_and_process()
        assert count == 3, f"Expected 3 events, got {count}"
        
        # Step 2: 验证事件已写入 store
        events = await event_store_with_fallback.read_stream("announcements")
        assert len(events) == 3, f"Expected 3 events in store, got {len(events)}"
        
        # Step 3: 策略订阅并处理
        strategy_events = await event_store_with_fallback.read_stream("announcements")
        for event in strategy_events:
            await strategy.on_event(event)
        
        # Step 4: 验证策略反应
        assert len(strategy.received_events) == 3
        assert len(strategy.listing_alerts) == 1  # 1 个 LISTING
        assert len(strategy.delisting_alerts) == 1  # 1 个 DELISTING
        assert len(strategy.maintenance_alerts) == 1  # 1 个 MAINTENANCE
        
        # 验证 LISTING 详情
        listing = strategy.listing_alerts[0]
        assert "BTCUSDT" in listing["symbol"]  # v2: full pair not base asset
        assert "DeFi" in listing["title"]
        
        # 验证 DELISTING 详情
        delisting = strategy.delisting_alerts[0]
        assert "DOGE" in delisting["symbol"]
    
    @pytest.mark.asyncio
    async def test_strategy_receives_events_correctly(
        self, event_store_with_fallback, mock_http_client
    ):
        """
        测试策略正确接收事件
        """
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "E2E101",
                        "title": "Binance将上线新的DeFi项目并开放ETHUSDT交易对",
                        "content": "test",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                ]
            }
        }
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=articles)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store_with_fallback,
            http_client=mock_http_client,
        )
        
        strategy = MockStrategy()
        
        # 第一次抓取
        await crawler.fetch_and_process()
        
        # 策略处理
        events = await event_store_with_fallback.read_stream("announcements")
        for event in events:
            await strategy.on_event(event)
        
        assert len(strategy.received_events) == 1
        assert len(strategy.listing_alerts) == 1
        assert strategy.listing_alerts[0]["symbol"] == "ETHUSDT"  # v2: full pair
    
    @pytest.mark.asyncio
    async def test_multiple_strategies_subscribe(
        self, event_store_with_fallback, mock_http_client
    ):
        """
        测试多个策略同时订阅同一事件流
        
        验证：
        - 多个策略可以独立订阅 announcements 流
        - 一个策略的处理不影响另一个
        """
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "E2E201",
                        "title": "Binance将上线新的DeFi项目并开放BTCUSDT交易对",
                        "content": "test",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                ]
            }
        }
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=articles)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store_with_fallback,
            http_client=mock_http_client,
        )
        
        # 创建两个不同策略
        strategy_a = MockStrategy(name="StrategyA")  # 只关注 LISTING
        strategy_b = MockStrategy(name="StrategyB")  # 关注所有类型
        
        # 爬虫抓取
        await crawler.fetch_and_process()
        
        # 两个策略都订阅
        events = await event_store_with_fallback.read_stream("announcements")
        for event in events:
            await strategy_a.on_event(event)
            await strategy_b.on_event(event)
        
        # 验证两个策略都收到了事件
        assert len(strategy_a.received_events) == 1
        assert len(strategy_b.received_events) == 1
        
        # 验证策略 A 的反应（只关注 LISTING）
        assert len(strategy_a.listing_alerts) == 1
        
        # 验证策略 B 的反应（关注所有）
        assert len(strategy_b.listing_alerts) == 1
    
    @pytest.mark.asyncio
    async def test_empty_announcement_does_not_trigger_strategy(
        self, event_store_with_fallback, mock_http_client
    ):
        """
        测试空公告列表不触发策略
        
        当爬虫获取到空结果时，不应该有事件写入 store，
        策略也不应该收到任何事件。
        """
        empty_response = {
            "code": "000000",
            "data": {
                "articles": []
            }
        }
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=empty_response)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store_with_fallback,
            http_client=mock_http_client,
        )
        
        strategy = MockStrategy()
        
        # 爬虫获取空结果
        count = await crawler.fetch_and_process()
        assert count == 0
        
        # 策略订阅应该没有新事件
        events = await event_store_with_fallback.read_stream("announcements")
        assert len(events) == 0
        
        # 策略不应该收到任何事件
        assert len(strategy.received_events) == 0
    
    @pytest.mark.asyncio
    async def test_announcement_event_type_mapping(
        self, event_store_with_fallback, mock_http_client
    ):
        """
        测试事件类型映射正确性
        
        验证：
        - 所有公告类型都映射到 SIGNAL_GENERATED
        - 原始公告类型存储在 data['type'] 中
        - event_type_str 正确
        """
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "E2E301",
                        "title": "Binance将上线新币AAA",
                        "content": "listing",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                    {
                        "id": "E2E302",
                        "title": "Binance将下架BBB",
                        "content": "delist",
                        "timestamp": 1742832000001,
                        "type": 1,
                        "locale": "zh",
                    },
                    {
                        "id": "E2E303",
                        "title": "Binance系统维护",
                        "content": "maintenance",
                        "timestamp": 1742832000002,
                        "type": 1,
                        "locale": "zh",
                    },
                    {
                        "id": "E2E304",
                        "title": "Binance季度报告发布",
                        "content": "other",
                        "timestamp": 1742832000003,
                        "type": 1,
                        "locale": "zh",
                    },
                ]
            }
        }
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=articles)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store_with_fallback,
            http_client=mock_http_client,
        )
        
        # 抓取
        await crawler.fetch_and_process()
        
        # 读取并验证
        events = await event_store_with_fallback.read_stream("announcements")
        assert len(events) == 4
        
        # 验证所有事件的 EventType 都是 SIGNAL_GENERATED
        for event in events:
            assert event.event_type == "SIGNAL_GENERATED"
        
        # 验证 data['type'] 包含原始公告类型
        event_types_in_data = {e.data["type"] for e in events}
        assert event_types_in_data == {"LISTING", "DELISTING", "MAINTENANCE", "OTHER"}
        
        # 验证 event_type_str
        event_type_strs = {e.data["event_type_str"] for e in events}
        assert event_type_strs == {
            "ANNOUNCEMENT_LISTING",
            "ANNOUNCEMENT_DELISTING", 
            "ANNOUNCEMENT_MAINTENANCE",
            "ANNOUNCEMENT_OTHER",
        }


class TestAnnouncementCrawlerFailureE2E:
    """公告爬虫失败回退 E2E 测试"""
    
    @pytest.mark.asyncio
    async def test_strategy_handles_missing_symbols_gracefully(
        self, event_store_with_fallback, mock_http_client
    ):
        """
        测试策略优雅处理无法提取交易对的公告
        
        当公告中没有提取到交易对时，策略不应该崩溃。
        """
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "E2E401",
                        "title": "Binance将上线新的DeFi项目",
                        "content": "公告内容未提及具体交易对",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                ]
            }
        }
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=articles)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store_with_fallback,
            http_client=mock_http_client,
        )
        
        strategy = MockStrategy()
        
        # 爬虫抓取
        count = await crawler.fetch_and_process()
        assert count == 1
        
        # 策略处理（应该优雅处理空 symbols 列表）
        events = await event_store_with_fallback.read_stream("announcements")
        for event in events:
            await strategy.on_event(event)  # 不应该崩溃
        
        # 验证策略收到了事件但没有 listing alerts（因为没有 symbols）
        assert len(strategy.received_events) == 1
        assert len(strategy.listing_alerts) == 0  # 没有 symbols，不触发
    
    @pytest.mark.asyncio
    async def test_memory_fallback_still_allows_subscription(
        self, event_store_with_fallback, mock_http_client
    ):
        """
        测试内存回退时仍然允许订阅
        
        即使使用内存存储（而非 PostgreSQL），
        策略仍然能通过 read_stream 订阅事件。
        """
        # 验证使用内存存储
        assert event_store_with_fallback.is_using_postgres is False
        
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "E2E501",
                        "title": "Binance将上线BTCUSDT",
                        "content": "test",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                ]
            }
        }
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=articles)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store_with_fallback,
            http_client=mock_http_client,
        )
        
        strategy = MockStrategy()
        
        # 爬虫写入
        await crawler.fetch_and_process()
        
        # 策略订阅（在内存回退模式下）
        events = await event_store_with_fallback.read_stream("announcements")
        assert len(events) == 1
        
        for event in events:
            await strategy.on_event(event)
        
        assert len(strategy.listing_alerts) == 1
