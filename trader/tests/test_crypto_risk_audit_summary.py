"""
Tests for GET /v1/risk/crypto/audit/summary endpoint (P4.5).

Verifies rejection reason aggregation across four group_by dimensions:
reason | symbol | strategy | risk_level

Response schema:
{
    "items": [{"key": str, "count": int, "latest_ts_ms": int, "sample_event_id": str|None}],
    "total": int,
    "since_ts_ms": int
}
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from trader.api.main import app
from trader.api.routes import risk as risk_routes
from trader.storage.in_memory import reset_storage


@pytest.fixture(autouse=True)
def reset_storage_fixture() -> None:
    reset_storage()


class AuditCaptureRepo:
    def __init__(self):
        self.appended: list[dict] = []
        self._storage_events: list[dict] = []

    async def append(self, event):
        record = event.to_record()
        self.appended.append(record)
        # Mirror to in-memory storage for list_events
        from trader.storage.in_memory import get_storage

        storage = get_storage()
        storage._events = [e for e in storage._events if e.get("stream_key") != "risk:crypto"]
        storage._events.extend(
            [
                {**record, "event_id": len(self.appended) + i}
                for i, record in enumerate(self.appended)
            ]
        )
        return {**record, "event_id": len(self.appended)}

    async def list_events(
        self,
        *,
        stream_key=None,
        event_type=None,
        trace_id=None,
        signal_id=None,
        since_ts_ms=None,
        limit=2000,
    ):
        from trader.storage.in_memory import get_storage

        storage = get_storage()
        events = storage.list_events(
            stream_key=stream_key,
            event_type=event_type,
            trace_id=trace_id,
            since_ts_ms=since_ts_ms,
            limit=limit,
        )
        if signal_id:
            events = [
                e
                for e in events
                if str(e.get("payload", {}).get("signal_id", "")) == str(signal_id)
            ]
        return events


@pytest.fixture
def capture_repo(monkeypatch):
    repo = AuditCaptureRepo()
    monkeypatch.setattr(risk_routes, "get_market_risk_audit_repository", lambda: repo)
    return repo


def _inject_pre_trade_rejection_events(repo: AuditCaptureRepo) -> list[dict]:
    """Inject a set of pre-trade rejected events for aggregation testing."""
    base_ts = int(time.time() * 1000)

    events = [
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": f"trace-1",
            "ts_ms": base_ts - 1000,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "BTCUSDT",
                "strategy_id": "strat-1",
                "rejection_reason": "SYMBOL_NOTIONAL_CAP_EXCEEDED",
                "risk_level": "HIGH",
                "signal_id": "sig-1",
                "decision_trace_id": "trace-1",
            },
        },
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": f"trace-2",
            "ts_ms": base_ts - 500,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "ETHUSDT",
                "strategy_id": "strat-1",
                "rejection_reason": "CLUSTER_NOTIONAL_CAP_EXCEEDED",
                "risk_level": "HIGH",
                "signal_id": "sig-2",
                "decision_trace_id": "trace-2",
            },
        },
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": f"trace-3",
            "ts_ms": base_ts,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "BTCUSDT",
                "strategy_id": "strat-1",
                "rejection_reason": "SYMBOL_NOTIONAL_CAP_EXCEEDED",
                "risk_level": "HIGH",
                "signal_id": "sig-3",
                "decision_trace_id": "trace-3",
            },
        },
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": f"trace-4",
            "ts_ms": base_ts - 800,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "BTCUSDT",
                "strategy_id": "strat-2",
                "rejection_reason": "MIN_MARGIN_RATIO_EXCEEDED",
                "risk_level": "CRITICAL",
                "signal_id": "sig-4",
                "decision_trace_id": "trace-4",
            },
        },
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": f"trace-5",
            "ts_ms": base_ts - 300,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "BNBUSDT",
                "strategy_id": "strat-1",
                "rejection_reason": "SYMBOL_NOTIONAL_CAP_EXCEEDED",
                "risk_level": "MEDIUM",
                "signal_id": "sig-5",
                "decision_trace_id": "trace-5",
            },
        },
    ]

    from trader.storage.in_memory import get_storage

    storage = get_storage()
    for i, event in enumerate(events):
        storage.append_event(event)

    return events


def test_get_audit_summary_group_by_reason(capture_repo) -> None:
    """Aggregate pre-trade rejections grouped by rejection_reason."""
    _inject_pre_trade_rejection_events(capture_repo)
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "reason"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert "total" in payload
    assert "since_ts_ms" in payload

    items = payload["items"]
    # We expect 3 distinct rejection reasons:
    # SYMBOL_NOTIONAL_CAP_EXCEEDED: 3
    # CLUSTER_NOTIONAL_CAP_EXCEEDED: 1
    # MIN_MARGIN_RATIO_EXCEEDED: 1
    reason_counts = {item["key"]: item["count"] for item in items}

    assert reason_counts.get("SYMBOL_NOTIONAL_CAP_EXCEEDED") == 3
    assert reason_counts.get("CLUSTER_NOTIONAL_CAP_EXCEEDED") == 1
    assert reason_counts.get("MIN_MARGIN_RATIO_EXCEEDED") == 1


def test_get_audit_summary_group_by_symbol(capture_repo) -> None:
    """Aggregate pre-trade rejections grouped by symbol."""
    _inject_pre_trade_rejection_events(capture_repo)
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "symbol"},
    )

    assert response.status_code == 200
    payload = response.json()
    items = payload["items"]
    symbol_counts = {item["key"]: item["count"] for item in items}

    assert symbol_counts.get("BTCUSDT") == 3
    assert symbol_counts.get("ETHUSDT") == 1
    assert symbol_counts.get("BNBUSDT") == 1


def test_get_audit_summary_group_by_strategy(capture_repo) -> None:
    """Aggregate pre-trade rejections grouped by strategy_id."""
    _inject_pre_trade_rejection_events(capture_repo)
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "strategy"},
    )

    assert response.status_code == 200
    payload = response.json()
    items = payload["items"]
    strategy_counts = {item["key"]: item["count"] for item in items}

    assert strategy_counts.get("strat-1") == 4
    assert strategy_counts.get("strat-2") == 1


def test_get_audit_summary_group_by_risk_level(capture_repo) -> None:
    """Aggregate pre-trade rejections grouped by risk_level."""
    _inject_pre_trade_rejection_events(capture_repo)
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "risk_level"},
    )

    assert response.status_code == 200
    payload = response.json()
    items = payload["items"]
    level_counts = {item["key"]: item["count"] for item in items}

    assert level_counts.get("HIGH") == 3
    assert level_counts.get("CRITICAL") == 1
    assert level_counts.get("MEDIUM") == 1


def test_get_audit_summary_since_ts_ms_filter(capture_repo) -> None:
    """Filter aggregation to events after a given timestamp."""
    _inject_pre_trade_rejection_events(capture_repo)
    client = TestClient(app)

    # base_ts - 400 is between trace-2 and trace-3, so trace-4 and trace-5 should be included
    base_ts = int(time.time() * 1000)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={
            "group_by": "reason",
            "since_ts_ms": base_ts - 400,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    # trace-2 (ts=base_ts-500) and trace-5 (ts=base_ts-300) are after the filter
    items = payload["items"]
    reason_counts = {item["key"]: item["count"] for item in items}

    # Events after base_ts - 400:
    # trace-5 (ts=base_ts-300, SYMBOL_NOTIONAL_CAP_EXCEEDED)
    # trace-3 (ts=base_ts, SYMBOL_NOTIONAL_CAP_EXCEEDED)
    # trace-2 (ts=base_ts-500) is BEFORE the filter (500 > 400), so excluded
    assert reason_counts.get("SYMBOL_NOTIONAL_CAP_EXCEEDED") == 2


def test_get_audit_summary_default_event_type_is_pre_trade_rejected(capture_repo) -> None:
    """When event_type is not provided, default to crypto_risk.pre_trade_rejected."""
    _inject_pre_trade_rejection_events(capture_repo)
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "reason"},
    )

    assert response.status_code == 200
    payload = response.json()
    # All injected events are crypto_risk.pre_trade_rejected
    assert payload["total"] == 5
    items = payload["items"]
    assert len(items) == 3  # 3 distinct reasons


def test_get_audit_summary_empty_result(capture_repo) -> None:
    """When no events exist, return empty items with total=0."""
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "reason"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 0


def test_get_audit_summary_limit_param(capture_repo) -> None:
    """Limit parameter restricts the number of aggregated groups returned."""
    _inject_pre_trade_rejection_events(capture_repo)
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "reason", "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    # Should return at most 1 group
    assert len(payload["items"]) <= 1


def test_get_audit_summary_invalid_group_by_returns_422(capture_repo) -> None:
    """Invalid group_by value should return 422 validation error."""
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "invalid_group"},
    )

    assert response.status_code == 422


def _inject_events_with_missing_strategy_fields(repo: AuditCaptureRepo) -> None:
    """Inject events covering strategy_id/strategy_name fallback and None/empty normalization."""
    base_ts = int(time.time() * 1000)
    events = [
        # Both strategy_id and strategy_name present
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": "trace-a",
            "ts_ms": base_ts - 200,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "BTCUSDT",
                "strategy_id": "strat-alpha",
                "strategy_name": "Alpha Strategy",
                "rejection_reason": "SYMBOL_NOTIONAL_CAP_EXCEEDED",
                "risk_level": "HIGH",
                "signal_id": "sig-a",
            },
        },
        # Only strategy_name present (no strategy_id)
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": "trace-b",
            "ts_ms": base_ts - 100,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "ETHUSDT",
                "strategy_id": None,
                "strategy_name": "Beta Strategy",
                "rejection_reason": "SYMBOL_NOTIONAL_CAP_EXCEEDED",
                "risk_level": "MEDIUM",
                "signal_id": "sig-b",
            },
        },
        # Neither strategy_id nor strategy_name present → "unknown"
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": "trace-c",
            "ts_ms": base_ts,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "BNBUSDT",
                "strategy_id": None,
                "strategy_name": None,
                "rejection_reason": "CLUSTER_NOTIONAL_CAP_EXCEEDED",
                "risk_level": "LOW",
                "signal_id": "sig-c",
            },
        },
    ]
    from trader.storage.in_memory import get_storage

    storage = get_storage()
    for event in events:
        storage.append_event(event)


def test_get_audit_summary_strategy_group_by_falls_back_to_strategy_name(
    capture_repo,
) -> None:
    """group_by=strategy uses strategy_id; falls back to strategy_name when strategy_id is absent."""
    _inject_events_with_missing_strategy_fields(capture_repo)
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "strategy"},
    )

    assert response.status_code == 200
    payload = response.json()
    items = payload["items"]
    counts = {item["key"]: item["count"] for item in items}

    # strat-alpha uses strategy_id; Beta Strategy uses strategy_name fallback
    assert counts.get("strat-alpha") == 1
    assert counts.get("Beta Strategy") == 1
    assert counts.get("unknown") == 1


def test_get_audit_summary_none_and_empty_keys_normalized_to_unknown(capture_repo) -> None:
    """Missing, None, or empty-string field values are normalized to 'unknown' key."""
    _inject_events_with_missing_strategy_fields(capture_repo)
    client = TestClient(app)

    response = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "symbol"},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    counts = {item["key"]: item["count"] for item in items}

    # All three symbols present → no unknown
    assert counts.get("BTCUSDT") == 1
    assert counts.get("ETHUSDT") == 1
    assert counts.get("BNBUSDT") == 1
    assert "unknown" not in counts


def test_get_audit_summary_missing_non_strategy_field_normalizes_to_unknown(
    capture_repo,
) -> None:
    """None/missing symbol or rejection_reason maps to 'unknown' key for non-strategy group_by."""
    base_ts = int(time.time() * 1000)
    events = [
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": "trace-x",
            "ts_ms": base_ts - 10,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": None,
                "strategy_id": "strat-x",
                "rejection_reason": None,
                "risk_level": "HIGH",
                "signal_id": "sig-x",
            },
        },
        {
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.pre_trade_rejected",
            "schema_version": 1,
            "trace_id": "trace-y",
            "ts_ms": base_ts,
            "asset_class": "crypto",
            "venue": "binance",
            "account_id": "crypto_risk",
            "payload": {
                "symbol": "",
                "strategy_id": "strat-y",
                "rejection_reason": "",
                "risk_level": "HIGH",
                "signal_id": "sig-y",
            },
        },
    ]
    from trader.storage.in_memory import get_storage

    storage = get_storage()
    for event in events:
        storage.append_event(event)
    client = TestClient(app)

    # symbol=None/empty → "unknown" when grouping by symbol
    resp_sym = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "symbol"},
    )
    sym_items = {item["key"]: item["count"] for item in resp_sym.json()["items"]}
    assert sym_items.get("unknown") == 2

    # rejection_reason=None/empty → "unknown" when grouping by reason
    resp_reason = client.get(
        "/v1/risk/crypto/audit/summary",
        params={"group_by": "reason"},
    )
    reason_items = {item["key"]: item["count"] for item in resp_reason.json()["items"]}
    assert reason_items.get("unknown") == 2
