"""
Test Binance Announcement Crawler - 公告爬虫单元测试
===================================================
测试 BinanceAnnouncementCrawler 的分类、解析和写入功能。

覆盖场景：
1. 公告分类：ListingEvent, DelistingEvent, MaintenanceEvent, OtherEvent
2. 币种提取：交易对和币种代码提取
3. 公告解析：API 响应解析
4. 事件写入：幂等写入 event_store
5. 降级保护：网络错误不影响主流程
"""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from trader.adapters.announcements.binance_crawler import (
    BinanceAnnouncementCrawler,
    AnnouncementEvent,
    AnnouncementType,
    crawl_once,
)
from trader.core.domain.models.events import DomainEvent


# ==================== Fixtures ====================

@pytest.fixture
def mock_event_store():
    """Mock EventStoreWithFallback"""
    store = MagicMock()
    store.get_latest_seq = AsyncMock(return_value=9)
    store.append_domain_event = AsyncMock(return_value="event-123")
    return store


@pytest.fixture
def mock_http_client():
    """Mock httpx.AsyncClient"""
    client = MagicMock()
    client.get = AsyncMock()
    return client


@pytest.fixture
def sample_api_response():
    """Binance API 响应样例"""
    return {
        "code": "000000",
        "data": {
            "articles": [
                {
                    "id": "1569405498730455040",
                    "title": "Binance将上线新的DeFi项目并开放BTCUSDT交易对",
                    "content": "Binance将上线新的DeFi项目...",
                    "timestamp": 1742832000000,
                    "type": 1,
                    "locale": "zh",
                },
                {
                    "id": "1569405498730455041",
                    "title": "Binance将下架某些低流动性交易对",
                    "content": "为了保护用户利益...",
                    "timestamp": 1742832000000,
                    "type": 1,
                    "locale": "zh",
                },
                {
                    "id": "1569405498730455042",
                    "title": "Binance将于周末进行系统维护",
                    "content": "计划维护时间...",
                    "timestamp": 1742832000000,
                    "type": 1,
                    "locale": "zh",
                },
                {
                    "id": "1569405498730455043",
                    "title": "Binance开启新的杠杆代币交易对",
                    "content": "杠杆代币详情...",
                    "timestamp": 1742832000000,
                    "type": 1,
                    "locale": "zh",
                },
                {
                    "id": "1569405498730455044",
                    "title": "Binance 2024年第四季度报告发布",
                    "content": "季度报告详情...",
                    "timestamp": 1742832000000,
                    "type": 1,
                    "locale": "zh",
                },
            ]
        }
    }


# ==================== 分类测试 ====================

class TestAnnouncementClassification:
    """公告分类测试"""
    
    def setup_method(self):
        """每个测试方法前创建新的 crawler 实例"""
        self.crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=MagicMock(),
        )
    
    def test_listing_chinese(self):
        """中文：上线新币"""
        title = "Binance将上线新的DeFi项目并开放BTCUSDT交易对"
        assert self.crawler._classify_announcement(title) == AnnouncementType.LISTING
    
    def test_listing_english(self):
        """英文：new listing"""
        title = "Binance will launch new token and add TRXUSDT trading pair"
        assert self.crawler._classify_announcement(title) == AnnouncementType.LISTING
    
    def test_delisting_chinese(self):
        """中文：下架"""
        title = "Binance将下架某些低流动性交易对"
        assert self.crawler._classify_announcement(title) == AnnouncementType.DELISTING
    
    def test_delisting_english(self):
        """英文：delist"""
        title = "Binance will delist multiple trading pairs"
        assert self.crawler._classify_announcement(title) == AnnouncementType.DELISTING
    
    def test_maintenance_chinese(self):
        """中文：维护"""
        title = "Binance将于周末进行系统维护"
        assert self.crawler._classify_announcement(title) == AnnouncementType.MAINTENANCE
    
    def test_maintenance_english(self):
        """英文：maintenance"""
        title = "Binance will undergo system maintenance"
        assert self.crawler._classify_announcement(title) == AnnouncementType.MAINTENANCE
    
    def test_other(self):
        """其他类型公告"""
        title = "Binance 2024年第四季度报告发布"
        assert self.crawler._classify_announcement(title) == AnnouncementType.OTHER
    
    def test_mixed_keywords_priority(self):
        """混合关键词时的优先级"""
        # 下架优先于上线
        title = "由于下架，将停止上新币种"
        assert self.crawler._classify_announcement(title) == AnnouncementType.DELISTING


# ==================== 币种提取测试 ====================

