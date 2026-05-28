from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from trader.adapters.binance.crypto_risk_source import (
    BinanceFuturesRiskDataSource,
    BinanceFuturesRiskDataSourceConfig,
)


class _FakeResponse:
    def __init__(self, status: int, data: Any) -> None:
        self.status = status
        self._data = data

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def json(self) -> Any:
        return self._data

    async def text(self) -> str:
        return str(self._data)


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, "headers": headers, "proxy": proxy})
        if not self._responses:
            raise AssertionError("No queued fake response")
        return self._responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        """Support async with session.get(url) pattern used by _sync_server_time_offset."""
        self.calls.append(
            {"method": "GET", "url": url, "headers": None, "proxy": kwargs.get("proxy")}
        )
        if not self._responses:
            raise AssertionError("No queued fake response")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def _source(session: _FakeSession) -> BinanceFuturesRiskDataSource:
    return BinanceFuturesRiskDataSource(
        BinanceFuturesRiskDataSourceConfig(
            api_key="key",
            secret_key="secret",
            max_retries=1,
        ),
        session=session,
    )


@pytest.mark.asyncio
async def test_account_request_is_signed_and_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(
        [
            _FakeResponse(200, {"serverTime": 100300}),  # time sync response
            _FakeResponse(
                200,
                {
                    "totalWalletBalance": "1000",
                    "availableBalance": "800",
                    "totalMarginBalance": "1050",
                },
            ),
        ]
    )
    monkeypatch.setattr(
        "trader.adapters.binance.crypto_risk_source.time.time",
        lambda: 100.0,
    )

    account = await _source(session).get_account_risk()

    assert account.margin_balance == account.equity
    call = session.calls[1]
    assert call["method"] == "GET"
    assert call["headers"] == {"X-MBX-APIKEY": "key"}
    assert urlparse(call["url"]).path == "/fapi/v3/account"
    query = parse_qs(urlparse(call["url"]).query)
    # timestamp = local_ms(100000) + offset(300) = 100300
    assert query["timestamp"] == ["100300"]
    assert query["recvWindow"] == ["5000"]
    assert "signature" in query


@pytest.mark.asyncio
async def test_public_exchange_info_is_mapped_without_api_key_header() -> None:
    session = _FakeSession(
        [
            _FakeResponse(200, {"serverTime": 100000}),
            _FakeResponse(
                200,
                {
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "baseAsset": "BTC",
                            "quoteAsset": "USDT",
                            "filters": [
                                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                                {
                                    "filterType": "LOT_SIZE",
                                    "minQty": "0.001",
                                    "maxQty": "100",
                                    "stepSize": "0.001",
                                },
                                {"filterType": "MIN_NOTIONAL", "notional": "10"},
                            ],
                        }
                    ]
                },
            ),
        ]
    )

    specs = await _source(session).get_instrument_specs({"BTCUSDT"})

    assert specs["BTCUSDT"].qty_step == specs["BTCUSDT"].min_qty
    assert specs["BTCUSDT"].min_notional == Decimal("10")
    call = session.calls[1]
    assert call["headers"] is None
    assert urlparse(call["url"]).path == "/fapi/v1/exchangeInfo"


