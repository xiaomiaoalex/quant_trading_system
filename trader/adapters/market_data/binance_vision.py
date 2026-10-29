"""
Binance Vision Archive Adapter
==============================
从币安公开归档（https://data.binance.vision）下载现货 1 分钟 K 线归档。

职责（全部属于 Adapter Plane，只做外部 IO + 格式转换）：
1. 构造固定归档 URL（不随 BINANCE_ENV 变化，不接受任意 URL）
2. 流式下载 ZIP 到临时文件，同时计算 SHA-256
3. 下载官方 .CHECKSUM 并比对
4. 校验通过后原子提交到 MARKET_DATA_ROOT 下的版本化路径
5. 只读取归档内预期的 CSV 成员（不做任意 extractall）

Core 无 IO：解析与校验逻辑在 `trader.core.domain.services.ohlcv_archive`。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

import aiohttp

from trader.core.domain.services.ohlcv_archive import (
    ARCHIVE_SOURCE_NAME,
    ARCHIVE_SUPPORTED_INTERVAL,
    ARCHIVE_SUPPORTED_MARKET,
    ARCHIVE_SUPPORTED_SYMBOLS,
    KlineBar,
    OhlcvRequestError,
    detect_header,
    parse_kline_fields,
    parse_month,
)

BINANCE_VISION_BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
BINANCE_VISION_BASE_URL_ENV = "BINANCE_VISION_BASE_URL"
MARKET_DATA_ROOT_ENV = "MARKET_DATA_ROOT"

_ARCHIVE_SOURCE_DIRECTORY = os.path.join("binance", "spot", "raw", "klines")
_DOWNLOAD_CHUNK_SIZE = 1024 * 256


class ArchiveError(RuntimeError):
    """归档下载 / 解析错误基类。"""


class ArchiveNotFoundError(ArchiveError):
    """归档不存在（HTTP 404）。"""


class ArchiveDownloadError(ArchiveError):
    """归档下载失败（网络、超时、5xx）。"""


class ArchiveChecksumMismatch(ArchiveError):
    """归档 SHA-256 与官方 CHECKSUM 不一致。"""


class ArchiveContentError(ArchiveError):
    """归档内容结构不符合预期（缺少预期 CSV 成员等）。"""


@dataclass(frozen=True, slots=True)
class ArchiveRef:
    """一个待下载归档的定位信息。"""

    symbol: str
    interval: str
    month: str
    zip_url: str
    checksum_url: str
    file_stem: str

    @property
    def csv_member_name(self) -> str:
        """归档内预期的 CSV 成员名。"""
        return f"{self.file_stem}.csv"


@dataclass(slots=True)
class DownloadedArchive:
    """已下载（尚未提交）的归档。"""

    ref: ArchiveRef
    temp_path: str
    size_bytes: int
    sha256: str
    official_sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveHttpResponse:
    """HTTP 响应（bytes 形式）。"""

    status: int
    body: bytes


def default_market_data_root() -> str:
    """
    行情根目录。

    优先级：``MARKET_DATA_ROOT`` 环境变量 > ``<项目根>/data/market``。
    """
    configured = os.environ.get(MARKET_DATA_ROOT_ENV, "").strip()
    if configured:
        return str(Path(configured).expanduser().resolve())
    project_root = Path(__file__).resolve().parents[3]
    return str((project_root / "data" / "market").resolve())


def build_archive_ref(
    symbol: str,
    interval: str = ARCHIVE_SUPPORTED_INTERVAL,
    month: str = "",
    base_url: Optional[str] = None,
) -> ArchiveRef:
    """
    构造归档定位信息。

    只允许 BTCUSDT / ETHUSDT + 1m + 现货；不接受任意 URL。
    """
    normalized_symbol = (symbol or "").strip().upper()
    if normalized_symbol not in ARCHIVE_SUPPORTED_SYMBOLS:
        raise OhlcvRequestError(
            f"Unsupported symbol '{symbol}'. Supported: {', '.join(ARCHIVE_SUPPORTED_SYMBOLS)}"
        )
    normalized_interval = (interval or ARCHIVE_SUPPORTED_INTERVAL).strip()
    if normalized_interval != ARCHIVE_SUPPORTED_INTERVAL:
        raise OhlcvRequestError(
            f"Unsupported interval '{interval}'. Only '{ARCHIVE_SUPPORTED_INTERVAL}' is available"
        )
    year, month_number = parse_month(month)
    normalized_month = f"{year:04d}-{month_number:02d}"

    root = base_url or os.environ.get(BINANCE_VISION_BASE_URL_ENV) or BINANCE_VISION_BASE_URL
    root = root.rstrip("/")
    file_stem = f"{normalized_symbol}-{normalized_interval}-{normalized_month}"
    zip_url = f"{root}/{normalized_symbol}/{normalized_interval}/{file_stem}.zip"
    checksum_url = f"{zip_url}.CHECKSUM"
    return ArchiveRef(
        symbol=normalized_symbol,
        interval=normalized_interval,
        month=normalized_month,
        zip_url=zip_url,
        checksum_url=checksum_url,
        file_stem=file_stem,
    )


class AiohttpArchiveHttpClient:
    """基于 aiohttp 的归档下载客户端（真实网络）。"""

    def __init__(
        self,
        timeout: float = 60.0,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self._timeout = timeout
        self._session = session
        self._owns_session = session is None

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def get_bytes(self, url: str) -> ArchiveHttpResponse:
        session = await self._ensure_session()
        try:
            async with session.get(url) as response:
                body = await response.read()
                return ArchiveHttpResponse(status=response.status, body=body)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ArchiveDownloadError(f"Failed to fetch {url}: {exc}") from exc

    async def head_status(self, url: str) -> int:
        """轻量探测：返回 HEAD 状态码（用于判断归档是否存在）。"""
        session = await self._ensure_session()
        try:
            async with session.head(url) as response:
                return response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ArchiveDownloadError(f"Failed to probe {url}: {exc}") from exc

    async def download_to_file(self, url: str, destination: str) -> int:
        """流式下载到本地文件，返回写入字节数。"""
        session = await self._ensure_session()
        written = 0
        try:
            async with session.get(url) as response:
                if response.status == 404:
                    raise ArchiveNotFoundError(f"Archive not found: {url}")
                if response.status >= 400:
                    raise ArchiveDownloadError(
                        f"Archive download failed with status {response.status}: {url}"
                    )
                with open(destination, "wb") as handle:
                    async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
                        handle.write(chunk)
                        written += len(chunk)
        except ArchiveError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ArchiveDownloadError(f"Failed to download {url}: {exc}") from exc
        return written


class BinanceVisionArchiveClient:
    """
    归档下载与读取客户端。

    - 下载到临时文件，校验 SHA-256 通过后再原子提交
    - 归档文件名带版本号，永远不覆盖已被数据集引用的文件
    """

    def __init__(
        self,
        http_client: AiohttpArchiveHttpClient,
        market_data_root: Optional[str] = None,
    ) -> None:
        self._http = http_client
        self._market_data_root = market_data_root or default_market_data_root()

    @property
    def market_data_root(self) -> str:
        return self._market_data_root

    def archive_dir(self, symbol: str, interval: str = ARCHIVE_SUPPORTED_INTERVAL) -> str:
        """归档目录：<root>/binance/spot/raw/klines/<symbol>/<interval>/"""
        return os.path.join(
            self._market_data_root,
            _ARCHIVE_SOURCE_DIRECTORY,
            symbol,
            interval,
        )

    def archive_path(self, symbol: str, interval: str, month: str, version: int) -> str:
        """版本化归档文件路径（不同版本互不覆盖）。"""
        directory = self.archive_dir(symbol, interval)
        return os.path.join(directory, f"{symbol}-{interval}-{month}-v{version}.zip")

    def checksum_path(self, symbol: str, interval: str, month: str, version: int) -> str:
        return f"{self.archive_path(symbol, interval, month, version)}.CHECKSUM"

    async def download(self, ref: ArchiveRef) -> DownloadedArchive:
        """
        下载归档到临时文件并校验 SHA-256。

        Raises:
            ArchiveNotFoundError: 归档或 CHECKSUM 不存在
            ArchiveDownloadError: 网络 / 超时 / 5xx
            ArchiveChecksumMismatch: 实际 SHA-256 与官方 CHECKSUM 不一致
        """
        directory = self.archive_dir(ref.symbol, ref.interval)
        os.makedirs(directory, exist_ok=True)
        temp_dir = os.path.join(directory, ".tmp")
        os.makedirs(temp_dir, exist_ok=True)

        checksum_response = await self._http.get_bytes(ref.checksum_url)
        if checksum_response.status == 404:
            raise ArchiveNotFoundError(f"Archive checksum not found: {ref.checksum_url}")
        if checksum_response.status >= 400:
            raise ArchiveDownloadError(
                f"Checksum request failed with status {checksum_response.status}: "
                f"{ref.checksum_url}"
            )
        official_sha = parse_checksum(checksum_response.body.decode("utf-8", errors="replace"))

        temp_path = os.path.join(temp_dir, f"{ref.file_stem}.{uuid.uuid4().hex[:8]}.part")
        try:
            size = await self._http.download_to_file(ref.zip_url, temp_path)
        except ArchiveNotFoundError:
            _safe_remove(temp_path)
            raise
        except ArchiveError:
            _safe_remove(temp_path)
            raise

        actual_sha = sha256_file(temp_path)
        if actual_sha.lower() != official_sha.lower():
            _safe_remove(temp_path)
            raise ArchiveChecksumMismatch(
                f"Archive checksum mismatch for {ref.zip_url}: "
                f"expected {official_sha.lower()}, got {actual_sha.lower()}"
            )

        return DownloadedArchive(
            ref=ref,
            temp_path=temp_path,
            size_bytes=size,
            sha256=actual_sha.lower(),
            official_sha256=official_sha.lower(),
        )

    def commit(
        self,
        downloaded: DownloadedArchive,
        version: int,
        official_checksum_text: Optional[str] = None,
    ) -> str:
        """把临时文件原子提交为版本化归档文件，返回最终路径。"""
        ref = downloaded.ref
        target = self.archive_path(ref.symbol, ref.interval, ref.month, version)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            # 目标文件已存在（同一 hash 的重复任务），直接复用，不覆盖。
            _safe_remove(downloaded.temp_path)
            return target
        os.replace(downloaded.temp_path, target)
        checksum_target = self.checksum_path(ref.symbol, ref.interval, ref.month, version)
        checksum_text = official_checksum_text or (
            f"{downloaded.official_sha256}  {ref.file_stem}.zip\n"
        )
        with open(checksum_target, "w", encoding="utf-8") as handle:
            handle.write(checksum_text)
        return target

    def read_klines(self, archive_path: str, ref: ArchiveRef) -> List[KlineBar]:
        """
        读取归档内预期 CSV 成员并解析为 K 线。

        只读取 ``<symbol>-1m-<month>.csv``，不做任意路径解压。
        """
        return read_klines_from_archive(archive_path, ref)

    async def exists(self, ref: ArchiveRef) -> bool:
        """
        探测归档是否存在（HEAD 官方 CHECKSUM 地址）。

        网络异常时抛 ArchiveDownloadError，由调用方决定如何降级。
        """
        status = await self._http.head_status(ref.checksum_url)
        return status < 400

    async def close(self) -> None:
        """关闭底层 HTTP 会话。"""
        close = getattr(self._http, "close", None)
        if callable(close):
            await close()


def read_klines_from_archive(archive_path: str, ref: ArchiveRef) -> List[KlineBar]:
    """同步读取 ZIP 内预期 CSV 成员（应在线程中调用）。"""
    if not os.path.exists(archive_path):
        raise ArchiveContentError(f"Archive file not found: {archive_path}")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            member = ref.csv_member_name
            if member not in names:
                csv_members = [name for name in names if name.endswith(".csv")]
                raise ArchiveContentError(
                    f"Expected CSV member '{member}' not found in archive. "
                    f"Available CSV members: {csv_members}"
                )
            with archive.open(member) as handle:
                payload = handle.read()
    except zipfile.BadZipFile as exc:
        raise ArchiveContentError(f"Archive is not a valid zip file: {archive_path}") from exc

    return parse_klines_csv(payload.decode("utf-8"))


def parse_klines_csv(text: str) -> List[KlineBar]:
    """解析归档 CSV 文本为 K 线（兼容有表头 / 无表头）。"""
    bars: List[KlineBar] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = [item.strip() for item in line.split(",")]
        if detect_header(fields):
            continue
        bars.append(parse_kline_fields(fields))
    return bars


def parse_checksum(text: str) -> str:
    """解析 ``sha256sum`` 格式的 CHECKSUM 文件内容。"""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        token = line.split()[0].lower()
        if len(token) != 64:
            raise ArchiveChecksumMismatch(f"Invalid CHECKSUM content: {raw_line!r}")
        int(token, 16)  # 校验是否为合法 hex
        return token
    raise ArchiveChecksumMismatch("CHECKSUM file is empty")


def sha256_file(path: str) -> str:
    """计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def copy_archive(source: str, destination: str) -> None:
    """复制归档文件（供测试/离线导入复用）。"""
    shutil.copyfile(source, destination)


def supported_symbols() -> Sequence[str]:
    return ARCHIVE_SUPPORTED_SYMBOLS


__all__ = [
    "ARCHIVE_SOURCE_NAME",
    "ARCHIVE_SUPPORTED_MARKET",
    "AiohttpArchiveHttpClient",
    "ArchiveChecksumMismatch",
    "ArchiveContentError",
    "ArchiveDownloadError",
    "ArchiveError",
    "ArchiveHttpResponse",
    "ArchiveNotFoundError",
    "ArchiveRef",
    "BinanceVisionArchiveClient",
    "build_archive_ref",
    "default_market_data_root",
    "parse_checksum",
    "parse_klines_csv",
    "read_klines_from_archive",
    "sha256_file",
]