class TestSymbolExtraction:
    """币种提取测试"""
    
    def setup_method(self):
        self.crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=MagicMock(),
        )
    
    def test_extract_trading_pairs(self):
        """提取标准交易对"""
        text = "Binance将上线新的DeFi项目并开放BTCUSDT和ETHUSDT交易对"
        symbols = self.crawler._extract_symbols(text)
        assert "BTC" in symbols
        assert "ETH" in symbols
    
    def test_extract_spot_margin_futures(self):
        """提取不同类型的交易对"""
        text = "新增 BTCUSDT 现货、ETHBTC 杠杆和 BTCPERP 合约交易"
        symbols = self.crawler._extract_symbols(text)
        assert "BTC" in symbols
        assert "ETH" in symbols
    
    def test_extract_from_listing_context(self):
        """从上线上下文中提取币种"""
        text = "Binance即将上线 SHIBIAO 并开放充值交易"
        symbols = self.crawler._extract_symbols(text)
        assert "SHIBIAO" in symbols
    
    def test_no_duplicates(self):
        """去重测试"""
        text = "BTCUSDT 和 BTCUSDT 交易对"
        symbols = self.crawler._extract_symbols(text)
        assert symbols.count("BTC") == 1
    
    def test_empty_text(self):
        """空文本"""
        symbols = self.crawler._extract_symbols("")
        assert symbols == []
    
    def test_extract_trailing_punctuation(self):
        """边界情况：交易对后紧跟标点符号"""
        text = "BTCUSDT."  # 句号结尾
        symbols = self.crawler._extract_symbols(text)
        assert "BTC" in symbols
    
    def test_extract_no_space_needed(self):
        """边界情况：交易对后紧跟非字母字符"""
        text = "ETHUSDT!BTCUSDT?"  # 紧跟感叹号和问号
        symbols = self.crawler._extract_symbols(text)
        assert "ETH" in symbols
        assert "BTC" in symbols


# ==================== 解析测试 ====================

class TestAnnouncementParsing:
    """公告解析测试"""
    
    def setup_method(self):
        self.crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=MagicMock(),
        )
    
    def test_parse_listing_announcement(self, sample_api_response):
        """解析上线公告"""
        articles = sample_api_response["data"]["articles"]
        result = self.crawler._parse_announcement(articles[0])
        
        assert result is not None
        assert result.announcement_id == "1569405498730455040"
        assert result.type == AnnouncementType.LISTING
        assert "BTC" in result.symbols  # 提取的是币种代码，不是完整交易对
        assert "DeFi" in result.title
    
    def test_parse_delisting_announcement(self, sample_api_response):
        """解析下架公告"""
        articles = sample_api_response["data"]["articles"]
        result = self.crawler._parse_announcement(articles[1])
        
        assert result is not None
        assert result.type == AnnouncementType.DELISTING
    
    def test_parse_maintenance_announcement(self, sample_api_response):
        """解析维护公告"""
        articles = sample_api_response["data"]["articles"]
        result = self.crawler._parse_announcement(articles[2])
        
        assert result is not None
        assert result.type == AnnouncementType.MAINTENANCE
    
    def test_parse_other_announcement(self, sample_api_response):
        """解析其他公告"""
        articles = sample_api_response["data"]["articles"]
        result = self.crawler._parse_announcement(articles[4])
        
        assert result is not None
        assert result.type == AnnouncementType.OTHER
    
    def test_parse_missing_fields(self):
        """解析缺失字段的公告"""
        article = {"id": "123"}
        result = self.crawler._parse_announcement(article)
        assert result is None
    
    def test_parse_empty_title(self):
        """解析空标题"""
        article = {"id": "123", "title": ""}
        result = self.crawler._parse_announcement(article)
        assert result is None


# ==================== HTTP 请求测试 ====================

class TestFetchAnnouncements:
    """公告抓取测试"""
    
    @pytest.mark.asyncio
    async def test_fetch_success(self, mock_http_client, sample_api_response):
        """成功抓取"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_api_response)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=mock_http_client,
        )
        
        result = await crawler.fetch_announcements()
        
        assert len(result) == 5
        mock_http_client.get.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_fetch_network_error(self, mock_http_client):
        """网络错误降级"""
        import httpx
        mock_http_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))
        
        crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=mock_http_client,
        )
        
        result = await crawler.fetch_announcements()
        
        assert result == []
    
    @pytest.mark.asyncio
    async def test_fetch_api_error(self, mock_http_client):
        """API 返回错误"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"code": "100000", "message": "error"})
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=mock_http_client,
        )
        
        result = await crawler.fetch_announcements()
        
        assert result == []


# ==================== 事件写入测试 ====================

