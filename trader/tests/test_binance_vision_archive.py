"""
Binance Vision Archive Adapter Tests
=====================================
覆盖归档 URL 构造、下载校验（SHA-256）、原子提交与 ZIP 内 CSV 解析。

所有 HTTP 交互都由 fake 客户端提供，测试不访问网络。
"""

from __future__ import annotations

import hashlib
import io
import os
import zipfile

import pytest

from trader.adapters.market_data.binance_vision import (
    ArchiveChecksumMismatch,
    ArchiveContentError,
    ArchiveDownloadError,
    ArchiveHttpResponse,
    ArchiveNotFoundError,
    BinanceVisionArchiveClient,
    build_archive_ref,
    default_market_data_root,
    parse_checksum,
    parse_klines_csv,
    read_klines_from_archive,
    sha256_file,
)
from trader.core.domain.services.ohlcv_archive import OhlcvRequestError


class FakeArchiveHttp:
    """脚本化的归档 HTTP 客户端（不访问网络）。"""

    def __init__(
        self,
        *,
        zip_bytes: bytes = b"",
        checksum_text: str = "",
        zip_status: int = 200,
        checksum_status: int = 200,
        download_error: Exception | None = None,
    ) -> None:
        self._zip_bytes = zip_bytes
        self._checksum_text = checksum_text
        self._zip_status = zip_status
        self._checksum_status = checksum_status
        self._download_error = download_error
        self.requested_urls: list[str] = []

    async def get_bytes(self, url: str) -> ArchiveHttpResponse:
        self.requested_urls.append(url)
        return ArchiveHttpResponse(status=self._checksum_status, body=self._checksum_text.encode())

    async def download_to_file(self, url: str, destination: str) -> int:
        self.requested_urls.append(url)
        if self._download_error is not None:
            raise self._download_error
        if self._zip_status == 404:
            raise ArchiveNotFoundError(f"Archive not found: {url}")
        if self._zip_status >= 400:
            raise ArchiveDownloadError(f"status {self._zip_status}: {url}")
        with open(destination, "wb") as handle:
            handle.write(self._zip_bytes)
        return len(self._zip_bytes)


def _csv_rows(count: int, start_ms: int = 1704067200000) -> str:
    lines = []
    for index in range(count):
        ts_ms = start_ms + index * 60_000
        lines.append(f"{ts_ms},100.0,101.0,99.0,100.5,12.5,{ts_ms + 59_999},0,10,0,0,0")
    return "\n".join(lines) + "\n"


def _make_zip(member_name: str, payload: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, payload)
    return buffer.getvalue()


def _client(http: FakeArchiveHttp, root: str) -> BinanceVisionArchiveClient:
    return BinanceVisionArchiveClient(http, market_data_root=root)


def test_build_archive_ref_uses_fixed_binance_vision_url():
    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    assert ref.zip_url == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip"
    )
    assert ref.checksum_url == ref.zip_url + ".CHECKSUM"
    assert ref.csv_member_name == "BTCUSDT-1m-2026-09.csv"


def test_build_archive_ref_accepts_ethusdt():
    ref = build_archive_ref("ethusdt", "1m", "2026-09")
    assert ref.symbol == "ETHUSDT"


def test_build_archive_ref_rejects_unsupported_symbol():
    with pytest.raises(OhlcvRequestError):
        build_archive_ref("SOLUSDT", "1m", "2026-09")


def test_build_archive_ref_rejects_unsupported_interval():
    with pytest.raises(OhlcvRequestError):
        build_archive_ref("BTCUSDT", "1h", "2026-09")


def test_build_archive_ref_rejects_invalid_month():
    with pytest.raises(OhlcvRequestError):
        build_archive_ref("BTCUSDT", "1m", "2026-13")


