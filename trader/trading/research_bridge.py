"""Binance Vision archive -> Research dataset projection.

Pure, deterministic mapping from archived 1-minute klines to the immutable
``BAR_V1`` dataset shape consumed by the Research event runtime.

Design constraints (see ``docs/INTERFACE_CONTRACTS.md``):

- No IO and no clocks: the same archive bytes always produce the same
  ``dataset_id``.
- Timestamps are integer milliseconds; ``close_time_ms`` and ``available_at_ms``
  are both ``open_time_ms + 60000`` so a bar can never be traded before it
  closes and never before it is visible.
- OHLCV values stay decimal strings. Binary floats are never used.
- ``identity.version`` is derived from the archive digest, never from a task or
  job identifier, so re-importing the same archive is idempotent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from trader.core.domain.services.ohlcv_archive import (
    ARCHIVE_SUPPORTED_SYMBOLS,
    KlineBar,
    OhlcvRequestError,
    parse_month,
)
from trader.trading.datasets import decimal_text, digest, freeze_dataset

RESEARCH_VENUE = "BINANCE"
RESEARCH_MARKET_TYPE = "SPOT"
RESEARCH_SOURCE = "binance_vision"
RESEARCH_INTERVAL = "1m"
RESEARCH_INTERVAL_MS = 60_000
QUOTE_ASSET = "USDT"

CONVERSION_VERSION = "binance_vision_1m_v1"
"""Stable identifier of the archive -> BAR_V1 conversion rules.

Bump this whenever the projection changes in a way that alters the produced
bar payload. It participates in ``identity.version`` so historical datasets
keep pointing at the rules that produced them.
"""

_HEX_DIGITS = frozenset("0123456789abcdef")


class ResearchBridgeError(ValueError):
    """The archive cannot be projected into a Research dataset."""


def split_symbol_assets(symbol: str) -> Tuple[str, str]:
    """Split a USDT spot symbol into its base and quote assets."""
    text = (symbol or "").strip().upper()
    if text not in ARCHIVE_SUPPORTED_SYMBOLS:
        raise ResearchBridgeError(f"Unsupported archive symbol '{symbol}'")
    if not text.endswith(QUOTE_ASSET) or len(text) <= len(QUOTE_ASSET):
        raise ResearchBridgeError(f"Cannot derive base asset from '{symbol}'")
    base = text[: -len(QUOTE_ASSET)]
    if not base.isalnum():
        raise ResearchBridgeError(f"Cannot derive base asset from '{symbol}'")
    return base, QUOTE_ASSET


def _validate_archive_sha256(archive_sha256: str) -> str:
    text = (archive_sha256 or "").strip().lower()
    if len(text) != 64 or not set(text) <= _HEX_DIGITS:
        raise ResearchBridgeError("archive sha256 must be a 64 character hex digest")
    return text


def build_research_identity(symbol: str, *, archive_sha256: str, month: str) -> Dict[str, Any]:
    """Build the frozen dataset identity for an archived month."""
    base_asset, quote_asset = split_symbol_assets(symbol)
    try:
        parse_month(month)
    except OhlcvRequestError as exc:
        raise ResearchBridgeError(str(exc)) from exc
    sha256 = _validate_archive_sha256(archive_sha256)
    return {
        "venue": RESEARCH_VENUE,
        "market_type": RESEARCH_MARKET_TYPE,
        "symbol": symbol.strip().upper(),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "interval": RESEARCH_INTERVAL,
        "source": RESEARCH_SOURCE,
        "version": f"{CONVERSION_VERSION}_{sha256[:12]}",
    }


def project_kline_bars(bars: Sequence[KlineBar]) -> List[Dict[str, Any]]:
    """Project archived klines into the BAR_V1 bar payload."""
    projected: List[Dict[str, Any]] = []
    previous: int | None = None
    for bar in bars:
        opened = int(bar.open_time_ms)
        if previous is not None and opened != previous + RESEARCH_INTERVAL_MS:
            raise ResearchBridgeError(
                "Archive bars are not a contiguous 1 minute series "
                f"(expected {previous + RESEARCH_INTERVAL_MS}, got {opened})"
            )
        previous = opened
        closed = opened + RESEARCH_INTERVAL_MS
        projected.append(
            {
                "open_time_ms": opened,
                "close_time_ms": closed,
                "available_at_ms": closed,
                "open": decimal_text(bar.open),
                "high": decimal_text(bar.high),
                "low": decimal_text(bar.low),
                "close": decimal_text(bar.close),
                "volume": decimal_text(bar.volume),
            }
        )
    return projected


def build_research_dataset(
    bars: Sequence[KlineBar],
    *,
    symbol: str,
    archive_sha256: str,
    month: str,
) -> Dict[str, Any]:
    """Freeze archived klines into an immutable, content addressed dataset."""
    identity = build_research_identity(symbol, archive_sha256=archive_sha256, month=month)
    projected = project_kline_bars(bars)
    if not projected:
        raise ResearchBridgeError("Cannot publish an empty Research dataset")
    return freeze_dataset(identity, projected)


def build_research_provenance(**fields: Any) -> Dict[str, Any]:
    """Freeze the provenance payload of a published dataset.

    The returned mapping holds the canonical payload and its SHA-256 digest.
    The digest covers every payload field, so any change to the archive bytes,
    the coverage window or the conversion rules changes the hash.
    """
    payload = {key: fields[key] for key in sorted(fields)}
    return {"payload": payload, "provenance_hash": digest(payload)}
