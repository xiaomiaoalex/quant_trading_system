"""
Announcement Models - 公告数据模型
===================================
定义 API-first 架构中的原始公告数据模型。

所有字段均为 Optional，以适应 WS 和 HTML 不同数据源的差异。
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import hashlib
import re


class AnnouncementType(Enum):
    """公告类型枚举"""
    LISTING = "LISTING"  # 新币种上线
    DELISTING = "DELISTING"  # 下架
    MAINTENANCE = "MAINTENANCE"  # 维护
    OTHER = "OTHER"  # 其他


def classify_announcement(title: str, content: str = "") -> AnnouncementType:
    """
    根据标题和内容分类公告
    
    这是共享的分类逻辑，被 binance_crawler.py 和 html_source.py 共用。
    
    Args:
        title: 公告标题
        content: 公告内容（可选）
        
    Returns:
        AnnouncementType
    """
    text = (title + " " + content).lower()
    
    # 下架关键词
    delisting_patterns = [
        r"下架", r"暂停交易", r"delist", r"remove.*trading",
        r"will remove", r"termination", r"停止交易",
    ]
    for pattern in delisting_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return AnnouncementType.DELISTING
    
    # 维护关键词
    maintenance_patterns = [
        r"维护", r"maintenance", r"system upgrade", r"upgrade",
        r"暂停服务", r"will suspend", r"suspend.*deposit",
        r"提币暂停", r"充值暂停", r"交易暂停",
    ]
    for pattern in maintenance_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return AnnouncementType.MAINTENANCE
    
    # 上线关键词
    listing_patterns = [
        r"上线", r"新增", r"上线.*交易", r"new listing", r"launch",
        r"will launch", r"listing", r"add.*trading", r"trading available",
        r"spot", r"margin", r"futures", r"contract",
    ]
    for pattern in listing_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return AnnouncementType.LISTING
    
    return AnnouncementType.OTHER


@dataclass
class RawAnnouncement:
    """原始公告数据模型（API-first 架构）
    
    所有字段均为 Optional，以适应 WS 和 HTML 不同数据源的差异。
    
    Attributes:
        catalog_id: 目录ID
        announcement_id: 公告ID
        title: 标题
        body: 正文
        publish_time: 发布时间
        detail_url: 详情URL
        locale: 语言
        source: 来源: "ws" | "html"
        external_id: 外部系统ID
        disclaimer: 免责声明
    """
    # 核心字段
    catalog_id: Optional[str] = None
    announcement_id: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    publish_time: Optional[datetime] = None
    detail_url: Optional[str] = None
    locale: Optional[str] = None
    source: Optional[str] = None  # "ws" | "html"
    
    # 扩展字段
    external_id: Optional[str] = None  # 外部系统ID
    disclaimer: Optional[str] = None   # 免责声明
    
    @property
    def dedup_key(self) -> str:
        """去重键：优先使用 detail_url tail
        
        优先级:
        1. detail_url 的尾部（最后一个路径段）
        2. 否则 sha256(catalog_id|publish_time|title|body[:200])
        """
        if self.detail_url:
            tail = self.detail_url.rstrip("/").split("/")[-1]
            if tail and len(tail) > 8:
                return tail
        
        # Fallback: 内容 hash
        content = "|".join([
            self.catalog_id or "",
            self.publish_time.isoformat() if self.publish_time else "",
            self.title or "",
            (self.body or "")[:200]
        ])
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "catalog_id": self.catalog_id,
            "announcement_id": self.announcement_id,
            "title": self.title,
            "body": self.body,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
            "detail_url": self.detail_url,
            "locale": self.locale,
            "source": self.source,
            "external_id": self.external_id,
            "disclaimer": self.disclaimer,
            "dedup_key": self.dedup_key,
        }
    
    @classmethod
    def from_ws_message(cls, msg: dict) -> "RawAnnouncement":
        """从 WebSocket 消息创建 RawAnnouncement
        
        Args:
            msg: WebSocket 消息字典
            
        Returns:
            RawAnnouncement 实例
        """
        # Binance WS 公告消息结构示例:
        # {
        #     "command": "SUBSCRIBE",
        #     "value": "com_announcement_en",
        #     "data": {
        #         "id": "...",
        #         "title": "...",
        #         "content": "...",
        #         "publishTime": 1234567890000,
        #         ...
        #     }
        # }
        
        data = msg.get("data", msg)
        
        publish_time = None
        if data.get("publishTime"):
            ts = data.get("publishTime")
            if isinstance(ts, (int, float)):
                # Use UTC to ensure deterministic timezone-independent timestamps
                publish_time = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        
        return cls(
            catalog_id=data.get("catalogId"),
            announcement_id=data.get("id") or data.get("announcementId"),
            title=data.get("title"),
            body=data.get("content") or data.get("body"),
            publish_time=publish_time,
            detail_url=data.get("detailUrl") or data.get("detail_url"),
            locale=data.get("locale"),
            source="ws",
            external_id=data.get("externalId") or data.get("external_id"),
            disclaimer=data.get("disclaimer"),
        )
    
    @classmethod
    def from_html_parse(
        cls,
        title: str,
        body: str,
        detail_url: str,
        publish_time: Optional[datetime] = None,
        locale: str = "zh",
        catalog_id: Optional[str] = None,
        announcement_id: Optional[str] = None,
    ) -> "RawAnnouncement":
        """从 HTML 解析结果创建 RawAnnouncement
        
        Args:
            title: 标题
            body: 正文
            detail_url: 详情URL
            publish_time: 发布时间
            locale: 语言
            catalog_id: 目录ID
            announcement_id: 公告ID
            
        Returns:
            RawAnnouncement 实例
        """
        return cls(
            catalog_id=catalog_id,
            announcement_id=announcement_id,
            title=title,
            body=body,
            publish_time=publish_time,
            detail_url=detail_url,
            locale=locale,
            source="html",
        )
