"""
Binance Announcement Crawler - Binance 公告爬虫
===============================================
从 Binance 官方公告源采集事件公告，分类后写入 event_log。

数据源：
- Binance 官方 RSS Feed: https://www.binance.com/bapi/earn/v1/public/feign/cms/article/list/query
- Binance 公告 API: https://www.binance.com/bapi/earn/v1/public/feign/cms/article/list/query

事件分类：
- ListingEvent: 新币种上线（含抹茶、合约等）
- DelistingEvent: 币种下架
- MaintenanceEvent: 交易对维护、系统维护
- OtherEvent: 其他公告

设计原则：
- Adapter 层允许 IO（网络请求）
- 事件写入 event_log（stream_key: announcements）
- 幂等写入：基于 (announcement_id, event_type) 去重
- 降级保护：采集失败不影响主交易流程
"""
import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any
import httpx

from trader.core.domain.models.events import DomainEvent, EventType

logger = logging.getLogger(__name__)


class AnnouncementType(Enum):
    """公告类型枚举"""
    LISTING = "LISTING"                    # 新币种上线
    DELISTING = "DELISTING"                # 币种下架
    MAINTENANCE = "MAINTENANCE"            # 维护公告
    OTHER = "OTHER"                         # 其他


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
    Binance 公告爬虫
    
    从 Binance API 采集公告，分类后写入 event_log。
    
    使用 httpx async client 遵守 Async-First 规范。
    """
    
    # Binance CMS API 端点
    BASE_URL = "https://www.binance.com"
    CMS_API_PATH = "/bapi/earn/v1/public/feign/cms/article/list/query"
    
    # 公告类型映射关键词
    LISTING_KEYWORDS = [
        r"上线", r"新增", r"上线.*交易", r"new listing", r"launch",
        r"will launch", r"listing", r"add.*trading", r"trading available",
        r"spot", r"margin", r"futures", r"contract",
    ]
    DELISTING_KEYWORDS = [
        r"下架", r"暂停交易", r"delist", r"remove.*trading",
        r"will remove", r"termination", r"停止交易",
    ]
    MAINTENANCE_KEYWORDS = [
        r"维护", r"maintenance", r"system upgrade", r"upgrade",
        r"暂停服务", r"will suspend", r"suspend.*deposit",
        r"提币暂停", r"充值暂停", r"交易暂停",
    ]
    
    def __init__(
        self,
        event_store: Any,  # EventStoreWithFallback
        http_client: Optional[httpx.AsyncClient] = None,
        poll_interval_seconds: int = 300,  # 5分钟轮询一次
        locale: str = "zh",
        max_concurrent_requests: int = 1,  # 并发请求限制，防止API限流
    ):
        """
        初始化公告爬虫
        
        Args:
            event_store: EventStoreWithFallback 实例，用于写入 event_log
            http_client: httpx.AsyncClient 实例（可选，用于测试注入）
            poll_interval_seconds: 轮询间隔（秒）
            locale: 语言偏好（zh/en）
            max_concurrent_requests: 最大并发请求数（默认1，防止API限流）
        """
        self._event_store = event_store
        self._http_client = http_client
        self._poll_interval = poll_interval_seconds
        self._locale = locale
        self._running = False
        self._last_fetch_time: Optional[datetime] = None
        self._processed_ids: set[str] = set()  # 已处理的公告ID（内存缓存）
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
    
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
        
        Args:
            limit: 获取数量
            
        Returns:
            公告列表
            
        Raises:
            httpx.HTTPError: 网络请求失败时降级返回空列表
        """
        # 使用信号量控制并发，防止API限流
        async with self._semaphore:
            client = await self._get_http_client()
            
            try:
                response = await client.get(
                    self.CMS_API_PATH,
                    params={
                        "type": 1,  # 1=公告, 2=活动
                        "locale": self._locale,
                        "limit": limit,
                    },
                )
                response.raise_for_status()
                data = response.json()
                
                # 解析响应结构
                # Binance API 结构: { "code": "000000", "data": { "articles": [...] } }
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
    
    def _classify_announcement(self, title: str, content: str = "") -> AnnouncementType:
        """
        根据标题和内容分类公告
        
        使用关键词匹配进行分类。
        
        Args:
            title: 公告标题
            content: 公告内容（可选）
            
        Returns:
            AnnouncementType 分类结果
        """
        text = (title + " " + content).lower()
        
        # 检查是否下架
        for pattern in self.DELISTING_KEYWORDS:
            if re.search(pattern, text, re.IGNORECASE):
                return AnnouncementType.DELISTING
        
        # 检查是否维护
        for pattern in self.MAINTENANCE_KEYWORDS:
            if re.search(pattern, text, re.IGNORECASE):
                return AnnouncementType.MAINTENANCE
        
        # 检查是否上线
        for pattern in self.LISTING_KEYWORDS:
            if re.search(pattern, text, re.IGNORECASE):
                return AnnouncementType.LISTING
        
        return AnnouncementType.OTHER
    
    def _extract_symbols(self, title: str, content: str = "") -> list[str]:
        """
        从标题和内容中提取涉及的币种/交易对
        
        使用正则匹配常见格式：
        - BTCUSDT, ETHUSDT 等标准交易对
        - BTC, ETH 等币种代码
        
        Args:
            title: 公告标题
            content: 公告内容
            
        Returns:
            币种/交易对列表
        """
        symbols = []
        text = title + " " + content
        
        # 匹配标准交易对格式: XXXUSDT, XXXBTC, XXXBNB
        # 使用正向前瞻(?=...)确保在空白、逗号、字符串结尾或非字母字符后匹配
        # 而不是在匹配后消费这些字符
        pair_pattern = r'([A-Z]{2,10})(?:USDT|BTC|ETH|BNB|BUSD)(?=[\s,]|$|[^A-Z])'
        matches = re.findall(pair_pattern, text)
        symbols.extend(matches)
        
        # 匹配单独币种代码（在特定上下文中）
        # 例如 "上线 BTC" 中的 BTC
        coin_pattern = r'(?:上线|新增|支持|交易)[:\s]+([A-Z]{2,10})'
        coins = re.findall(coin_pattern, text)
        symbols.extend(coins)
        
        # 去重
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
                # Binance 返回毫秒时间戳
                if isinstance(timestamp_str, (int, float)):
                    timestamp = datetime.fromtimestamp(timestamp_str / 1000, tz=timezone.utc)
                else:
                    timestamp = datetime.fromisoformat(timestamp_str)
            else:
                timestamp = datetime.now(timezone.utc)
            
            # 分类
            ann_type = self._classify_announcement(title, content)
            
            # 提取币种
            symbols = self._extract_symbols(title, content)
            
            return AnnouncementEvent(
                announcement_id=announcement_id,
                title=title,
                content=content[:500],  # 截取前500字符避免过大
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
    
    # AnnouncementType 到 EventType 的映射（使用 SIGNAL_GENERATED 作为代理）
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
        # 使用映射的 EventType，保持类型安全
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
                "type": ann_event.type.value,  # 原始公告类型存储在 data 中
                "event_type_str": ann_event.event_type_str,  # 原始字符串类型也保存
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
            # 检查是否已处理（内存缓存）
            if ann_event.aggregate_id in self._processed_ids:
                return True
            
            # 获取当前 stream 最新 seq
            stream_key = "announcements"
            latest_seq = await self._event_store.get_latest_seq(stream_key)
            next_seq = (latest_seq + 1) if latest_seq is not None else 0
            
            # 创建领域事件
            domain_event = self._create_domain_event(ann_event)
            
            # 追加到 event_store
            await self._event_store.append_domain_event(
                stream_key=stream_key,
                domain_event=domain_event,
                seq=next_seq,
            )
            
            # 更新内存缓存
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
            
            # 跳过已处理的
            if ann_event.aggregate_id in self._processed_ids:
                continue
            
            # 写入 event_store
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
    
    async def start_background_polling(self) -> None:
        """
        启动后台轮询任务
        
        持续运行，定期抓取新公告。
        使用 Ctrl+C 或调用 stop() 停止。
        """
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
            
            # 等待下一次轮询
            await asyncio.sleep(self._poll_interval)
    
    def stop(self) -> None:
        """停止后台轮询"""
        self._running = False
        logger.info("BINANCE_CRAWLER_STOPPED")
    
    async def close(self) -> None:
        """关闭资源"""
        self.stop()
        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception:
                pass  # 忽略关闭错误
            self._http_client = None


# ==================== 便捷函数 ====================

async def crawl_once(event_store: Any) -> int:
    """
    执行一次公告抓取（便捷函数）
    
    用于定时任务或一次性同步。
    
    Args:
        event_store: EventStoreWithFallback 实例
        
    Returns:
        处理成功的公告数量
    """
    crawler = BinanceAnnouncementCrawler(event_store=event_store)
    try:
        return await crawler.fetch_and_process()
    finally:
        await crawler.close()
