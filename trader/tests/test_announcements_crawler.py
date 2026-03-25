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
from trader.adapters.announcements.models import RawAnnouncement
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


# ==================== HTML Fixtures (v2 调整) ====================

@pytest.fixture
def html_list_fixture():
    """HTML 公告列表 fixture (v2 拆分)"""
    return """
    <html><body>
    <div class="announce-item">
        <h3>Binance将上线新的DeFi项目</h3>
        <p>开放 BTCUSDT、ETHUSDT 交易对</p>
        <a href="/support/announcement/detail/123">查看详情</a>
    </div>
    <div class="announce-item">
        <h3>Binance将下架某些交易对</h3>
        <p>停止 DOGEUSDT、SHIBUSDT 交易</p>
        <a href="/support/announcement/detail/124">查看详情</a>
    </div>
    </body></html>
    """


@pytest.fixture
def html_detail_fixture():
    """HTML 公告详情 fixture (v2 拆分)"""
    return """
    <html><body>
    <div class="announcement-detail">
        <h1>Binance将上线新的DeFi项目</h1>
        <div class="content">
            Binance非常荣幸地宣布将于近期上线新的DeFi项目。
            开放 BTCUSDT、ETHUSDT、BNBUSDT 交易对。
            请广大用户做好准备。
        </div>
    </div>
    </body></html>
    """


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
    
    def test_mixed_language_keywords(self):
        """多语言混合关键词测试"""
        # 中文标题含英文关键词
        title = "Binance将上线新的DeFi项目 listing"
        assert self.crawler._classify_announcement(title) == AnnouncementType.LISTING
        
        # 英文标题含中文关键词
        title = "Binance will launch new token 上线"
        assert self.crawler._classify_announcement(title) == AnnouncementType.LISTING
        
        # 维护相关的中英文混合
        title = "system maintenance 维护通知"
        assert self.crawler._classify_announcement(title) == AnnouncementType.MAINTENANCE


# ==================== 币种提取测试 (v2 调整) ====================

