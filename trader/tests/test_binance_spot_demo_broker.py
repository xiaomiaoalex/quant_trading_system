from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from trader.adapters.broker.binance_spot_demo_broker import (
    BinanceSpotDemoBroker,
    BinanceSpotDemoBrokerConfig,
)


class _FakeResponse:
    def __init__(self, status: int, data: Any, content_type: str = "application/json"):
        self.status = status
        self._data = data
        self.headers = {"Content-Type": content_type}

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def json(self) -> Any:
        return self._data

    async def text(self) -> str:
        return str(self._data)


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, headers: dict[str, str] | None = None, proxy: str | None = None):
        self.calls.append({"method": method, "url": url, "headers": headers, "proxy": proxy})
        if not self._responses:
            raise AssertionError("No queued fake response for request")
        return self._responses.pop(0)


def _build_broker() -> BinanceSpotDemoBroker:
    config = BinanceSpotDemoBrokerConfig.for_demo(api_key="test_key", secret_key="test_secret")
    return BinanceSpotDemoBroker(config)


@pytest.mark.asyncio
async def test_refresh_time_offset_uses_server_time(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = _build_broker()

    async def _fake_server_time() -> int:
        return 100_000

    monkeypatch.setattr(broker, "get_server_time", _fake_server_time)
    monkeypatch.setattr(
        "trader.adapters.broker.binance_spot_demo_broker.time.time",
        lambda: 99.0,
    )

    offset = await broker._refresh_time_offset()

    assert offset == 1_000
    assert broker._time_offset_ms == 1_000
    assert broker._time_offset_synced is True


@pytest.mark.asyncio
async def test_signed_request_uses_time_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = _build_broker()
    broker._time_offset_synced = True
    broker._time_offset_ms = -1_000
    broker._session = _FakeSession([_FakeResponse(200, {"ok": True})])

    monkeypatch.setattr(
        "trader.adapters.broker.binance_spot_demo_broker.time.time",
        lambda: 100.0,
    )

    data = await broker._request("GET", "/v3/account", signed=True)
    assert data == {"ok": True}

    call = broker._session.calls[0]
    query = parse_qs(urlparse(call["url"]).query)
    assert query["timestamp"] == ["99000"]
    assert query["recvWindow"] == [str(broker._config.recv_window)]
    assert "signature" in query


@pytest.mark.asyncio
async def test_signed_request_resyncs_on_1021_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = _build_broker()
    broker._time_offset_synced = True
    broker._time_offset_ms = 0
    broker._session = _FakeSession(
        [
            _FakeResponse(
                400,
                {
                    "code": -1021,
                    "msg": "Timestamp for this request was 1000ms ahead of the server's time.",
                },
            ),
            _FakeResponse(200, {"ok": True}),
        ]
    )

    async def _fake_refresh_time_offset() -> int:
        broker._time_offset_ms = -1_200
        broker._time_offset_synced = True
        return broker._time_offset_ms

    monkeypatch.setattr(broker, "_refresh_time_offset", _fake_refresh_time_offset)
    monkeypatch.setattr(
        "trader.adapters.broker.binance_spot_demo_broker.time.time",
        lambda: 100.0,
    )

    data = await broker._request("GET", "/v3/account", signed=True)
    assert data == {"ok": True}
    assert len(broker._session.calls) == 2

    first_query = parse_qs(urlparse(broker._session.calls[0]["url"]).query)
    second_query = parse_qs(urlparse(broker._session.calls[1]["url"]).query)
    assert first_query["timestamp"] == ["100000"]
    assert second_query["timestamp"] == ["98800"]
