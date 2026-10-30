"""Market Data Adapters - 外部历史行情数据适配器。"""

from trader.adapters.market_data.binance_vision import (
    ArchiveChecksumMismatch,
    ArchiveContentError,
    ArchiveDownloadError,
    ArchiveError,
    ArchiveNotFoundError,
    ArchiveRef,
    BinanceVisionArchiveClient,
    build_archive_ref,
    default_market_data_root,
)

__all__ = [
    "ArchiveChecksumMismatch",
    "ArchiveContentError",
    "ArchiveDownloadError",
    "ArchiveError",
    "ArchiveNotFoundError",
    "ArchiveRef",
    "BinanceVisionArchiveClient",
    "build_archive_ref",
    "default_market_data_root",
]
