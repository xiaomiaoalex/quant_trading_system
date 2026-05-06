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
            _FakeResponse(
                200,
                {
                    "totalWalletBalance": "1000",
                    "availableBalance": "800",
                    "totalMarginBalance": "1050",
                },
            )
        ]
    )
    monkeypatch.setattr(
        "trader.adapters.binance.crypto_risk_source.time.time",
        lambda: 100.0,
    )

    account = await _source(session).get_account_risk()

    assert account.margin_balance == account.equity
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["headers"] == {"X-MBX-APIKEY": "key"}
    assert urlparse(call["url"]).path == "/fapi/v3/account"
    query = parse_qs(urlparse(call["url"]).query)
    assert query["timestamp"] == ["100000"]
    assert query["recvWindow"] == ["5000"]
    assert "signature" in query


@pytest.mark.asyncio
async def test_public_exchange_info_is_mapped_without_api_key_header() -> None:
    session = _FakeSession(
        [
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
            )
        ]
    )

    specs = await _source(session).get_instrument_specs({"BTCUSDT"})

    assert specs["BTCUSDT"].qty_step == specs["BTCUSDT"].min_qty
    assert specs["BTCUSDT"].min_notional == Decimal("10")
    call = session.calls[0]
    assert call["headers"] is None
    assert urlparse(call["url"]).path == "/fapi/v1/exchangeInfo"
