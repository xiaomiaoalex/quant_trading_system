"""Benchmark constituent projector service.

Project benchmark constituent weights into Performance storage.
Supports both static configuration and dynamic Binance market cap weighting.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from trader.services.performance import PerformanceService, get_performance_service

logger = logging.getLogger(__name__)

# Binance API base URL
BINANCE_API_BASE = "https://api.binance.com"


@dataclass(slots=True)
class BenchmarkConstituent:
    """One benchmark constituent with symbol and weight."""

    symbol: str
    weight: Decimal
    period_return: Decimal = Decimal("0")


@dataclass(slots=True)
class BenchmarkProjectorResult:
    """Result summary for benchmark projection."""

    benchmark_id: str
    projected: int = 0
    duplicates: int = 0
    failed: int = 0
    period_start_ms: int = 0
    period_end_ms: int = 0


@dataclass(slots=True)
class PortfolioHoldingFactResult:
    """Result summary for portfolio holding fact generation."""

    run_id: str
    projected: int = 0
    duplicates: int = 0
    failed: int = 0
    period_start_ms: int = 0
    period_end_ms: int = 0


class BenchmarkConstituentProjector:
    """Project benchmark constituent weights into Performance storage.

    Supports two projection modes:
    1. project_from_config: Write fixed weights from configuration
    2. project_benchmark: Fetch market cap weights from Binance and project

    Idempotent writes use UNIQUE constraint on:
    (benchmark_id, symbol, period_start_ms, period_end_ms)
    """

    def __init__(
        self,
        *,
        performance_service: PerformanceService | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._performance_service = performance_service or get_performance_service()
        self._http_client = http_client

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client.

        Returns an AsyncClient that should be used within an async context manager.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def _close_http_client(self) -> None:
        """Close the HTTP client if it was created by this projector."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _fetch_binance_weights(
        self,
        top_n: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch top coins by quote volume from Binance 24hr ticker API.

        Returns a list of dicts with 'symbol' and 'quoteVolume' keys,
        sorted by quoteVolume descending.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{BINANCE_API_BASE}/api/v3/ticker/24hr"

            try:
                response = await client.get(url)
                response.raise_for_status()
                tickers = response.json()

                # Filter USDT pairs and sort by quoteVolume
                usdt_tickers = [
                    {
                        "symbol": t["symbol"],
                        "quoteVolume": Decimal(t["quoteVolume"]),
                    }
                    for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"].isalpha()  # Filter out leveraged tokens
                ]

                # Sort by quoteVolume descending
                usdt_tickers.sort(key=lambda x: x["quoteVolume"], reverse=True)

                # Return top_n
                return usdt_tickers[:top_n]

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Binance API HTTP error (status %s) fetching weights: %s",
                    exc.response.status_code,
                    exc,
                )
                raise
            except httpx.RequestError as exc:
                logger.warning("Binance API request error fetching weights: %s", exc)
                raise

    async def _normalize_weights(
        self,
        constituents: list[BenchmarkConstituent],
    ) -> list[BenchmarkConstituent]:
        """Normalize weights to sum to 1.0."""
        total = sum(c.weight for c in constituents)
        if total <= 0:
            logger.warning(
                "Cannot normalize weights with total <= 0: %s constituents",
                len(constituents),
            )
            return []

        normalized = []
        for c in constituents:
            normalized_weight = c.weight / total
            normalized.append(
                BenchmarkConstituent(
                    symbol=c.symbol,
                    weight=normalized_weight,
                    period_return=c.period_return,
                )
            )
        return normalized

    def _make_holding_id(
        self,
        benchmark_id: str,
        symbol: str,
        period_start_ms: int,
        period_end_ms: int,
    ) -> str:
        """Generate a stable holding ID from components."""
        raw = "|".join(str(part) for part in [benchmark_id, symbol, period_start_ms, period_end_ms])
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"benchmark_holding:{digest}"

    async def project_from_config(
        self,
        *,
        benchmark_id: str,
        constituents: dict[str, Decimal],
        period_start_ms: int,
        period_end_ms: int,
        period_return: str = "0",
    ) -> BenchmarkProjectorResult:
        """Project benchmark weights from configuration.

        Args:
            benchmark_id: Unique benchmark identifier (e.g., "config:my_benchmark")
            constituents: Dict mapping symbol -> weight (will be normalized to 1.0)
            period_start_ms: Period start timestamp in milliseconds
            period_end_ms: Period end timestamp in milliseconds
            period_return: Default period return for all constituents (default "0")

        Returns:
            BenchmarkProjectorResult with projected/duplicates/failed counts
        """
        result = BenchmarkProjectorResult(
            benchmark_id=benchmark_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )

        # Parse default period return
        default_period_return = Decimal(period_return)

        # Convert to BenchmarkConstituent list
        const_list = [
            BenchmarkConstituent(symbol=symbol, weight=weight, period_return=default_period_return)
            for symbol, weight in constituents.items()
        ]

        # Normalize weights to sum to 1.0
        const_list = await self._normalize_weights(const_list)

        if not const_list:
            return result

        # Pre-fetch existing holdings for idempotency check
        existing_holdings = await self._performance_service.list_benchmark_holdings(
            benchmark_id=benchmark_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
        existing_symbols = {h.symbol for h in existing_holdings}

        # Save each constituent
        for const in const_list:
            if const.symbol in existing_symbols:
                result.duplicates += 1
                continue
            holding_id = self._make_holding_id(
                benchmark_id, const.symbol, period_start_ms, period_end_ms
            )
            try:
                await self._performance_service.record_benchmark_holding(
                    benchmark_id=benchmark_id,
                    symbol=const.symbol,
                    period_start_ms=period_start_ms,
                    period_end_ms=period_end_ms,
                    weight=const.weight,
                    period_return=const.period_return,
                    holding_id=holding_id,
                    source="benchmark_projector",
                    quality="complete",
                    metadata={},
                )
                result.projected += 1
            except Exception as exc:
                result.failed += 1
                logger.warning("Failed to project benchmark holding %s: %s", const.symbol, exc)

        return result

    async def project_benchmark(
        self,
        *,
        benchmark_id: str,
        period_start_ms: int,
        period_end_ms: int,
        top_n: int = 20,
        symbols: str | None = None,
    ) -> BenchmarkProjectorResult:
        """Project benchmark weights by fetching from Binance.

        Fetches top coins by quote volume from Binance 24hr ticker API,
        normalizes weights to 1.0, and projects into Performance storage.

        Args:
            benchmark_id: Unique benchmark identifier (e.g., "binance:top20")
            period_start_ms: Period start timestamp in milliseconds
            period_end_ms: Period end timestamp in milliseconds
            top_n: Number of top coins to include (default 20)
            symbols: Optional comma-separated list of symbols to filter
                (e.g., "BTCUSDT,ETHUSDT"). If provided, only these symbols are included.

        Returns:
            BenchmarkProjectorResult with projected/duplicates/failed counts
        """
        result = BenchmarkProjectorResult(
            benchmark_id=benchmark_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )

        # Parse symbols filter if provided
        symbols_filter: set[str] | None = None
        if symbols:
            symbols_filter = {s.strip().upper() for s in symbols.split(",") if s.strip()}

        # Fetch weights from Binance
        try:
            ticker_data = await self._fetch_binance_weights(top_n=top_n)
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch Binance weights for %s: %s", benchmark_id, exc)
            result.failed = top_n
            return result

        if not ticker_data:
            logger.warning("No ticker data returned from Binance for %s", benchmark_id)
            return result

        # Apply symbols filter if provided
        if symbols_filter:
            ticker_data = [t for t in ticker_data if t["symbol"] in symbols_filter]
            if not ticker_data:
                logger.warning(
                    "No matching symbols after filter for %s: %s", benchmark_id, symbols_filter
                )
                return result

        # Convert to BenchmarkConstituent list
        constituents = []
        for t in ticker_data:
            qv = t["quoteVolume"]
            # Handle both string (from API) and Decimal (from tests)
            if isinstance(qv, str):
                qv = Decimal(qv)
            constituents.append(
                BenchmarkConstituent(
                    symbol=t["symbol"],
                    weight=qv,
                )
            )

        # Normalize weights to sum to 1.0
        constituents = await self._normalize_weights(constituents)

        if not constituents:
            logger.warning("No constituents to project after normalization for %s", benchmark_id)
            return result

        # Pre-fetch existing holdings for idempotency check
        existing_holdings = await self._performance_service.list_benchmark_holdings(
            benchmark_id=benchmark_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
        existing_symbols = {h.symbol for h in existing_holdings}

        # Build a map for quoteVolume lookup
        quote_volume_map = {t["symbol"]: str(t["quoteVolume"]) for t in ticker_data}

        # Save each constituent
        for const in constituents:
            if const.symbol in existing_symbols:
                result.duplicates += 1
                continue
            try:
                holding_id = self._make_holding_id(
                    benchmark_id, const.symbol, period_start_ms, period_end_ms
                )
                await self._performance_service.record_benchmark_holding(
                    benchmark_id=benchmark_id,
                    symbol=const.symbol,
                    period_start_ms=period_start_ms,
                    period_end_ms=period_end_ms,
                    weight=const.weight,
                    period_return=const.period_return,
                    holding_id=holding_id,
                    source="benchmark_projector:binance",
                    quality="complete",
                    metadata={"quoteVolume": quote_volume_map.get(const.symbol, "")},
                )
                result.projected += 1
            except Exception as exc:
                result.failed += 1
                logger.warning("Failed to project benchmark holding %s: %s", const.symbol, exc)

        return result


class InitialWeightGenerator:
    """Generate period-start portfolio holding weights from config or snapshot.

    Used to initialize the portfolio holding facts at the start of a performance
    period, providing baseline weights for attribution calculations.
    """

    def __init__(
        self,
        *,
        performance_service: PerformanceService | None = None,
    ) -> None:
        self._performance_service = performance_service or get_performance_service()

    def _make_fact_id(
        self,
        run_id: str,
        symbol: str,
        period_start_ms: int,
        period_end_ms: int,
    ) -> str:
        """Generate a stable holding fact ID from components."""
        raw = "|".join(str(part) for part in [run_id, symbol, period_start_ms, period_end_ms])
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"portfolio_holding:{digest}"

    async def generate_from_config(
        self,
        *,
        run_id: str,
        constituents: dict[str, Decimal],
        period_start_ms: int,
        period_end_ms: int,
    ) -> PortfolioHoldingFactResult:
        """Generate portfolio holding facts from configuration.

        Args:
            run_id: Strategy run identifier
            constituents: Dict mapping symbol -> weight (will be normalized to 1.0)
            period_start_ms: Period start timestamp in milliseconds
            period_end_ms: Period end timestamp in milliseconds

        Returns:
            PortfolioHoldingFactResult with projected/duplicates/failed counts
        """
        result = PortfolioHoldingFactResult(
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )

        # Normalize weights to sum to 1.0
        total = sum(constituents.values())
        if total <= 0:
            logger.warning("No valid weights provided for run %s", run_id)
            return result

        normalized = {symbol: weight / total for symbol, weight in constituents.items()}

        # Pre-fetch existing facts for idempotency check
        existing_facts = await self._performance_service.list_portfolio_holding_facts(
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
        existing_symbols = {f.symbol for f in existing_facts}

        # Save each portfolio holding fact
        for symbol, weight in normalized.items():
            if symbol in existing_symbols:
                result.duplicates += 1
                continue
            try:
                fact_id = self._make_fact_id(run_id, symbol, period_start_ms, period_end_ms)
                await self._performance_service.record_portfolio_holding_fact(
                    run_id=run_id,
                    symbol=symbol,
                    period_start_ms=period_start_ms,
                    period_end_ms=period_end_ms,
                    weight=weight,
                    period_return=Decimal("0"),  # Initial weights have no return yet
                    holding_id=fact_id,
                    source="initial_weight_generator",
                    quality="complete",
                    metadata={},
                )
                result.projected += 1
            except Exception as exc:
                result.failed += 1
                logger.warning("Failed to project portfolio holding fact %s: %s", symbol, exc)

        return result


# Module-level convenience functions


async def project_benchmark_from_config(
    *,
    benchmark_id: str,
    constituents: dict[str, Decimal],
    period_start_ms: int,
    period_end_ms: int,
    period_return: str = "0",
) -> BenchmarkProjectorResult:
    """Project benchmark weights from configuration.

    Convenience function that creates a temporary projector.
    """
    projector = BenchmarkConstituentProjector()
    return await projector.project_from_config(
        benchmark_id=benchmark_id,
        constituents=constituents,
        period_start_ms=period_start_ms,
        period_end_ms=period_end_ms,
        period_return=period_return,
    )


async def project_benchmark_from_binance(
    *,
    benchmark_id: str,
    period_start_ms: int,
    period_end_ms: int,
    top_n: int = 20,
) -> BenchmarkProjectorResult:
    """Project benchmark weights from Binance market cap.

    Convenience function that creates a temporary projector.
    """
    projector = BenchmarkConstituentProjector()
    return await projector.project_benchmark(
        benchmark_id=benchmark_id,
        period_start_ms=period_start_ms,
        period_end_ms=period_end_ms,
        top_n=top_n,
    )


async def generate_initial_weights(
    *,
    run_id: str,
    constituents: dict[str, Decimal],
    period_start_ms: int,
    period_end_ms: int,
) -> PortfolioHoldingFactResult:
    """Generate initial portfolio holding weights from configuration.

    Convenience function that creates a temporary generator.
    """
    generator = InitialWeightGenerator()
    return await generator.generate_from_config(
        run_id=run_id,
        constituents=constituents,
        period_start_ms=period_start_ms,
        period_end_ms=period_end_ms,
    )