@pytest.mark.asyncio
async def test_signed_request_retries_and_resyncs_on_negative_1021(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """-1021 时自动重同步时间并扩大 recvWindow。"""
    session = _FakeSession(
        [
            _FakeResponse(200, {"serverTime": 100000}),  # initial time sync
            _FakeResponse(400, '{"code":-1021,"msg":"Timestamp problem"}'),  # first attempt fails
            _FakeResponse(200, {"serverTime": 100500}),  # resync with new offset
            _FakeResponse(
                200,
                {
                    "totalWalletBalance": "1000",
                    "availableBalance": "800",
                    "totalMarginBalance": "1050",
                },
            ),
        ]
    )
    # max_retries=2: first attempt fails with -1021, second attempt succeeds
    source = BinanceFuturesRiskDataSource(
        BinanceFuturesRiskDataSourceConfig(
            api_key="key",
            secret_key="secret",
            max_retries=2,
        ),
        session=session,
    )
    monkeypatch.setattr(
        "trader.adapters.binance.crypto_risk_source.time.time",
        lambda: 100.0,
    )

    account = await source.get_account_risk()

    # 4 calls: time_sync, failed_attempt, resync, success
    assert len(session.calls) == 4
    # First call is time sync
    assert session.calls[0]["url"].endswith("/v3/time")
    # Second call is the failed signed request (recvWindow still 5000)
    call2 = session.calls[1]
    query2 = parse_qs(urlparse(call2["url"]).query)
    assert query2["recvWindow"] == ["5000"]
    # Third call is resync (gets new serverTime 100500, offset 500)
    assert session.calls[2]["url"].endswith("/v3/time")
    # Fourth call is retry with expanded recvWindow
    call4 = session.calls[3]
    query4 = parse_qs(urlparse(call4["url"]).query)
    # recvWindow doubled: min(60000, 5000*2) = 10000
    assert query4["recvWindow"] == ["10000"]
    # timestamp = 100000 + 500 = 100500
    assert query4["timestamp"] == ["100500"]
    assert account.margin_balance == account.equity


@pytest.mark.asyncio
async def test_time_sync_failure_falls_back_to_local_time(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """时间同步失败时回退到本机时间（无偏移），并记录 warning。"""
    import logging

    session = _FakeSession(
        [
            _FakeResponse(500, "Internal Server Error"),  # time sync fails
            _FakeResponse(
                200,
                {
                    "totalWalletBalance": "1000",
                    "availableBalance": "800",
                    "totalMarginBalance": "1050",
                },
            ),
        ]
    )
    monkeypatch.setattr(
        "trader.adapters.binance.crypto_risk_source.time.time",
        lambda: 100.0,
    )

    account = await _source(session).get_account_risk()

    # 2 calls: failed time sync, account request with local time
    assert len(session.calls) == 2
    assert session.calls[0]["url"].endswith("/v3/time")
    call1 = session.calls[1]
    query1 = parse_qs(urlparse(call1["url"]).query)
    # No offset, uses local time directly
    assert query1["timestamp"] == ["100000"]
    # Warning logged for failed time sync
    assert any("Time sync failed" in record.message for record in caplog.records)
    assert account.margin_balance == account.equity


@pytest.mark.asyncio
async def test_recv_window_resets_after_24h(monkeypatch: pytest.MonkeyPatch) -> None:
    """每日基线重置：超过 24h 后 recvWindow 恢复初始值。"""
    session = _FakeSession(
        [
            _FakeResponse(200, {"serverTime": 100000}),  # initial sync
            _FakeResponse(400, '{"code":-1021,"msg":"Timestamp"}'),  # -1021 expand to 10000
            _FakeResponse(200, {"serverTime": 100100}),  # resync
            _FakeResponse(
                200,
                {
                    "totalWalletBalance": "1000",
                    "availableBalance": "800",
                    "totalMarginBalance": "1050",
                },
            ),
            # Second request after 24h
            _FakeResponse(200, {"serverTime": 100000}),  # sync (skipped if offset already set)
            _FakeResponse(
                200,
                {
                    "totalWalletBalance": "2000",
                    "availableBalance": "1800",
                    "totalMarginBalance": "2100",
                },
            ),
        ]
    )
    source = BinanceFuturesRiskDataSource(
        BinanceFuturesRiskDataSourceConfig(
            api_key="key",
            secret_key="secret",
            max_retries=2,
            initial_recv_window_ms=5000,
        ),
        session=session,
    )

    # First call: 100.0s -> syncs offset
    monkeypatch.setattr(
        "trader.adapters.binance.crypto_risk_source.time.time",
        lambda: 100.0,
    )
    await source.get_account_risk()

    # Second call after 24h: offset already set, -1021 expands to 10000
    monkeypatch.setattr(
        "trader.adapters.binance.crypto_risk_source.time.time",
        lambda: 100.0 + 86400,  # 24h later
    )
    account2 = await source.get_account_risk()

    # Find first account call (index 3) and second account call (index 5)
    account_calls = [(i, c) for i, c in enumerate(session.calls) if "/fapi/v3/account" in c["url"]]
    assert len(account_calls) >= 2

    # Second request recvWindow should be reset to initial (5000) then expanded to 10000
    idx2, call2 = account_calls[1]
    query2 = parse_qs(urlparse(call2["url"]).query)
    assert query2["recvWindow"] == ["10000"]
    assert account2.margin_balance == account2.equity
