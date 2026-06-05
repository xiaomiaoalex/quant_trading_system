"""Tests for BenchmarkConstituentProjector service."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trader.adapters.persistence.performance_repository import (
    PerformanceRepository,
    get_performance_repository,
    reset_performance_repository,
)
from trader.core.domain.models.performance import StrategyRun
from trader.services.performance import PerformanceService


@pytest.fixture
def mock_performance_repo(monkeypatch: pytest.MonkeyPatch) -> PerformanceRepository:
    """Create a fresh performance repository with mocked postgres."""
    reset_performance_repository()
    repo = get_performance_repository()
    monkeypatch.setattr(repo, "_ensure_postgres", AsyncMock(return_value=False))
    return repo


@pytest.fixture
def performance_service(mock_performance_repo: PerformanceRepository) -> PerformanceService:
    """Create a performance service with the mock repo."""
    return PerformanceService(mock_performance_repo)


@pytest.mark.asyncio
async def test_benchmark_projector_fetch_and_project_binance_top_coins(
    performance_service: PerformanceService,
    mock_performance_repo: PerformanceRepository,
) -> None:
    """Test fetching top coins from Binance and projecting benchmark weights."""
    from trader.services.benchmark_projector import (
        BenchmarkConstituentProjector,
        BenchmarkProjectorResult,
    )

    # Mock Binance ticker response with top coins by quote volume
    mock_ticker_data = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000000"},
        {"symbol": "ETHUSDT", "quoteVolume": "500000000"},
        {"symbol": "BNBUSDT", "quoteVolume": "200000000"},
        {"symbol": "SOLUSDT", "quoteVolume": "100000000"},
        {"symbol": "XRPUSDT", "quoteVolume": "80000000"},
    ]

    projector = BenchmarkConstituentProjector(performance_service=performance_service)

    # Patch the Binance API call
    with patch.object(
        projector,
        "_fetch_binance_weights",
        AsyncMock(return_value=mock_ticker_data),
    ):
        result = await projector.project_benchmark(
            benchmark_id="binance:top20",
            period_start_ms=1700000000000,
            period_end_ms=1700000064000,
            top_n=5,
        )

    # Verify result structure
    assert isinstance(result, BenchmarkProjectorResult)
    assert result.benchmark_id == "binance:top20"
    assert result.projected == 5
    assert result.duplicates == 0
    assert result.failed == 0
    assert result.period_start_ms == 1700000000000
    assert result.period_end_ms == 1700000064000

    # Verify holdings were saved
    holdings = await mock_performance_repo.list_benchmark_holdings(
        benchmark_id="binance:top20",
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )
    assert len(holdings) == 5

    # Verify weights are normalized to 1.0
    total_weight = sum(h.weight for h in holdings)
    assert abs(total_weight - Decimal("1")) < Decimal("0.0001")

    # Verify each holding has a valid holding_id
    for holding in holdings:
        assert holding.holding_id.startswith("benchmark_holding:")


@pytest.mark.asyncio
async def test_benchmark_projector_idempotent(
    performance_service: PerformanceService,
    mock_performance_repo: PerformanceRepository,
) -> None:
    """Test that projecting the same benchmark twice does not create duplicates."""
    from trader.services.benchmark_projector import (
        BenchmarkConstituentProjector,
        BenchmarkProjectorResult,
    )

    mock_ticker_data = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000000"},
        {"symbol": "ETHUSDT", "quoteVolume": "500000000"},
    ]

    projector = BenchmarkConstituentProjector(performance_service=performance_service)

    with patch.object(
        projector,
        "_fetch_binance_weights",
        AsyncMock(return_value=mock_ticker_data),
    ):
        # First projection
        first_result = await projector.project_benchmark(
            benchmark_id="binance:spot",
            period_start_ms=1700000000000,
            period_end_ms=1700000064000,
            top_n=2,
        )

        # Second projection (should be idempotent)
        second_result = await projector.project_benchmark(
            benchmark_id="binance:spot",
            period_start_ms=1700000000000,
            period_end_ms=1700000064000,
            top_n=2,
        )

    # First projection should create 2 holdings
    assert first_result.projected == 2
    assert first_result.duplicates == 0
    assert first_result.failed == 0

    # Second projection should have 0 new projections (idempotent)
    assert second_result.projected == 0
    assert second_result.duplicates == 2
    assert second_result.failed == 0

    # Verify only 2 holdings exist (no duplicates)
    holdings = await mock_performance_repo.list_benchmark_holdings(
        benchmark_id="binance:spot",
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )
    assert len(holdings) == 2


@pytest.mark.asyncio
async def test_benchmark_projector_project_from_config(
    performance_service: PerformanceService,
    mock_performance_repo: PerformanceRepository,
) -> None:
    """Test projecting benchmark weights from configuration."""
    from trader.services.benchmark_projector import (
        BenchmarkConstituentProjector,
        BenchmarkProjectorResult,
    )

    config = {
        "BTCUSDT": Decimal("0.40"),
        "ETHUSDT": Decimal("0.35"),
        "BNBUSDT": Decimal("0.15"),
        "SOLUSDT": Decimal("0.10"),
    }

    projector = BenchmarkConstituentProjector(performance_service=performance_service)
    result = await projector.project_from_config(
        benchmark_id="config:my_benchmark",
        constituents=config,
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )

    assert isinstance(result, BenchmarkProjectorResult)
    assert result.benchmark_id == "config:my_benchmark"
    assert result.projected == 4
    assert result.duplicates == 0
    assert result.failed == 0

    # Verify holdings match config
    holdings = await mock_performance_repo.list_benchmark_holdings(
        benchmark_id="config:my_benchmark",
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )
    assert len(holdings) == 4

    # Verify weights match config
    holdings_dict = {h.symbol: h.weight for h in holdings}
    assert holdings_dict["BTCUSDT"] == Decimal("0.40")
    assert holdings_dict["ETHUSDT"] == Decimal("0.35")
    assert holdings_dict["BNBUSDT"] == Decimal("0.15")
    assert holdings_dict["SOLUSDT"] == Decimal("0.10")


@pytest.mark.asyncio
async def test_initial_weight_generator_from_config(
    performance_service: PerformanceService,
    mock_performance_repo: PerformanceRepository,
) -> None:
    """Test generating initial portfolio weights from configuration."""
    from trader.services.benchmark_projector import InitialWeightGenerator

    # Create a test run
    run = await performance_service.create_run(
        StrategyRun(
            run_id="test:run:001",
            deployment_id="dep:test",
            strategy_id="strat:test",
            account_id="acc:test",
            venue="BINANCE",
            initial_capital=Decimal("100000"),
            started_at_ms=1700000000000,
        )
    )

    config = {
        "BTCUSDT": Decimal("0.50"),
        "ETHUSDT": Decimal("0.30"),
        "BNBUSDT": Decimal("0.20"),
    }

    generator = InitialWeightGenerator(performance_service=performance_service)
    result = await generator.generate_from_config(
        run_id="test:run:001",
        constituents=config,
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )

    assert result.run_id == "test:run:001"
    assert result.projected == 3
    assert result.duplicates == 0
    assert result.failed == 0

    # Verify portfolio holding facts were saved
    facts = await mock_performance_repo.list_portfolio_holding_facts(
        run_id="test:run:001",
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )
    assert len(facts) == 3

    # Verify weights match config
    facts_dict = {f.symbol: f.weight for f in facts}
    assert facts_dict["BTCUSDT"] == Decimal("0.50")
    assert facts_dict["ETHUSDT"] == Decimal("0.30")
    assert facts_dict["BNBUSDT"] == Decimal("0.20")


@pytest.mark.asyncio
async def test_initial_weight_generator_idempotent(
    performance_service: PerformanceService,
    mock_performance_repo: PerformanceRepository,
) -> None:
    """Test that generating weights twice is idempotent."""
    from trader.services.benchmark_projector import InitialWeightGenerator

    # Create a test run
    await performance_service.create_run(
        StrategyRun(
            run_id="test:run:002",
            deployment_id="dep:test",
            strategy_id="strat:test",
            account_id="acc:test",
            venue="BINANCE",
            initial_capital=Decimal("100000"),
            started_at_ms=1700000000000,
        )
    )

    config = {
        "BTCUSDT": Decimal("0.60"),
        "ETHUSDT": Decimal("0.40"),
    }

    generator = InitialWeightGenerator(performance_service=performance_service)

    # First generation
    first = await generator.generate_from_config(
        run_id="test:run:002",
        constituents=config,
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )

    # Second generation (idempotent)
    second = await generator.generate_from_config(
        run_id="test:run:002",
        constituents=config,
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )

    assert first.projected == 2
    assert first.duplicates == 0
    assert second.projected == 0
    assert second.duplicates == 2

    # Verify only 2 facts exist
    facts = await mock_performance_repo.list_portfolio_holding_facts(
        run_id="test:run:002",
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )
    assert len(facts) == 2


@pytest.mark.asyncio
async def test_benchmark_projector_handles_empty_ticker_data(
    performance_service: PerformanceService,
) -> None:
    """Test that projector handles empty Binance response gracefully."""
    from trader.services.benchmark_projector import BenchmarkConstituentProjector

    projector = BenchmarkConstituentProjector(performance_service=performance_service)

    with patch.object(
        projector,
        "_fetch_binance_weights",
        AsyncMock(return_value=[]),
    ):
        result = await projector.project_benchmark(
            benchmark_id="binance:empty",
            period_start_ms=1700000000000,
            period_end_ms=1700000064000,
            top_n=10,
        )

    assert result.projected == 0
    assert result.duplicates == 0
    assert result.failed == 0


@pytest.mark.asyncio
async def test_benchmark_projector_respects_top_n_limit(
    performance_service: PerformanceService,
    mock_performance_repo: PerformanceRepository,
) -> None:
    """Test that projector respects the top_n parameter."""
    from trader.services.benchmark_projector import BenchmarkConstituentProjector

    # Return more coins than top_n
    mock_ticker_data = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000000"},
        {"symbol": "ETHUSDT", "quoteVolume": "500000000"},
        {"symbol": "BNBUSDT", "quoteVolume": "200000000"},
        {"symbol": "SOLUSDT", "quoteVolume": "100000000"},
        {"symbol": "XRPUSDT", "quoteVolume": "80000000"},
    ]

    projector = BenchmarkConstituentProjector(performance_service=performance_service)

    with patch.object(
        projector,
        "_fetch_binance_weights",
        AsyncMock(side_effect=lambda top_n=20: mock_ticker_data[:top_n]),
    ):
        result = await projector.project_benchmark(
            benchmark_id="binance:top3",
            period_start_ms=1700000000000,
            period_end_ms=1700000064000,
            top_n=3,
        )

    assert result.projected == 3

    holdings = await mock_performance_repo.list_benchmark_holdings(
        benchmark_id="binance:top3",
        period_start_ms=1700000000000,
        period_end_ms=1700000064000,
    )
    assert len(holdings) == 3

    # Verify weights are normalized to 1.0 for the top 3
    total_weight = sum(h.weight for h in holdings)
    assert abs(total_weight - Decimal("1")) < Decimal("0.0001")
