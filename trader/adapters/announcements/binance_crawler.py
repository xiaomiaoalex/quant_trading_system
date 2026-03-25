"""
Binance Announcement Crawler - Binance 公告爬虫
===============================================
从 Binance 官方公告源采集事件公告，分类后写入 event_log。

数据源：
- Binance WebSocket (主): wss://stream.binance.com:9443/ws/com_announcement_en
- Binance HTML API (回退): https://www.binance.com/bapi/earn/v1/public/feign/cms/article/list/query

事件分类：
- ListingEvent: 新币种上线（含抹茶、合约等）
- DelistingEvent: 币种下架
- MaintenanceEvent: 交易对维护、系统维护
- OtherEvent: 其他公告

设计原则：
- Orchestration Layer 持有 WS Source 和 HTML Source
- 优先使用 WS 源，失败时自动降级到 HTML 源
- 使用 RawAnnouncement 作为统一数据模型
- 事件写入 event_log（stream_key: announcements）
- 幂等写入：基于 dedup_key 去重
- 降级保护：采集失败不影响主交易流程
"""
import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any, AsyncIterator, Union
import httpx

from trader.core.domain.models.events import DomainEvent, EventType
from trader.adapters.announcements.models import (
    AnnouncementType,
    classify_announcement as shared_classify_announcement,
)

logger = logging.getLogger(__name__)


# Re-export AnnouncementType from models for backward compatibility
# _classify_announcement delegates to shared_classify_announcement


@dataclass
class AnnouncementEvent:
    """
    Binance 公告事件
    
    用于结构化存储和去重判断。
    """
    announcement_id: str
    title: str
    content: str
    type: AnnouncementType
    timestamp: datetime
    source_url: str
    symbols: list[str] = field(default_factory=list)  # 涉及的交易对/币种
    
    @property
    def event_type_str(self) -> str:
        return f"ANNOUNCEMENT_{self.type.value}"
    
    @property
    def aggregate_id(self) -> str:
        return f"{self.announcement_id}_{self.type.value}"