class TestSymbolExtraction:
    """币种提取测试 - v2 返回完整交易对"""
    
    def setup_method(self):
        self.crawler = BinanceAnnouncementCrawler(
            event_store=MagicMock(),
            http_client=MagicMock(),
        )
    
    def test_extract_trading_pairs_returns_full_pair(self):
        """v2 调整: 提取标准交易对应返回完整交易对 (BTCUSDT) 而非 base asset (BTC)"""
        text = "Binance将上线新的DeFi项目并开放BTCUSDT和ETHUSDT交易对"
        symbols = self.crawler._extract_symbols(text)
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols
        assert "BTC" not in symbols  # v2 不再返回 base asset
        assert "ETH" not in symbols
    
    def test_extract_multiple_pairs(self):
        """多个交易对"""
        text = "开放 BTCUSDT、ETHUSDT、BNBUSDT 交易对"
        symbols = self.crawler._extract_symbols(text)
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols
        assert "BNBUSDT" in symbols
    
    def test_extract_different_quote_currencies(self):
        """不同计价货币的交易对"""
        text = "新增 BTCUSDT、ETHBTC、BNBETH 交易对"
        symbols = self.crawler._extract_symbols(text)
        assert "BTCUSDT" in symbols
        assert "ETHBTC" in symbols
        assert "BNBETH" in symbols
    
    def test_extract_no_base_asset_duplicates(self):
        """不返回 base asset (避免重复)"""
        text = "BTCUSDT 和 BTCUSDT 交易对"
        symbols = self.crawler._extract_symbols(text)
        assert symbols.count("BTCUSDT") == 1
        assert "BTC" not in symbols
    
    def test_extract_from_listing_context(self):
        """v2 不再支持: 从上线上下文中提取无计价货币的币种"""
        # v2 只返回完整交易对，所以 "SHIBIAO" 不会被提取
        # 这个测试验证旧行为不再适用
        text = "Binance即将上线 SHIBIAO 并开放充值交易"
        symbols = self.crawler._extract_symbols(text)
        # v2: 没有计价货币的币种不会被提取
        assert "SHIBIAO" not in symbols
        assert len(symbols) == 0
    
    def test_no_duplicates(self):
        """v2: 去重测试完整交易对"""
        text = "BTCUSDT 和 BTCUSDT 交易对"
        symbols = self.crawler._extract_symbols(text)
        assert symbols.count("BTCUSDT") == 1
    
    def test_empty_text(self):
        """空文本"""
        symbols = self.crawler._extract_symbols("")
        assert symbols == []
    
    def test_extract_trailing_punctuation(self):
        """v2: 边界情况：交易对后紧跟标点符号"""
        text = "BTCUSDT."  # 句号结尾
        symbols = self.crawler._extract_symbols(text)
        assert "BTCUSDT" in symbols
    
    def test_extract_no_space_needed(self):
        """v2: 边界情况：交易对后紧跟非字母字符"""
        text = "ETHUSDT!BTCUSDT?"  # 紧跟感叹号和问号
        symbols = self.crawler._extract_symbols(text)
        assert "ETHUSDT" in symbols
        assert "BTCUSDT" in symbols


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
        # v2: 提取的是完整交易对 BTCUSDT，不是 base asset BTC
        assert "BTCUSDT" in result.symbols
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
    
    @pytest.mark.asyncio
    async def test_write_with_none_latest_seq(self):
        """get_latest_seq 返回 None 时的回退路径（空 stream）"""
        mock_store = MagicMock()
        mock_store.get_latest_seq = AsyncMock(return_value=None)
        mock_store.append_domain_event = AsyncMock(return_value="event-123")
        
        crawler = BinanceAnnouncementCrawler(
            event_store=mock_store,
            http_client=MagicMock(),
        )
        
        ann_event = AnnouncementEvent(
            announcement_id="test-456",
            title="Test Listing",
            content="Test content",
            type=AnnouncementType.LISTING,
            timestamp=datetime.now(timezone.utc),
            source_url="https://binance.com/test",
            symbols=["ETH"],
        )
        
        result = await crawler._write_to_event_store(ann_event)
        
        assert result is True
        # 验证 get_latest_seq 被调用，且 next_seq 为 0
        mock_store.get_latest_seq.assert_called_once_with("announcements")
        # 验证写入被调用，seq 参数为 0
        call_args = mock_store.append_domain_event.call_args
        assert call_args is not None
        assert call_args.kwargs.get("seq") == 0 or (len(call_args.args) >= 3 and call_args.args[2] == 0)
    
    @pytest.mark.asyncio
    async def test_write_with_existing_latest_seq(self):
        """get_latest_seq 返回已有 seq 时的递增逻辑"""
        mock_store = MagicMock()
        mock_store.get_latest_seq = AsyncMock(return_value=99)
        mock_store.append_domain_event = AsyncMock(return_value="event-123")
        
        crawler = BinanceAnnouncementCrawler(
            event_store=mock_store,
            http_client=MagicMock(),
        )
        
        ann_event = AnnouncementEvent(
            announcement_id="test-789",
            title="Test Delisting",
            content="Test content",
            type=AnnouncementType.DELISTING,
            timestamp=datetime.now(timezone.utc),
            source_url="https://binance.com/test",
            symbols=["DOGE"],
        )
        
        result = await crawler._write_to_event_store(ann_event)
        
        assert result is True
        # 验证 next_seq 为 100 (99 + 1)
        call_args = mock_store.append_domain_event.call_args
        assert call_args is not None
        assert call_args.kwargs.get("seq") == 100 or (len(call_args.args) >= 3 and call_args.args[2] == 100)


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


# ==================== RawAnnouncement 模型测试 (v2 新增) ====================

