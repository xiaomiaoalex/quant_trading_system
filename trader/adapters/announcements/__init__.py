"""
Announcements Adapter - 事件公告爬虫适配器
=========================================
负责从 Binance 公告源采集事件公告，分类后写入 event_log。

事件分类：
- ListingEvent: 新币种上线公告
- DelistingEvent: 币种下架公告
- MaintenanceEvent: 交易维护公告
- OtherEvent: 其他公告

写入 stream_key: announcements

API-first 架构:
- BinanceWsAnnouncementSource: WebSocket 主数据源
- BinanceHtmlAnnouncementSource: HTML 回退数据源
- BinanceAnnouncementCrawler: Orchestration Layer
- RawAnnouncement: 统一数据模型
"""
from trader.adapters.announcements.binance_crawler import (
    BinanceAnnouncementCrawler,
    AnnouncementEvent,
    crawl_once,
)
from trader.adapters.announcements.models import (
    RawAnnouncement,
    AnnouncementType,
    classify_announcement,
)
from trader.adapters.announcements.ws_source import BinanceWsAnnouncementSource
from trader.adapters.announcements.html_source import BinanceHtmlAnnouncementSource

__all__ = [
    # Core Crawler
    "BinanceAnnouncementCrawler",
    "AnnouncementEvent",
    "crawl_once",
    # Models
    "RawAnnouncement",
    "AnnouncementType",
    "classify_announcement",
    # Sources
    "BinanceWsAnnouncementSource",
    "BinanceHtmlAnnouncementSource",
]