class BinanceAnnouncementCrawler:
    """
    Binance 公告爬虫 (Orchestration Layer)
    
    持有 WS Source 和 HTML Source，优先使用 WS，失败时降级到 HTML。
    
    兼容旧 API:
    - fetch_announcements(): 兼容旧版 REST 调用
    - _extract_symbols(): 返回完整交易对 (v2 调整)
    """
    
    # Binance CMS API 端点 (用于 HTML fallback)
    BASE_URL = "https://www.binance.com"
    CMS_API_PATH = "/bapi/earn/v1/public/feign/cms/article/list/query"
    
    # DEPRECATED: 关键词列表已移至 models.classify_announcement
    # 这些类属性保留用于向后兼容，但不再被 _classify_announcement 使用
    LISTING_KEYWORDS: list = []  # type: ignore[assignment]
    DELISTING_KEYWORDS: list = []  # type: ignore[assignment]
    MAINTENANCE_KEYWORDS: list = []  # type: ignore[assignment]
    
    def __init__(
        self,
        event_store: Any,  # EventStoreWithFallback
        http_client: Optional[httpx.AsyncClient] = None,
        poll_interval_seconds: int = 300,  # 5分钟轮询一次
        locale: str = "zh",
        max_concurrent_requests: int = 1,  # 并发请求限制，防止API限流
        # Orchestration Layer 新增参数
        ws_source: Optional[Any] = None,  # BinanceWsAnnouncementSource
        html_source: Optional[Any] = None,  # BinanceHtmlAnnouncementSource
        use_ws_primary: bool = True,  # 是否优先使用 WS
    ):
        """
        初始化公告爬虫
        
        Args:
            event_store: EventStoreWithFallback 实例，用于写入 event_log
            http_client: httpx.AsyncClient 实例（可选，用于测试注入）
            poll_interval_seconds: 轮询间隔（秒）
            locale: 语言偏好（zh/en）
            max_concurrent_requests: 最大并发请求数（默认1，防止API限流）
            ws_source: WS 数据源（可选，自动创建）
            html_source: HTML 数据源（可选，自动创建）
            use_ws_primary: 是否优先使用 WS
        """
        self._event_store = event_store
        self._http_client = http_client
        self._poll_interval = poll_interval_seconds
        self._locale = locale
        self._running = False
        self._last_fetch_time: Optional[datetime] = None
        self._processed_ids: set[str] = set()  # 已处理的公告ID（内存缓存）
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        
        # Orchestration Layer
        self._ws_source = ws_source
        self._html_source = html_source
        self._use_ws_primary = use_ws_primary
    
    def _get_ws_source(self) -> Any:
        """获取或创建 WS Source"""
        if self._ws_source is None:
            from trader.adapters.announcements.ws_source import BinanceWsAnnouncementSource
            self._ws_source = BinanceWsAnnouncementSource()
        return self._ws_source
    
    def _get_html_source(self) -> Any:
        """获取或创建 HTML Source"""
        if self._html_source is None:
            from trader.adapters.announcements.html_source import BinanceHtmlAnnouncementSource
            self._html_source = BinanceHtmlAnnouncementSource(locale=self._locale)
        return self._html_source
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; BinanceAnnouncementCrawler/1.0)",
                    "Accept": "application/json",
                },
            )
        return self._http_client
    
    async def fetch_announcements(self, limit: int = 20) -> list[dict[str, Any]]:
        """
        从 Binance API 获取公告列表
        
        兼容旧 API。优先使用 WS，失败时降级到 HTML。
        
        Args:
            limit: 获取数量
            
        Returns:
            公告列表
            
        Raises:
            httpx.HTTPError: 网络请求失败时降级返回空列表
        """
        # 优先尝试 WS 源
        if self._use_ws_primary:
            ws_source = self._get_ws_source()
            try:
                # Suppress DeprecationWarning: WS fetch_initial is deprecated by design
                import warnings
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=DeprecationWarning)
                    raw_list = await ws_source.fetch_initial(max_results=limit)
                if raw_list:
                    return [self._raw_to_dict(ann) for ann in raw_list]
            except Exception as e:
                logger.warning(
                    "WS_SOURCE_FETCH_FAILED, falling back to HTML",
                    extra={"error": str(e)}
                )
        
        # HTML fallback
        html_results = await self._fetch_from_html(limit)
        if html_results:
            return html_results
        
        # HTML 也失败，返回空列表
        logger.warning("ALL_SOURCES_FAILED: WS and HTML both returned no results")
        return []
    
    async def _fetch_from_html(self, limit: int = 20) -> list[dict[str, Any]]:
        """从 HTML 源获取公告"""
        async with self._semaphore:
            client = await self._get_http_client()
            
            try:
                response = await client.get(
                    self.CMS_API_PATH,
                    params={
                        "type": 1,
                        "locale": self._locale,
                        "limit": limit,
                    },
                )
                response.raise_for_status()
                data = response.json()
                
                if data.get("code") == "000000" and "data" in data:
                    articles = data["data"].get("articles", [])
                    return articles
                else:
                    logger.warning(
                        "BINANCE_CRAWLER_API_ERROR",
                        extra={"code": data.get("code"), "message": data.get("message", "")},
                    )
                    return []
                    
            except httpx.HTTPError as e:
                logger.warning(
                    "BINANCE_CRAWLER_NETWORK_ERROR",
                    extra={"error": str(e), "error_type": type(e).__name__},
                )
                return []
            except Exception as e:
                logger.error(
                    "BINANCE_CRAWLER_UNEXPECTED_ERROR",
                    extra={"error": str(e), "error_type": type(e).__name__},
                )
                return []
    
    def _raw_to_dict(self, raw_ann: Any) -> dict:
        """将 RawAnnouncement 转换为旧版 dict 格式"""
        return {
            "id": raw_ann.announcement_id,
            "title": raw_ann.title,
            "content": raw_ann.body,
            "timestamp": int(raw_ann.publish_time.timestamp() * 1000) if raw_ann.publish_time else 0,
            "locale": raw_ann.locale,
        }
    
    def _classify_announcement(self, title: str, content: str = "") -> AnnouncementType:
        """
        根据标题和内容分类公告
        
        委托给 models.classify_announcement 函数以避免代码重复。
        
        Args:
            title: 公告标题
            content: 公告内容（可选）
            
        Returns:
            AnnouncementType 分类结果
        """
        return shared_classify_announcement(title, content)
    
    def _extract_symbols(self, title: str, content: str = "") -> list[str]:
        """
        从标题和内容中提取完整交易对
        
        v2 调整: 返回完整交易对 (BTCUSDT)，不返回 base asset (BTC)
        
        Args:
            title: 公告标题
            content: 公告内容
            
        Returns:
            完整交易对列表，如 ["BTCUSDT", "ETHUSDT"]
        """
        symbols = []
        text = title + " " + content
        
        # 匹配完整交易对: BTCUSDT, ETHUSDT, BTCBTC, etc.
        # 边界：空白、非ASCII字符、字符串结束、常见标点
        pair_pattern = r'([A-Z]{2,10})(USDT|BTC|ETH|BNB|BUSD)(?=\s|[^\x00-\x7F]|$|[.,!?;:()\[\]{}])'
        matches = re.findall(pair_pattern, text, re.UNICODE)
        for base, quote in matches:
            symbols.append(f"{base}{quote}")
        
        return list(set(symbols))
    
    def _parse_announcement(self, article: dict[str, Any]) -> Optional[AnnouncementEvent]:
        """
        解析单个公告为 AnnouncementEvent
        
        Args:
            article: Binance API 返回的公告数据
            
        Returns:
            AnnouncementEvent 或 None（解析失败时）
        """
        try:
            announcement_id = str(article.get("id", ""))
            title = article.get("title", "")
            content = article.get("content", "")
            
            if not announcement_id or not title:
                return None
            
            # 解析时间戳
            timestamp_str = article.get("timestamp") or article.get("createTime")
            if timestamp_str:
                if isinstance(timestamp_str, (int, float)):
                    timestamp = datetime.fromtimestamp(timestamp_str / 1000, tz=timezone.utc)
                else:
                    timestamp = datetime.fromisoformat(timestamp_str)
            else:
                timestamp = datetime.now(timezone.utc)
            
            # 分类
            ann_type = self._classify_announcement(title, content)
            
            # 提取交易对
            symbols = self._extract_symbols(title, content)
            
            return AnnouncementEvent(
                announcement_id=announcement_id,
                title=title,
                content=content[:500],
                type=ann_type,
                timestamp=timestamp,
                source_url=f"https://www.binance.com/zh-CN/support/announcement/detail/{announcement_id}",
                symbols=symbols,
            )
            
        except Exception as e:
            logger.warning(
                "BINANCE_CRAWLER_PARSE_ERROR",
                extra={"article_id": article.get("id"), "error": str(e)},
            )
            return None
    
    # AnnouncementType 到 EventType 的映射
    _ANNOUNCEMENT_TO_EVENT_TYPE = {
        AnnouncementType.LISTING: EventType.SIGNAL_GENERATED,
        AnnouncementType.DELISTING: EventType.SIGNAL_GENERATED,
        AnnouncementType.MAINTENANCE: EventType.SIGNAL_GENERATED,
        AnnouncementType.OTHER: EventType.SIGNAL_GENERATED,
    }

    def _create_domain_event(self, ann_event: AnnouncementEvent) -> DomainEvent:
        """
        将 AnnouncementEvent 转换为 DomainEvent
        
        Args:
            ann_event: 公告事件
            
        Returns:
            DomainEvent
        """
        event_type = self._ANNOUNCEMENT_TO_EVENT_TYPE.get(
            ann_event.type, EventType.SIGNAL_GENERATED
        )
        return DomainEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            aggregate_id=ann_event.aggregate_id,
            aggregate_type="Announcement",
            timestamp=ann_event.timestamp,
            data={
                "announcement_id": ann_event.announcement_id,
                "title": ann_event.title,
                "content": ann_event.content,
                "type": ann_event.type.value,
                "event_type_str": ann_event.event_type_str,
                "source_url": ann_event.source_url,
                "symbols": ann_event.symbols,
            },
            metadata={
                "source": "binance_cms_api",
                "local_receive_ts_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
                "exchange_event_ts_ms": int(ann_event.timestamp.timestamp() * 1000),
            },
        )
    
    async def _write_to_event_store(self, ann_event: AnnouncementEvent) -> bool:
        """
        写入事件到 event_store
        
        幂等写入：如果已存在则跳过。
        
        Args:
            ann_event: 公告事件
            
        Returns:
            True 表示写入成功（或已存在），False 表示失败
        """
        try:
            if ann_event.aggregate_id in self._processed_ids:
                return True
            
            stream_key = "announcements"
            latest_seq = await self._event_store.get_latest_seq(stream_key)
            next_seq = (latest_seq + 1) if latest_seq is not None else 0
            
            domain_event = self._create_domain_event(ann_event)
            
            await self._event_store.append_domain_event(
                stream_key=stream_key,
                domain_event=domain_event,
                seq=next_seq,
            )
            
            self._processed_ids.add(ann_event.aggregate_id)
            
            logger.info(
                "ANNOUNCEMENT_EVENT_WRITTEN",
                extra={
                    "announcement_id": ann_event.announcement_id,
                    "type": ann_event.type.value,
                    "title": ann_event.title[:50],
                },
            )
            return True
            
        except Exception as e:
            logger.error(
                "BINANCE_CRAWLER_WRITE_ERROR",
                extra={
                    "announcement_id": ann_event.announcement_id,
                    "error": str(e),
                },
            )
            return False
    
    async def fetch_and_process(self) -> int:
        """
        抓取并处理所有新公告
        
        Returns:
            处理成功的公告数量
        """
        articles = await self.fetch_announcements()
        
        if not articles:
            logger.debug("BINANCE_CRAWLER_NO_ARTICLES")
            return 0
        
        success_count = 0
        
        for article in articles:
            ann_event = self._parse_announcement(article)
            
            if ann_event is None:
                continue
            
            if ann_event.aggregate_id in self._processed_ids:
                continue
            
            if await self._write_to_event_store(ann_event):
                success_count += 1
        
        self._last_fetch_time = datetime.now(timezone.utc)
        
        logger.info(
            "BINANCE_CRAWLER_FETCH_COMPLETE",
            extra={
                "fetched": len(articles),
                "processed": success_count,
                "cached": len(self._processed_ids),
            },
        )
        
        return success_count
    
    # ==================== Orchestration Layer Methods ====================
    
    async def ws_stream(self) -> AsyncIterator[Any]:
        """WebSocket 实时公告流
        
        Yields:
            RawAnnouncement 实例
        """
        ws_source = self._get_ws_source()
        
        if not ws_source.is_connected:
            await ws_source.connect()
        
        if not ws_source.is_subscribed:
            await ws_source.subscribe()
        
        async for ann in ws_source.recv_async_iterator():
            yield ann
    
    async def process_raw_announcement(self, raw_ann: Any) -> bool:
        """处理单个 RawAnnouncement
        
        将 RawAnnouncement 转换为 AnnouncementEvent 并写入 event_store。
        
        Args:
            raw_ann: RawAnnouncement 实例
            
        Returns:
            True 表示处理成功
        """
        # 转换 RawAnnouncement 为 dict
        article = {
            "id": raw_ann.announcement_id,
            "title": raw_ann.title,
            "content": raw_ann.body,
            "timestamp": int(raw_ann.publish_time.timestamp() * 1000) if raw_ann.publish_time else None,
        }
        
        ann_event = self._parse_announcement(article)
        
        if ann_event is None:
            return False
        
        return await self._write_to_event_store(ann_event)
    
    async def close(self) -> None:
        """关闭资源"""
        if self._ws_source:
            try:
                await self._ws_source.disconnect()
            except Exception:
                pass
        
        if self._html_source:
            try:
                await self._html_source.close()
            except Exception:
                pass
        
        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
    
    # ==================== Legacy Methods ====================
    
    async def start_background_polling(self) -> None:
        """启动后台轮询任务"""
        self._running = True
        logger.info(
            "BINANCE_CRAWLER_STARTED",
            extra={"poll_interval_seconds": self._poll_interval},
        )
        
        while self._running:
            try:
                await self.fetch_and_process()
            except Exception as e:
                logger.error(
                    "BINANCE_CRAWLER_POLL_ERROR",
                    extra={"error": str(e)},
                )
            
            await asyncio.sleep(self._poll_interval)
    
    def stop(self) -> None:
        """停止后台轮询"""
        self._running = False
        logger.info("BINANCE_CRAWLER_STOPPED")


# ==================== 便捷函数 ====================

async def crawl_once(event_store: Any) -> int:
    """执行一次公告抓取（便捷函数）"""
    crawler = BinanceAnnouncementCrawler(event_store=event_store)
    try:
        return await crawler.fetch_and_process()
    finally:
        await crawler.close()