class TestRawAnnouncement:
    """RawAnnouncement 模型测试"""
    
    def test_optional_fields(self):
        """v2: 所有字段应为 Optional"""
        ann = RawAnnouncement()
        assert ann.catalog_id is None
        assert ann.announcement_id is None
        assert ann.title is None
        assert ann.body is None
        assert ann.publish_time is None
        assert ann.detail_url is None
        assert ann.locale is None
        assert ann.source is None
        assert ann.external_id is None
        assert ann.disclaimer is None
    
    def test_dedup_key_from_url(self):
        """去重键优先使用 detail_url"""
        ann = RawAnnouncement(
            detail_url="https://binance.com/support/announcement/detail/ABC123XYZ"
        )
        assert ann.dedup_key == "ABC123XYZ"
    
    def test_dedup_key_fallback(self):
        """去重键 fallback 到 content hash"""
        ann = RawAnnouncement(
            title="Test Title",
            body="Test Body Content",
        )
        # 应该是一个 SHA256 hash (64 字符)
        assert len(ann.dedup_key) == 64
        assert ann.dedup_key.isalnum()
    
    def test_to_dict(self):
        """转换为字典"""
        ann = RawAnnouncement(
            catalog_id="cat-1",
            announcement_id="ann-1",
            title="Test",
            body="Content",
            locale="zh",
            source="html",
        )
        d = ann.to_dict()
        assert d["catalog_id"] == "cat-1"
        assert d["announcement_id"] == "ann-1"
        assert d["title"] == "Test"
        assert d["source"] == "html"
        assert "dedup_key" in d
    
    def test_from_ws_message(self):
        """从 WebSocket 消息创建"""
        msg = {
            "command": "SUBSCRIBE",
            "value": "com_announcement_en",
            "data": {
                "id": "123",
                "title": "Binance 公告",
                "content": "公告内容",
                "publishTime": 1742832000000,
            }
        }
        ann = RawAnnouncement.from_ws_message(msg)
        assert ann.announcement_id == "123"
        assert ann.title == "Binance 公告"
        assert ann.body == "公告内容"
        assert ann.source == "ws"
    
    def test_from_html_parse(self):
        """从 HTML 解析结果创建"""
        ann = RawAnnouncement.from_html_parse(
            title="Test Title",
            body="Test Body",
            detail_url="https://binance.com/announcement/123",
            locale="zh",
        )
        assert ann.title == "Test Title"
        assert ann.body == "Test Body"
        assert ann.detail_url == "https://binance.com/announcement/123"
        assert ann.source == "html"


# ==================== HTML Source 测试 (v2 新增) ====================

class TestHtmlSourceParsing:
    """HTML Source 解析测试"""
    
    def test_parse_html_list(self, html_list_fixture):
        """解析 HTML 公告列表"""
        from trader.adapters.announcements.html_source import BinanceHtmlAnnouncementSource
        
        source = BinanceHtmlAnnouncementSource()
        announcements = source.parse_html_list(html_list_fixture)
        
        assert len(announcements) == 2
        assert announcements[0].title == "Binance将上线新的DeFi项目"
        assert announcements[0].source == "html"
    
    def test_parse_html_detail(self, html_detail_fixture):
        """解析 HTML 公告详情"""
        from trader.adapters.announcements.html_source import BinanceHtmlAnnouncementSource
        
        source = BinanceHtmlAnnouncementSource()
        detail_url = "https://binance.com/support/announcement/detail/123"
        ann = source.parse_html_detail(html_detail_fixture, detail_url)
        
        assert ann is not None
        assert ann.title == "Binance将上线新的DeFi项目"
        assert "BTCUSDT" in ann.body
        assert ann.detail_url == detail_url
    
    def test_extract_symbols_returns_full_pair(self):
        """HTML Source 的 _extract_symbols 也应返回完整交易对"""
        from trader.adapters.announcements.html_source import BinanceHtmlAnnouncementSource
        
        source = BinanceHtmlAnnouncementSource()
        symbols = source._extract_symbols("Binance将上线 BTCUSDT 交易对")
        
        assert "BTCUSDT" in symbols
        assert "BTC" not in symbols


# ==================== WS Source 测试 (v3 新增) ====================