class TestEventWriting:
    """事件写入测试"""
    
    @pytest.mark.asyncio
    async def test_write_new_event(self, mock_event_store):
        """写入新事件"""
        crawler = BinanceAnnouncementCrawler(
            event_store=mock_event_store,
            http_client=MagicMock(),
        )
        
        ann_event = AnnouncementEvent(
            announcement_id="test-123",
            title="Test Listing",
            content="Test content",
            type=AnnouncementType.LISTING,
            timestamp=datetime.now(timezone.utc),
            source_url="https://binance.com/test",
            symbols=["BTC"],
        )
        
        result = await crawler._write_to_event_store(ann_event)
        
        assert result is True
        mock_event_store.append_domain_event.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_idempotent_write(self, mock_event_store):
        """幂等写入"""
        crawler = BinanceAnnouncementCrawler(
            event_store=mock_event_store,
            http_client=MagicMock(),
        )
        
        ann_event = AnnouncementEvent(
            announcement_id="test-123",
            title="Test Listing",
            content="Test content",
            type=AnnouncementType.LISTING,
            timestamp=datetime.now(timezone.utc),
            source_url="https://binance.com/test",
            symbols=["BTC"],
        )
        
        # 第一次写入
        await crawler._write_to_event_store(ann_event)
        # 第二次写入（应该跳过）
        await crawler._write_to_event_store(ann_event)
        
        # 只调用一次
        assert mock_event_store.append_domain_event.call_count == 1
    
    @pytest.mark.asyncio
    async def test_write_failure(self, mock_event_store):
        """写入失败"""
        mock_event_store.append_domain_event = AsyncMock(
            side_effect=Exception("Write failed")
        )
        
        crawler = BinanceAnnouncementCrawler(
            event_store=mock_event_store,
            http_client=MagicMock(),
        )
        
        ann_event = AnnouncementEvent(
            announcement_id="test-123",
            title="Test Listing",
            content="Test content",
            type=AnnouncementType.LISTING,
            timestamp=datetime.now(timezone.utc),
            source_url="https://binance.com/test",
            symbols=["BTC"],
        )
        
        result = await crawler._write_to_event_store(ann_event)
        
        assert result is False


# ==================== 完整流程测试 ====================

class TestFullFlow:
    """完整流程测试"""
    
    @pytest.mark.asyncio
    async def test_fetch_and_process(self, mock_event_store, mock_http_client, sample_api_response):
        """完整抓取处理流程"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_api_response)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=mock_event_store,
            http_client=mock_http_client,
        )
        
        count = await crawler.fetch_and_process()
        
        # 5 个公告，4 个被分类（最后一个是 OTHER，但也应该被处理）
        assert count == 5
        assert len(crawler._processed_ids) == 5
    
    @pytest.mark.asyncio
    async def test_fetch_and_process_empty(self, mock_event_store, mock_http_client):
        """空结果处理"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"code": "000000", "data": {"articles": []}})
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=mock_event_store,
            http_client=mock_http_client,
        )
        
        count = await crawler.fetch_and_process()
        
        assert count == 0


# ==================== 便捷函数测试 ====================

class TestConvenienceFunction:
    """便捷函数测试"""
    
    @pytest.mark.asyncio
    async def test_crawl_once(self, mock_event_store, mock_http_client, sample_api_response):
        """直接测试 crawler.fetch_and_process()（等效于 crawl_once 功能）"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_api_response)
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.aclose = AsyncMock()
        
        # 创建带有 mock client 的 crawler
        crawler = BinanceAnnouncementCrawler(
            event_store=mock_event_store,
            http_client=mock_http_client,
        )
        
        count = await crawler.fetch_and_process()
        assert count == 5
        
        # 验证资源清理
        await crawler.close()
    
    @pytest.mark.asyncio
    async def test_crawl_once_function_directly(self, sample_api_response):
        """直接测试 crawl_once 便捷函数本身"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_api_response)
        
        # Patch httpx.AsyncClient 构造函数
        with patch('httpx.AsyncClient') as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            mock_client_instance.aclose = AsyncMock()
            MockClient.return_value = mock_client_instance
            
            mock_event_store = MagicMock()
            mock_event_store.get_latest_seq = AsyncMock(return_value=0)
            mock_event_store.append_domain_event = AsyncMock(return_value="event-123")
            
            count = await crawl_once(mock_event_store)
            assert count == 5


# ==================== 生命周期测试 ====================

class TestLifecycle:
    """生命周期测试"""
    
    def test_max_concurrent_requests_default(self):
        """默认 max_concurrent_requests=1 限制"""
        crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=MagicMock(),
        )
        assert crawler._semaphore._value == 1
    
    def test_max_concurrent_requests_custom(self):
        """自定义 max_concurrent_requests"""
        crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=MagicMock(),
            max_concurrent_requests=5,
        )
        assert crawler._semaphore._value == 5
    
    @pytest.mark.asyncio
    async def test_start_stop(self, mock_http_client):
        """启动停止"""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"code": "000000", "data": {"articles": []}})
        mock_http_client.get = AsyncMock(return_value=mock_response)
        
        crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=mock_http_client,
            poll_interval_seconds=1,  # 1秒便于测试
        )
        
        # 启动后台任务（会在1秒后自动停止因为我们调用了 stop）
        async def stop_after_start():
            await asyncio.sleep(0.1)
            crawler.stop()
        
        task = asyncio.create_task(crawler.start_background_polling())
        stop_task = asyncio.create_task(stop_after_start())
        
        await asyncio.gather(task, stop_task, return_exceptions=True)
        
        assert crawler._running is False
    
    @pytest.mark.asyncio
    async def test_close(self, mock_http_client):
        """关闭资源"""
        mock_http_client.aclose = AsyncMock()
        
        crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=mock_http_client,
        )
        
        await crawler.close()
        
        assert crawler._running is False
        mock_http_client.aclose.assert_called_once()
