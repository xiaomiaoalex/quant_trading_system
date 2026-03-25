"""
Test Binance Announcement Crawler Integration - 公告爬虫集成测试
================================================================
测试 BinanceAnnouncementCrawler 与 EventStoreWithFallback 的真实交互。

覆盖场景：
1. 使用真实 EventStoreWithFallback（内存模式）进行幂等写入验证
2. 验证重复调用不产生重复事件
3. 验证 read_stream("announcements") 能读取到写入的事件
4. 验证不同公告类型的正确处理
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from trader.adapters.announcements.binance_crawler import (
    BinanceAnnouncementCrawler,
    AnnouncementEvent,
    AnnouncementType,
)
from trader.adapters.persistence.event_store import EventStoreWithFallback
from trader.adapters.persistence.memory.event_store import InMemoryEventStore
from trader.core.domain.models.events import DomainEvent, EventType


# ==================== Fixtures ====================

@pytest.fixture
def memory_event_store():
    """纯内存 EventStoreWithFallback（无 PostgreSQL）"""
    memory_store = InMemoryEventStore()
    event_store = EventStoreWithFallback(
        pg_event_store=None,
        memory_event_store=memory_store,
    )
    return event_store


@pytest.fixture
def mock_http_client():
    """Mock httpx.AsyncClient"""
    client = MagicMock()
    client.get = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def sample_api_response():
    """Binance API 响应样例"""
    return {
        "code": "000000",
        "data": {
            "articles": [
                {
                    "id": "1569405498730455001",
                    "title": "Binance将上线新的DeFi项目并开放BTCUSDT交易对",
                    "content": "Binance将上线新的DeFi项目...",
                    "timestamp": 1742832000000,
                    "type": 1,
                    "locale": "zh",
                },
                {
                    "id": "1569405498730455002",
                    "title": "Binance将下架某些低流动性交易对",
                    "content": "为了保护用户利益...",
                    "timestamp": 1742832000000,
                    "type": 1,
                    "locale": "zh",
                },
                {
                    "id": "1569405498730455003",
                    "title": "Binance将于周末进行系统维护",
                    "content": "计划维护时间...",
                    "timestamp": 1742832000000,
                    "type": 1,
                    "locale": "zh",
                },
            ]
        }
    }


# ==================== 集成测试 ====================

class TestAnnouncementCrawlerIntegration:
    """公告爬虫与 EventStore 集成测试"""
    
    @pytest.mark.asyncio
    async def test_fetch_and_process_idempotent_write(
        self, memory_event_store, mock_http_client, sample_api_response
    ):
        """验证幂等写入：重复调用不产生重复事件"""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_api_response)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        # Create crawler with real event store
        crawler = BinanceAnnouncementCrawler(
            event_store=memory_event_store,
            http_client=mock_http_client,
        )
        
        # First call - should write 3 events
        count1 = await crawler.fetch_and_process()
        assert count1 == 3
        
        # Verify events were written
        events1 = await memory_event_store.read_stream("announcements")
        assert len(events1) == 3
        
        # Second call - should be idempotent (skip already processed)
        count2 = await crawler.fetch_and_process()
        assert count2 == 0  # No new events
        
        # Verify no duplicate events
        events2 = await memory_event_store.read_stream("announcements")
        assert len(events2) == 3  # Still 3 events, not 6
        
        # Verify the crawler processed_ids cache is working
        assert len(crawler._processed_ids) == 3
    
    @pytest.mark.asyncio
    async def test_read_stream_announcements(
        self, memory_event_store, mock_http_client, sample_api_response
    ):
        """验证 read_stream('announcements') 能读取到写入的事件"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_api_response)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=memory_event_store,
            http_client=mock_http_client,
        )
        
        # Process announcements
        await crawler.fetch_and_process()
        
        # Read from stream
        events = await memory_event_store.read_stream("announcements")
        
        assert len(events) == 3
        
        # Verify event structure
        for event in events:
            assert event.stream_key == "announcements"
            assert event.event_type == "SIGNAL_GENERATED"
            assert event.aggregate_type == "Announcement"
            assert "announcement_id" in event.data
            assert "title" in event.data
            assert "type" in event.data
    
    @pytest.mark.asyncio
    async def test_different_announcement_types(
        self, memory_event_store, mock_http_client
    ):
        """验证不同公告类型的正确处理"""
        # Different announcement types
        different_articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "1001",
                        "title": "Binance将上线新的DeFi项目",
                        "content": "listing announcement",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                    {
                        "id": "1002",
                        "title": "Binance将下架某些交易对",
                        "content": "delist announcement",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                    {
                        "id": "1003",
                        "title": "Binance将于周末进行系统维护",
                        "content": "maintenance announcement",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                    {
                        "id": "1004",
                        "title": "Binance 2024年第四季度报告发布",
                        "content": "other announcement",
                        "timestamp": 1742832000000,
                        "type": 1,
                        "locale": "zh",
                    },
                ]
            }
        }
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=different_articles)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=memory_event_store,
            http_client=mock_http_client,
        )
        
        count = await crawler.fetch_and_process()
        assert count == 4
        
        # Verify events in stream
        events = await memory_event_store.read_stream("announcements")
        assert len(events) == 4
        
        # Verify event types are stored in data
        event_types = [e.data["type"] for e in events]
        assert "LISTING" in event_types
        assert "DELISTING" in event_types
        assert "MAINTENANCE" in event_types
        assert "OTHER" in event_types
    
    @pytest.mark.asyncio
    async def test_empty_response_handling(
        self, memory_event_store, mock_http_client
    ):
        """验证空响应处理"""
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
            event_store=memory_event_store,
            http_client=mock_http_client,
        )
        
        count = await crawler.fetch_and_process()
        assert count == 0
        
        events = await memory_event_store.read_stream("announcements")
        assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_get_latest_seq_none_fallback(
        self, memory_event_store, mock_http_client
    ):
        """验证 get_latest_seq 返回 None 时的回退（内存存储不支持 stream_key seq 追踪）"""
        # Memory store returns None for get_latest_seq (limitation of memory fallback)
        latest_seq = await memory_event_store.get_latest_seq("announcements")
        assert latest_seq is None  # Memory store doesn't track seq per stream_key
        
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "2001",
                        "title": "Binance将上线新币",
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
            event_store=memory_event_store,
            http_client=mock_http_client,
        )
        
        # Should handle None seq gracefully
        count = await crawler.fetch_and_process()
        assert count == 1
        
        events = await memory_event_store.read_stream("announcements")
        assert len(events) == 1
    
    @pytest.mark.asyncio
    async def test_concurrent_idempotent_writes(
        self, memory_event_store, mock_http_client, sample_api_response
    ):
        """验证并发写入的幂等性"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_api_response)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=memory_event_store,
            http_client=mock_http_client,
        )
        
        # Simulate concurrent calls
        results = await asyncio.gather(
            crawler.fetch_and_process(),
            crawler.fetch_and_process(),
            crawler.fetch_and_process(),
        )
        
        # First call returns 3, subsequent calls return 0 (idempotent)
        assert results[0] == 3
        assert results[1] == 0
        assert results[2] == 0
        
        # Verify only 3 events were written
        events = await memory_event_store.read_stream("announcements")
        assert len(events) == 3


class TestAnnouncementEventMetadata:
    """公告事件元数据测试"""
    
    @pytest.mark.asyncio
    async def test_event_metadata_fields(
        self, memory_event_store, mock_http_client
    ):
        """验证事件元数据字段正确"""
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "3001",
                        "title": "Binance将上线BTCUSDT交易对",
                        "content": "Detailed content here",
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
            event_store=memory_event_store,
            http_client=mock_http_client,
        )
        
        await crawler.fetch_and_process()
        
        events = await memory_event_store.read_stream("announcements")
        assert len(events) == 1
        
        event = events[0]
        
        # Verify metadata
        assert "source" in event.metadata
        assert event.metadata["source"] == "binance_cms_api"
        assert "local_receive_ts_ms" in event.metadata
        assert "exchange_event_ts_ms" in event.metadata
        
        # Verify data fields
        assert event.data["announcement_id"] == "3001"
        assert event.data["title"] == "Binance将上线BTCUSDT交易对"
        assert event.data["type"] == "LISTING"
        assert event.data["event_type_str"] == "ANNOUNCEMENT_LISTING"
        assert "BTCUSDT" in event.data["symbols"]  # v2: full pair not base asset
        assert "source_url" in event.data
        assert "binance.com" in event.data["source_url"]
    
    @pytest.mark.asyncio
    async def test_aggregate_id_format(
        self, memory_event_store, mock_http_client
    ):
        """验证 aggregate_id 格式正确"""
        articles = {
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "4001",
                        "title": "Binance将上线ETHUSDT",
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
            event_store=memory_event_store,
            http_client=mock_http_client,
        )
        
        await crawler.fetch_and_process()
        
        events = await memory_event_store.read_stream("announcements")
        assert len(events) == 1
        
        event = events[0]
        
        # aggregate_id should be: {announcement_id}_{type}
        assert event.aggregate_id == "4001_LISTING"
        assert event.aggregate_type == "Announcement"