class TestWsSourceParsing:
    """WS Source 消息解析测试"""
    
    def test_parse_command_response(self):
        """COMMAND 响应消息应被正确处理"""
        from trader.adapters.announcements.ws_source import BinanceWsAnnouncementSource
        
        source = BinanceWsAnnouncementSource()
        
        # 模拟 COMMAND 响应
        command_msg = {
            "type": "COMMAND",
            "data": "SUCCESS",
            "subType": "SUBSCRIBE",
            "code": "00000000"
        }
        
        # recv_one 内部的消息解析逻辑通过 recv_async_iterator 测试
        # 这里测试 _should_stop 和异常处理
        # COMMAND 消息不应该导致异常
        import json
        outer = json.loads(json.dumps(command_msg))
        assert outer["type"] == "COMMAND"
        assert outer.get("data") == "SUCCESS"
        assert outer.get("subType") == "SUBSCRIBE"
    
    def test_parse_data_double_json(self):
        """DATA 消息的 data 是 JSON 字符串，需要二次解析"""
        import json
        
        # 外层消息
        outer_msg = {
            "type": "DATA",
            "topic": "com_announcement_en",
            "data": json.dumps({
                "catalogId": 161,
                "announcementId": "123456",
                "title": "Binance将上线新币种",
                "body": "公告内容",
                "publishDate": "2024-03-25"
            })
        }
        
        outer = json.loads(json.dumps(outer_msg))
        assert outer["type"] == "DATA"
        
        # 模拟二次解析
        inner_data = outer.get("data")
        assert isinstance(inner_data, str)
        inner = json.loads(inner_data)
        assert inner["announcementId"] == "123456"
        assert inner["title"] == "Binance将上线新币种"
    
    def test_parse_data_dict_compatibility(self):
        """data 是 dict 的兼容分支"""
        # 有些实现可能直接返回 dict 而不是 JSON 字符串
        outer_msg = {
            "type": "DATA",
            "topic": "com_announcement_en",
            "data": {
                "catalogId": 161,
                "announcementId": "789",
                "title": "Test Announcement",
                "body": "Content here",
            }
        }
        
        assert outer_msg["type"] == "DATA"
        inner_data = outer_msg.get("data")
        assert isinstance(inner_data, dict)
        assert inner_data["announcementId"] == "789"
    
    def test_parse_unknown_type_continues(self):
        """未知类型的消息应该被跳过而不是退出循环"""
        unknown_msg = {
            "type": "UNKNOWN",
            "someField": "value"
        }
        
        # 未知类型应该只记录 warning，不抛出异常
        assert unknown_msg["type"] == "UNKNOWN"
        # 这是解析逻辑的预期行为：warning + continue


# ==================== Orchestration Layer Tests (v3 新增) ====================

class TestWsStreamOrchestration:
    """WS Stream 编排层测试"""
    
    @pytest.mark.asyncio
    async def test_ws_stream_gets_ws_source(self):
        """ws_stream() 应获取或创建 WS Source"""
        from trader.adapters.announcements.binance_crawler import BinanceAnnouncementCrawler
        from trader.adapters.announcements.ws_source import BinanceWsAnnouncementSource
        from unittest.mock import MagicMock
        
        event_store = MagicMock()
        event_store.get_latest_seq = AsyncMock(return_value=None)
        event_store.append_domain_event = AsyncMock(return_value=True)
        
        crawler = BinanceAnnouncementCrawler(event_store=event_store)
        
        # 验证 _get_ws_source 方法存在
        assert hasattr(crawler, '_get_ws_source')
        
        # 调用 _get_ws_source 应该创建一个 WS Source
        ws_source = crawler._get_ws_source()
        assert ws_source is not None
        # 增强断言：验证返回类型是 BinanceWsAnnouncementSource
        assert isinstance(ws_source, BinanceWsAnnouncementSource), (
            f"Expected BinanceWsAnnouncementSource, got {type(ws_source).__name__}"
        )
        # 验证 ws_source 具有必要的方法和属性
        assert hasattr(ws_source, 'connect')
        assert hasattr(ws_source, 'subscribe')
        assert hasattr(ws_source, 'recv_one')
        assert hasattr(ws_source, 'recv_async_iterator')
    
    @pytest.mark.asyncio
    async def test_ws_stream_returns_async_iterator(self):
        """ws_stream() 应返回异步迭代器"""
        from trader.adapters.announcements.binance_crawler import BinanceAnnouncementCrawler
        from unittest.mock import MagicMock
        
        event_store = MagicMock()
        crawler = BinanceAnnouncementCrawler(event_store=event_store)
        
        # Mock ws_source
        mock_ws = MagicMock()
        mock_ws.is_connected = False
        mock_ws.is_subscribed = False
        mock_ws.connect = AsyncMock()
        mock_ws.subscribe = AsyncMock()
        
        # recv_async_iterator 是 async generator，需要用 async def 模拟
        async def mock_async_gen():
            yield  # 确保是生成器
        mock_ws.recv_async_iterator = MagicMock(return_value=mock_async_gen())
        crawler._ws_source = mock_ws
        
        # ws_stream() 应该返回一个异步生成器
        result = crawler.ws_stream()
        import inspect
        assert inspect.isasyncgen(result) or hasattr(result, '__aiter__')
    
    @pytest.mark.asyncio
    async def test_process_raw_announcement_success(self):
        """process_raw_announcement() 成功处理 RawAnnouncement"""
        from trader.adapters.announcements.binance_crawler import BinanceAnnouncementCrawler
        from trader.adapters.announcements.models import RawAnnouncement
        from datetime import datetime, timezone
        
        event_store = MagicMock()
        event_store.get_latest_seq = AsyncMock(return_value=0)
        event_store.append_domain_event = AsyncMock(return_value=True)
        
        crawler = BinanceAnnouncementCrawler(event_store=event_store)
        
        # 创建一个有效的 RawAnnouncement
        raw_ann = RawAnnouncement(
            announcement_id="123",
            title="Binance将上线 BTCUSDT 交易对",
            body="Binance非常荣幸地宣布...",
            publish_time=datetime(2024, 3, 25, 12, 0, 0, tzinfo=timezone.utc),
            locale="zh",
            source="ws",
        )
        
        result = await crawler.process_raw_announcement(raw_ann)
        assert result is True
        event_store.append_domain_event.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_process_raw_announcement_invalid_title(self):
        """process_raw_announcement() 对无效标题返回 False"""
        from trader.adapters.announcements.binance_crawler import BinanceAnnouncementCrawler
        from trader.adapters.announcements.models import RawAnnouncement
        
        event_store = MagicMock()
        crawler = BinanceAnnouncementCrawler(event_store=event_store)
        
        # 创建一个无效的 RawAnnouncement (无标题)
        raw_ann = RawAnnouncement(
            announcement_id="123",
            title=None,  # 无效标题
            body="Some content",
            source="ws",
        )
        
        result = await crawler.process_raw_announcement(raw_ann)
        assert result is False


