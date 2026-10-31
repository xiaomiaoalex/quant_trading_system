"""Immutable, content-addressed spot OHLCV datasets."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decimal_text(value: Any) -> str:
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("Non-finite dataset value")
    return format(amount.normalize(), "f")


def freeze_dataset(identity: dict, bars: list[dict]) -> dict:
    required = (
        "venue",
        "market_type",
        "symbol",
        "base_asset",
        "quote_asset",
        "interval",
        "source",
        "version",
    )
    if any(not isinstance(identity.get(k), str) or not identity[k].strip() for k in required):
        raise ValueError(
            "Dataset requires venue, market_type, symbol, assets, interval, source and version"
        )
    if identity["market_type"] != "SPOT":
        raise ValueError("Only SPOT datasets are supported")
    if identity["base_asset"] == identity["quote_asset"]:
        raise ValueError("Base and quote asset must differ")
    interval = INTERVAL_MS.get(identity["interval"])
    if interval is None or not bars:
        raise ValueError("Unsupported interval or empty dataset")
    frozen_identity = {k: identity[k] for k in required}
    frozen_identity.update(
        schema_version=1,
        interval_ms=interval,
        time_semantics="UTC [open_time_ms,close_time_ms); visible at available_at_ms",
    )
    normalized = []
    previous = None
    for source in bars:
        opened = int(source["open_time_ms"])
        closed = int(source.get("close_time_ms", opened + interval))
        available = int(source.get("available_at_ms", closed))
        if opened < 0 or closed != opened + interval or available != closed:
            raise ValueError("BAR_V1 requires fixed-duration completed bars available at close")
        if previous is not None and opened != previous + interval:
            raise ValueError("Dataset has duplicate, unordered or missing bars")
        values = {k: decimal_text(source[k]) for k in ("open", "high", "low", "close", "volume")}
        o, h, low, c, v = (Decimal(values[k]) for k in ("open", "high", "low", "close", "volume"))
        if min(o, h, low, c) <= 0 or v < 0 or low > min(o, c) or h < max(o, c) or low > h:
            raise ValueError("Invalid OHLCV bounds")
        normalized.append(
            {
                "open_time_ms": opened,
                "close_time_ms": closed,
                "available_at_ms": available,
                **values,
            }
        )
        previous = opened
    content = {"identity": frozen_identity, "bars": normalized}
    return {
        "dataset_id": "ds_" + digest(content),
        **content,
        "quality": {
            "complete": True,
            "bar_count": len(normalized),
            "coverage_start_ms": normalized[0]["open_time_ms"],
            "coverage_end_ms": normalized[-1]["close_time_ms"],
        },
    }