def test_default_market_data_root_uses_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKET_DATA_ROOT", str(tmp_path))
    assert default_market_data_root() == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_download_verifies_checksum_and_returns_hash(tmp_path):
    payload = _make_zip("BTCUSDT-1m-2026-09.csv", _csv_rows(3))
    digest = hashlib.sha256(payload).hexdigest()
    http = FakeArchiveHttp(zip_bytes=payload, checksum_text=f"{digest}  BTCUSDT-1m-2026-09.zip\n")
    client = _client(http, str(tmp_path))

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    downloaded = await client.download(ref)

    assert downloaded.sha256 == digest
    assert downloaded.official_sha256 == digest
    assert downloaded.size_bytes == len(payload)
    assert os.path.exists(downloaded.temp_path)


@pytest.mark.asyncio
async def test_download_rejects_checksum_mismatch(tmp_path):
    payload = _make_zip("BTCUSDT-1m-2026-09.csv", _csv_rows(3))
    http = FakeArchiveHttp(
        zip_bytes=payload,
        checksum_text=f"{'0' * 64}  BTCUSDT-1m-2026-09.zip\n",
    )

    client = _client(http, str(tmp_path))

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    with pytest.raises(ArchiveChecksumMismatch):
        await client.download(ref)


@pytest.mark.asyncio
async def test_download_reports_missing_archive(tmp_path):
    http = FakeArchiveHttp(checksum_status=404)
    client = _client(http, str(tmp_path))
    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    with pytest.raises(ArchiveNotFoundError):
        await client.download(ref)


@pytest.mark.asyncio
async def test_download_reports_network_failure(tmp_path):
    http = FakeArchiveHttp(
        checksum_text=f"{'a' * 64}  BTCUSDT-1m-2026-09.zip\n",
        download_error=ArchiveDownloadError("timeout"),
    )
    client = _client(http, str(tmp_path))
    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    with pytest.raises(ArchiveDownloadError):
        await client.download(ref)


@pytest.mark.asyncio
async def test_commit_moves_temp_file_to_versioned_path(tmp_path):
    payload = _make_zip("BTCUSDT-1m-2026-09.csv", _csv_rows(3))
    digest = hashlib.sha256(payload).hexdigest()
    http = FakeArchiveHttp(
        zip_bytes=payload,
        checksum_text=f"{digest}  BTCUSDT-1m-2026-09.zip\n",
    )
    client = _client(http, str(tmp_path))

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    downloaded = await client.download(ref)
    target = client.commit(downloaded, version=1)

    assert os.path.exists(target)
    assert target.endswith("BTCUSDT-1m-2026-09-v1.zip")
    assert not os.path.exists(downloaded.temp_path)
    assert sha256_file(target) == digest
    assert os.path.exists(f"{target}.CHECKSUM")


@pytest.mark.asyncio
async def test_commit_reuses_existing_archive_with_same_hash(tmp_path):
    payload = _make_zip("BTCUSDT-1m-2026-09.csv", _csv_rows(3))
    digest = hashlib.sha256(payload).hexdigest()
    http = FakeArchiveHttp(
        zip_bytes=payload,
        checksum_text=f"{digest}  BTCUSDT-1m-2026-09.zip\n",
    )
    client = _client(http, str(tmp_path))

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    downloaded = await client.download(ref)
    first = client.commit(downloaded, version=1)

    downloaded2 = await client.download(ref)
    second = client.commit(downloaded2, version=1)

    assert second == first
    assert sha256_file(first) == digest


@pytest.mark.asyncio
async def test_commit_refuses_to_overwrite_archive_with_different_hash(tmp_path):
    payload = _make_zip("BTCUSDT-1m-2026-09.csv", _csv_rows(3))
    digest = hashlib.sha256(payload).hexdigest()
    http = FakeArchiveHttp(
        zip_bytes=payload,
        checksum_text=f"{digest}  BTCUSDT-1m-2026-09.zip\n",
    )
    client = _client(http, str(tmp_path))

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    downloaded = await client.download(ref)
    target = client.commit(downloaded, version=1)
    with open(target, "wb") as handle:
        handle.write(b"tampered")

    downloaded2 = await client.download(ref)
    with pytest.raises(ArchiveChecksumMismatch):
        client.commit(downloaded2, version=1)

    with open(target, "rb") as handle:
        assert handle.read() == b"tampered"