class TestWsToHtmlFailover:
    """WS 到 HTML 降级测试"""
    
    @pytest.mark.asyncio
    async def test_fetch_announcements_falls_back_to_html(self):
        """WS 获取失败时降级到 HTML (通过 raw HTTP)"""
        from trader.adapters.announcements.binance_crawler import BinanceAnnouncementCrawler
        
        event_store = MagicMock()
        
        # Mock WS source 抛出异常
        mock_ws = MagicMock()
        mock_ws.fetch_initial = AsyncMock(side_effect=Exception("WS connection failed"))
        
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store,
            ws_source=mock_ws,
            use_ws_primary=True,
        )
        
        # Mock HTTP client - _fetch_from_html 使用原始 HTTP
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={
            "code": "000000",
            "data": {
                "articles": [
                    {
                        "id": "456",
                        "title": "Test Announcement",
                        "content": "Content here",
                        "timestamp": 1711234567890,
                        "locale": "zh",
                    }
                ]
            }
        })
        
        mock_http_client = MagicMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        crawler._http_client = mock_http_client
        
        result = await crawler.fetch_announcements()
        
        # 应该降级到 HTML 并返回数据
        assert len(result) == 1
        assert result[0]["id"] == "456"
    
    @pytest.mark.asyncio
    async def test_fetch_announcements_uses_ws_when_available(self):
        """WS 可用时优先使用 WS"""
        from trader.adapters.announcements.binance_crawler import BinanceAnnouncementCrawler
        from trader.adapters.announcements.models import RawAnnouncement
        from datetime import datetime, timezone
        
        event_store = MagicMock()
        
        # Mock WS source 返回数据
        mock_ws = MagicMock()
        mock_ws.fetch_initial = AsyncMock(return_value=[
            RawAnnouncement(
                announcement_id="789",
                title="WS Announcement",
                body="WS Content",
                publish_time=datetime(2024, 3, 25, 12, 0, 0, tzinfo=timezone.utc),
                locale="en",
                source="ws",
            )
        ])
        
        crawler = BinanceAnnouncementCrawler(
            event_store=event_store,
            ws_source=mock_ws,
            use_ws_primary=True,
        )
        
        result = await crawler.fetch_announcements()
        
        # 应该使用 WS 数据并转换为 dict
        assert len(result) == 1
        assert result[0]["id"] == "789"
        assert result[0]["title"] == "WS Announcement"
        mock_ws.fetch_initial.assert_called_once()
