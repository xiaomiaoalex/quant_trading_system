"""
Binance HTML Announcement Source - Binance HTML 公告源
====================================================
使用 Binance HTML 页面作为回退数据源采集公告。

用于:
- WS 源失败时的 fallback
- 初始数据 backfill
- 必须实现 fetch_initial
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional
import httpx

from trader.adapters.announcements.models import RawAnnouncement

logger = logging.getLogger(__name__)


class BinanceHtmlAnnouncementSource:
    """Binance HTML 公告数据源
    
    作为 WebSocket 源的回退源，从 Binance HTML 页面解析公告。
    必须实现 fetch_initial 方法。
    """
    
    # Binance 公告页面
    BASE_URL = "https://www.binance.com"
    LIST_URL = "/zh-CN/support/announcement"
    DETAIL_URL = "/zh-CN/support/announcement/detail/{announcement_id}"
    
    # API 端点（用于获取列表数据）
    CMS_API_PATH = "/bapi/earn/v1/public/feign/cms/article/list/query"
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        locale: str = "zh",
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        """
        初始化 HTML 公告源
        
        Args:
            base_url: 基础 URL
            locale: 语言偏好
            http_client: httpx.AsyncClient 实例
        """
        self._base_url = base_url or self.BASE_URL
        self._locale = locale
        self._http_client = http_client
        self._semaphore = asyncio.Semaphore(1)  # 并发请求限制
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; BinanceAnnouncementSource/1.0)",
                    "Accept": "application/json, text/html",
                },
            )
        return self._http_client
    
    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
    
    async def fetch_initial(self, max_results: int = 100) -> list[RawAnnouncement]:
        """获取初始公告列表
        
        必须实现此方法，用于:
        - WS 源失败时的 fallback
        - 初始数据 backfill
        
        Args:
            max_results: 最大结果数
            
        Returns:
            RawAnnouncement 列表
        """
        async with self._semaphore:
            client = await self._get_http_client()
            
            try:
                response = await client.get(
                    self.CMS_API_PATH,
                    params={
                        "type": 1,  # 1=公告, 2=活动
                        "locale": self._locale,
                        "limit": max_results,
                    },
                )
                response.raise_for_status()
                data = response.json()
                
                if data.get("code") == "000000" and "data" in data:
                    articles = data["data"].get("articles", [])
                    return [self._parse_article(article) for article in articles]
                else:
                    logger.warning(
                        "HTML_SOURCE_API_ERROR",
                        extra={"code": data.get("code"), "message": data.get("message", "")}
                    )
                    return []
                    
            except httpx.HTTPError as e:
                logger.error("HTML_SOURCE_NETWORK_ERROR", extra={"error": str(e)})
                return []
            except Exception as e:
                logger.error("HTML_SOURCE_UNEXPECTED_ERROR", extra={"error": str(e)})
                return []
    
    def _parse_article(self, article: dict) -> RawAnnouncement:
        """解析 API 文章为 RawAnnouncement
        
        Args:
            article: Binance API 返回的文章数据
            
        Returns:
            RawAnnouncement 实例
        """
        announcement_id = str(article.get("id", ""))
        
        # 解析时间戳
        timestamp_str = article.get("timestamp") or article.get("createTime")
        publish_time = None
        if timestamp_str:
            if isinstance(timestamp_str, (int, float)):
                publish_time = datetime.fromtimestamp(timestamp_str / 1000, tz=timezone.utc)
            else:
                try:
                    dt = datetime.fromisoformat(timestamp_str)
                    # 确保返回 UTC-aware datetime，与 models.py 保持一致
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    publish_time = dt
                except ValueError:
                    pass
        
        # 构建详情 URL
        detail_url = f"{self._base_url}/zh-CN/support/announcement/detail/{announcement_id}"
        
        return RawAnnouncement(
            catalog_id=article.get("catalogId"),
            announcement_id=announcement_id,
            title=article.get("title"),
            body=article.get("content"),
            publish_time=publish_time,
            detail_url=detail_url,
            locale=article.get("locale", self._locale),
            source="html",
            external_id=article.get("externalId"),
            disclaimer=None,
        )
    
    def _extract_symbols(self, title: str, content: str = "") -> list[str]:
        """从标题和内容中提取完整交易对
        
        返回格式: BTCUSDT, ETHUSDT (完整交易对)
        
        Args:
            title: 标题
            content: 内容
            
        Returns:
            完整交易对列表
        """
        symbols = []
        text = title + " " + content
        
        # 匹配完整交易对: BTCUSDT, ETHUSDT, BTCBTC, etc.
        # 边界：空白、非ASCII字符、字符串结束、常见标点
        # 与 binance_crawler.py 保持一致
        pair_pattern = r'([A-Z]{2,10})(USDT|BTC|ETH|BNB|BUSD)(?=\s|[^\x00-\x7F]|$|[.,!?;:()\[\]{}])'
        matches = re.findall(pair_pattern, text, re.UNICODE)
        for base, quote in matches:
            symbols.append(f"{base}{quote}")
        
        return list(set(symbols))
    
    def _classify_announcement(self, title: str, content: str = ""):
        """根据标题和内容分类公告
        
        委托给 models.classify_announcement 以避免代码重复。
        
        Args:
            title: 标题
            content: 内容
            
        Returns:
            AnnouncementType
        """
        from trader.adapters.announcements.models import classify_announcement
        return classify_announcement(title, content)
    
    async def fetch_detail(self, announcement_id: str) -> Optional[RawAnnouncement]:
        """获取公告详情
        
        Args:
            announcement_id: 公告ID
            
        Returns:
            RawAnnouncement 或 None
        """
        async with self._semaphore:
            client = await self._get_http_client()
            
            try:
                # 尝试通过 API 获取详情
                response = await client.get(
                    self.CMS_API_PATH,
                    params={
                        "type": 1,
                        "locale": self._locale,
                        "limit": 100,
                    },
                )
                response.raise_for_status()
                data = response.json()
                
                if data.get("code") == "000000" and "data" in data:
                    articles = data["data"].get("articles", [])
                    for article in articles:
                        if str(article.get("id", "")) == announcement_id:
                            return self._parse_article(article)
                
                return None
                
            except Exception as e:
                logger.error(
                    "HTML_SOURCE_FETCH_DETAIL_ERROR",
                    extra={"announcement_id": announcement_id, "error": str(e)}
                )
                return None
    
    def parse_html_list(self, html: str) -> list[RawAnnouncement]:
        """解析 HTML 公告列表
        
        Args:
            html: HTML 字符串
            
        Returns:
            RawAnnouncement 列表
        """
        announcements = []
        
        # 简单的 HTML 解析（实际使用时建议使用 BeautifulSoup）
        # 匹配公告项
        item_pattern = r'<div[^>]*class="announce-item"[^>]*>(.*?)</div>'
        items = re.findall(item_pattern, html, re.DOTALL | re.IGNORECASE)
        
        for item in items:
            title_match = re.search(r'<h[13]>([^<]+)</h[13]>', item, re.IGNORECASE)
            link_match = re.search(r'href="([^"]+)"', item, re.IGNORECASE)
            content_match = re.search(r'<p[^>]*>([^<]+)</p>', item, re.IGNORECASE)
            
            if title_match:
                title = title_match.group(1).strip()
                detail_url = link_match.group(1) if link_match else ""
                content = content_match.group(1).strip() if content_match else ""
                
                announcements.append(RawAnnouncement(
                    title=title,
                    body=content,
                    detail_url=detail_url,
                    locale=self._locale,
                    source="html",
                ))
        
        return announcements
    
    def parse_html_detail(self, html: str, detail_url: str) -> Optional[RawAnnouncement]:
        """解析 HTML 公告详情
        
        Args:
            html: HTML 字符串
            detail_url: 详情 URL
            
        Returns:
            RawAnnouncement 或 None
        """
        # 提取标题
        title_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
        if not title_match:
            title_match = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
        
        title = title_match.group(1).strip() if title_match else None
        
        # 提取内容
        content_match = re.search(
            r'<div[^>]*class="content"[^>]*>(.*?)</div>',
            html, re.DOTALL | re.IGNORECASE
        )
        if not content_match:
            content_match = re.search(
                r'<div[^>]*class="detail"[^>]*>(.*?)</div>',
                html, re.DOTALL | re.IGNORECASE
            )
        
        body = content_match.group(1).strip() if content_match else None
        
        # 清理 HTML 标签
        if body:
            body = re.sub(r'<[^>]+>', '', body)
            body = re.sub(r'\s+', ' ', body).strip()
        
        if not title:
            return None
        
        return RawAnnouncement(
            title=title,
            body=body,
            detail_url=detail_url,
            locale=self._locale,
            source="html",
        )
    
    async def get_announcement_updates(self):
        """获取公告更新流
        
        HTML 源不支持实时更新，抛出异常。
        """
        raise NotImplementedError("HTML source does not support real-time updates")