@pytest.mark.asyncio
async def test_commit_restores_missing_checksum_file(tmp_path):
    payload = _make_zip("BTCUSDT-1m-2026-09.csv", _csv_rows(3))
    digest = hashlib.sha256(payload).hexdigest()
    official_text = f"{digest}  BTCUSDT-1m-2026-09.zip\n"
    http = FakeArchiveHttp(zip_bytes=payload, checksum_text=official_text)
    client = _client(http, str(tmp_path))

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    downloaded = await client.download(ref)
    target = client.commit(downloaded, version=1)
    checksum_path = f"{target}.CHECKSUM"

    # 崩溃窗口：归档已落盘但 CHECKSUM 丢失
    os.remove(checksum_path)

    downloaded2 = await client.download(ref)
    client.commit(downloaded2, version=1)

    assert os.path.exists(checksum_path)
    with open(checksum_path, "r", encoding="utf-8") as handle:
        assert handle.read() == official_text


@pytest.mark.asyncio
async def test_commit_keeps_official_checksum_text_verbatim(tmp_path):
    payload = _make_zip("BTCUSDT-1m-2026-09.csv", _csv_rows(3))
    digest = hashlib.sha256(payload).hexdigest()
    official_text = f"{digest}  BTCUSDT-1m-2026-09.zip\n"
    http = FakeArchiveHttp(zip_bytes=payload, checksum_text=official_text)
    client = _client(http, str(tmp_path))

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    downloaded = await client.download(ref)
    target = client.commit(downloaded, version=1)

    with open(f"{target}.CHECKSUM", "r", encoding="utf-8") as handle:
        assert handle.read() == official_text
    assert downloaded.official_file_name == "BTCUSDT-1m-2026-09.zip"


def test_parse_checksum_matches_expected_file_name():
    text = f"{'1' * 64}  other-file.zip\n{'2' * 64}  BTCUSDT-1m-2026-09.zip\n"
    digest, file_name = parse_checksum(text, expected_file_name="BTCUSDT-1m-2026-09.zip")
    assert digest == "2" * 64
    assert file_name == "BTCUSDT-1m-2026-09.zip"


def test_parse_checksum_rejects_missing_expected_file_name():
    text = f"{'1' * 64}  other-file.zip\n"
    with pytest.raises(ArchiveChecksumMismatch):
        parse_checksum(text, expected_file_name="BTCUSDT-1m-2026-09.zip")


def test_read_klines_from_archive_parses_expected_member(tmp_path):
    payload = _make_zip("BTCUSDT-1m-2026-09.csv", _csv_rows(4))
    archive_path = tmp_path / "BTCUSDT-1m-2026-09.zip"
    archive_path.write_bytes(payload)

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    bars = read_klines_from_archive(str(archive_path), ref)
    assert len(bars) == 4
    assert bars[0].open_time_ms == 1704067200000


def test_read_klines_from_archive_rejects_missing_member(tmp_path):
    payload = _make_zip("other.csv", _csv_rows(2))
    archive_path = tmp_path / "BTCUSDT-1m-2026-09.zip"
    archive_path.write_bytes(payload)

    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    with pytest.raises(ArchiveContentError):
        read_klines_from_archive(str(archive_path), ref)


def test_read_klines_from_archive_rejects_invalid_zip(tmp_path):
    archive_path = tmp_path / "BTCUSDT-1m-2026-09.zip"
    archive_path.write_bytes(b"not a zip")
    ref = build_archive_ref("BTCUSDT", "1m", "2026-09")
    with pytest.raises(ArchiveContentError):
        read_klines_from_archive(str(archive_path), ref)


def test_parse_klines_csv_skips_header_row():
    text = "open_time,open,high,low,close,volume\n" + _csv_rows(2)
    bars = parse_klines_csv(text)
    assert len(bars) == 2


def test_parse_checksum_rejects_invalid_content():
    with pytest.raises(ArchiveChecksumMismatch):
        parse_checksum("not-a-hash  file.zip\n", expected_file_name="file.zip")


def test_parse_checksum_rejects_empty_content():
    with pytest.raises(ArchiveChecksumMismatch):
        parse_checksum("\n", expected_file_name="file.zip")
