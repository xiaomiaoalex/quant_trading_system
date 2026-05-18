"""
historical_snapshot_provider.py - P10 Historical Crypto Risk Snapshot Provider
=============================================================================
Service 层历史快照提供者，用于回测时从历史数据构建 CryptoRiskSnapshot。

输入：
- OHLCV / mark price
- Funding / OI metrics
- Exchange rules / leverage brackets
- Initial account / positions

输出：
- CryptoRiskSnapshot（被 CryptoPreTradeRiskPlugin 消费）

核心契约：
- `build(signal: Signal) -> CryptoRiskSnapshot` - 满足 CryptoRiskSnapshotProvider Protocol
- 支持 stale/missing Funding/OI 场景
- 使用 as-of lookup 防止未来数据泄露

参考:
- docs/INTERFACE_CONTRACTS.md 8.13.9 HistoricalCryptoRiskSnapshotProvider 契约
- trader/core/application/plugins/crypto_pre_trade_risk_plugin.py::CryptoRiskSnapshotProvider
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trader.core.domain.models.crypto_risk import (
        CryptoAccountRisk,
        CryptoFundingOIRiskMetrics,
        CryptoInstrumentSpec,
        CryptoPositionRisk,
        CryptoRiskBudget,
        CryptoRiskSnapshot,
        LeverageBracket,
    )
    from trader.core.domain.models.signal import Signal
    from trader.services.backtesting.backtest_risk_replay import AccountSnapshot, PositionSnapshot


@dataclass
class HistoricalOHLCV:
    timestamp_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass
class HistoricalMarkPrice:
    timestamp_ms: int
    symbol: str
    price: Decimal


@dataclass
class HistoricalFundingOI:
    timestamp_ms: int
    symbol: str
    funding_rate: Decimal | None = None
    open_interest: Decimal | None = None
    funding_data_stale: bool = False
    oi_data_stale: bool = False
    funding_current_missing: bool = False
    oi_current_missing: bool = False


@dataclass
class HistoricalExchangeRule:
    symbol: str
    market_type: str = "usd_m_futures"
    price_tick: Decimal = Decimal("0.01")
    qty_step: Decimal = Decimal("0.001")
    min_qty: Decimal = Decimal("0.001")
    min_notional: Decimal = Decimal("10")
    max_qty: Decimal | None = None


@dataclass
class HistoricalLeverageBracket:
    symbol: str
    notional_floor: Decimal = Decimal("0")
    notional_cap: Decimal = Decimal("100000")
    initial_leverage: Decimal = Decimal("20")
    maint_margin_ratio: Decimal = Decimal("0.005")


@dataclass
class HistoricalAccountData:
    equity: Decimal = Decimal("0")
    available_balance: Decimal = Decimal("0")
    wallet_balance: Decimal = Decimal("0")
    margin_balance: Decimal = Decimal("0")
    total_initial_margin: Decimal = Decimal("0")
    total_maintenance_margin: Decimal = Decimal("0")


@dataclass
class HistoricalPositionData:
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    leverage: Decimal = Decimal("1")
    position_side: str = "BOTH"


@dataclass
class HistoricalSnapshotInput:
    timestamp_ms: int
    symbol: str
    ohlcv: HistoricalOHLCV | None = None
    mark_prices: dict[str, Decimal] = field(default_factory=dict)
    funding_oi: HistoricalFundingOI | None = None
    exchange_rules: dict[str, HistoricalExchangeRule] = field(default_factory=dict)
    leverage_brackets: dict[str, list[HistoricalLeverageBracket]] = field(default_factory=dict)
    account: HistoricalAccountData | None = None
    positions: list[HistoricalPositionData] = field(default_factory=list)


class FakeHistoricalCryptoRiskSnapshotProvider:
    """Fake Historical Crypto Risk Snapshot Provider

    用于回测时从历史数据构建 CryptoRiskSnapshot。
    满足 CryptoRiskSnapshotProvider Protocol，可直接注入 CryptoPreTradeRiskPlugin。

    特点：
    - 实现 `build(signal) -> CryptoRiskSnapshot`
    - 转换历史输入为 Crypto DTOs
    - 支持 stale/missing Funding/OI 场景
    - 使用 as-of lookup 防止未来数据泄露
    - Core 层无 IO，历史数据读取停留在 Service 层
    """

    def __init__(
        self,
        initial_account: HistoricalAccountData,
        initial_positions: list[HistoricalPositionData] | None = None,
        risk_budget: CryptoRiskBudget | None = None,
        historical_data: dict[str, list[HistoricalSnapshotInput]] | None = None,
    ) -> None:
        self._initial_account = initial_account
        self._initial_positions = initial_positions or []
        self._risk_budget = risk_budget
        self._historical_data = historical_data or {}
        self._current_timestamp_ms: int = 0

    def add_historical_snapshot(
        self,
        symbol: str,
        snapshot: HistoricalSnapshotInput,
    ) -> None:
        if symbol not in self._historical_data:
            self._historical_data[symbol] = []
        self._historical_data[symbol].append(snapshot)

    def add_historical_batch(
        self,
        symbol: str,
        snapshots: list[HistoricalSnapshotInput],
    ) -> None:
        if symbol not in self._historical_data:
            self._historical_data[symbol] = []
        self._historical_data[symbol].extend(snapshots)

    async def build(self, signal: Signal) -> CryptoRiskSnapshot:
        """构建 CryptoRiskSnapshot（满足 CryptoRiskSnapshotProvider Protocol）

        使用 as-of lookup：只使用 timestamp <= current_timestamp 的历史数据。
        """
        from trader.core.domain.models.crypto_risk import (
            CryptoAccountRisk,
            CryptoFundingOIRiskMetrics,
            CryptoInstrumentSpec,
            CryptoMarketType,
            CryptoPositionRisk,
            CryptoRiskBudget,
            CryptoRiskSnapshot,
            LeverageBracket,
        )

        self._current_timestamp_ms = self._get_signal_timestamp_ms(signal)

        account_data = self._get_account_at_timestamp(self._current_timestamp_ms)
        crypto_account = CryptoAccountRisk(
            equity=account_data.equity,
            available_balance=account_data.available_balance,
            wallet_balance=account_data.wallet_balance,
            margin_balance=account_data.margin_balance,
            total_initial_margin=account_data.total_initial_margin,
            total_maintenance_margin=account_data.total_maintenance_margin,
        )

        target_symbol = signal.symbol.upper().replace("-", "").replace("/", "").strip()
        positions = self._get_positions_at_timestamp(self._current_timestamp_ms)

        crypto_positions: list[CryptoPositionRisk] = []
        for pos in positions:
            mark_price = self._get_mark_price(pos.symbol, self._current_timestamp_ms)
            crypto_positions.append(
                CryptoPositionRisk(
                    symbol=pos.symbol,
                    qty=pos.quantity,
                    entry_price=pos.entry_price,
                    mark_price=mark_price,
                    leverage=pos.leverage,
                )
            )

        portfolio_symbols = self._get_portfolio_symbols(target_symbol, positions)
        specs = self._build_instrument_specs(portfolio_symbols)
        brackets = self._build_leverage_brackets(portfolio_symbols)
        mark_prices = self._build_mark_prices(portfolio_symbols)
        funding_oi_metrics = self._build_funding_oi_metrics(portfolio_symbols)

        return CryptoRiskSnapshot(
            account=crypto_account,
            instrument_specs=specs,
            leverage_brackets=brackets,
            positions=crypto_positions,
            open_orders=[],
            mark_prices=mark_prices,
            risk_budget=self._risk_budget or CryptoRiskBudget(),
            venue_health="HEALTHY",
            funding_oi_metrics=funding_oi_metrics,
        )

    async def get_account_snapshot(
        self,
        symbol: str,
        timestamp_ms: int,
    ) -> AccountSnapshot:
        """获取历史账户快照（replay timeline helper）"""
        from trader.services.backtesting.backtest_risk_replay import AccountSnapshot

        account_data = self._get_account_at_timestamp(timestamp_ms)
        positions = self._get_positions_at_timestamp(timestamp_ms)

        total_position_value = Decimal("0")
        for pos in positions:
            mark_price = self._get_mark_price(pos.symbol, timestamp_ms)
            if mark_price and pos.quantity > 0:
                total_position_value += pos.quantity * mark_price

        return AccountSnapshot(
            timestamp_ms=timestamp_ms,
            total_equity=account_data.equity,
            available_cash=account_data.available_balance,
            total_position_value=total_position_value,
            margin_used=account_data.total_initial_margin,
            unrealized_pnl=Decimal("0"),
        )

    async def get_position_snapshot(
        self,
        symbol: str,
        timestamp_ms: int,
    ) -> PositionSnapshot:
        """获取历史持仓快照（replay timeline helper）"""
        from trader.services.backtesting.backtest_risk_replay import PositionSnapshot

        positions = self._get_positions_at_timestamp(timestamp_ms)
        position = next((p for p in positions if p.symbol == symbol), None)

        if position is None:
            return PositionSnapshot(
                timestamp_ms=timestamp_ms,
                symbol=symbol,
                quantity=Decimal("0"),
                avg_price=Decimal("0"),
                market_value=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                side="LONG",
            )

        mark_price = self._get_mark_price(symbol, timestamp_ms)
        market_value = position.quantity * mark_price if mark_price else Decimal("0")

        return PositionSnapshot(
            timestamp_ms=timestamp_ms,
            symbol=position.symbol,
            quantity=position.quantity,
            avg_price=position.entry_price,
            market_value=market_value,
            unrealized_pnl=position.unrealized_pnl,
            side="LONG" if position.position_side in ("BOTH", "LONG") else "SHORT",
        )

    def _get_signal_timestamp_ms(self, signal: Signal) -> int:
        ts = getattr(signal, "timestamp", None)
        if ts is None:
            return 0
        if hasattr(ts, "timestamp"):
            return int(ts.timestamp() * 1000)
        if isinstance(ts, (int, float)):
            if ts > 1_000_000_000_000:
                return int(ts)
            return int(ts)
        return 0

    def _get_account_at_timestamp(
        self,
        timestamp_ms: int,
    ) -> HistoricalAccountData:
        """获取指定时间点的账户数据（as-of lookup）"""
        if not self._historical_data:
            return self._initial_account

        nearest = None
        for symbol in self._historical_data:
            snap = self._find_as_of_snapshot(symbol, timestamp_ms)
            if snap and snap.account:
                if nearest is None or snap.timestamp_ms > nearest.timestamp_ms:
                    nearest = snap

        if nearest and nearest.account:
            return nearest.account
        return self._initial_account

    def _get_positions_at_timestamp(
        self,
        timestamp_ms: int,
    ) -> list[HistoricalPositionData]:
        """获取指定时间点的持仓数据（as-of lookup）"""
        positions: list[HistoricalPositionData] = list(self._initial_positions)

        for symbol in self._historical_data:
            snap = self._find_as_of_snapshot(symbol, timestamp_ms)
            if snap:
                positions.extend(snap.positions)

        return positions

    def _get_mark_price(
        self,
        symbol: str,
        timestamp_ms: int,
    ) -> Decimal:
        """获取指定时间点的 mark price（as-of lookup）"""
        snap = self._find_as_of_snapshot(symbol, timestamp_ms)
        if snap:
            return snap.mark_prices.get(symbol, Decimal("0"))
        return Decimal("0")

    def _find_as_of_snapshot(
        self,
        symbol: str,
        timestamp_ms: int,
    ) -> HistoricalSnapshotInput | None:
        """As-of lookup：使用 <= timestamp_ms 的数据"""
        snapshots = self._historical_data.get(symbol, [])
        if not snapshots:
            return None

        nearest: HistoricalSnapshotInput | None = None
        nearest_diff: float = float("inf")

        for snap in snapshots:
            diff = snap.timestamp_ms - timestamp_ms
            if diff <= 0 and -diff <= nearest_diff:
                nearest_diff = -diff
                nearest = snap

        return nearest

    def _get_portfolio_symbols(
        self,
        target_symbol: str,
        positions: list[HistoricalPositionData],
    ) -> set[str]:
        symbols = {target_symbol}
        symbols.update(p.symbol for p in positions)
        for symbol in self._historical_data:
            if symbol != target_symbol and symbol not in {p.symbol for p in positions}:
                snap = self._find_as_of_snapshot(symbol, self._current_timestamp_ms)
                if snap:
                    symbols.add(symbol)
        return symbols

    def _build_instrument_specs(
        self,
        symbols: set[str],
    ) -> dict[str, CryptoInstrumentSpec]:
        from trader.core.domain.models.crypto_risk import CryptoInstrumentSpec, CryptoMarketType

        specs: dict[str, CryptoInstrumentSpec] = {}
        for symbol in symbols:
            snap = self._find_as_of_snapshot(symbol, self._current_timestamp_ms)
            if snap and snap.exchange_rules.get(symbol):
                rule = snap.exchange_rules[symbol]
                market_type = CryptoMarketType.SPOT
                if rule.market_type == "usd_m_futures":
                    market_type = CryptoMarketType.USD_M_FUTURES
                elif rule.market_type == "coin_m_futures":
                    market_type = CryptoMarketType.COIN_M_FUTURES
                specs[symbol] = CryptoInstrumentSpec(
                    symbol=symbol,
                    market_type=market_type,
                    price_tick=rule.price_tick,
                    qty_step=rule.qty_step,
                    min_qty=rule.min_qty,
                    min_notional=rule.min_notional,
                    max_qty=rule.max_qty,
                )
        return specs

    def _build_leverage_brackets(
        self,
        symbols: set[str],
    ) -> dict[str, list[LeverageBracket]]:
        from trader.core.domain.models.crypto_risk import LeverageBracket

        brackets: dict[str, list[LeverageBracket]] = {}
        for symbol in symbols:
            snap = self._find_as_of_snapshot(symbol, self._current_timestamp_ms)
            if snap and snap.leverage_brackets.get(symbol):
                brackets[symbol] = [
                    LeverageBracket(
                        symbol=b.symbol,
                        notional_floor=b.notional_floor,
                        notional_cap=b.notional_cap,
                        initial_leverage=b.initial_leverage,
                        maint_margin_ratio=b.maint_margin_ratio,
                    )
                    for b in snap.leverage_brackets[symbol]
                ]
        return brackets

    def _build_mark_prices(
        self,
        symbols: set[str],
    ) -> dict[str, Decimal]:
        prices: dict[str, Decimal] = {}
        for symbol in symbols:
            mark_price = self._get_mark_price(symbol, self._current_timestamp_ms)
            if mark_price > 0:
                prices[symbol] = mark_price
        return prices

    def _build_funding_oi_metrics(
        self,
        symbols: set[str],
    ) -> dict[str, CryptoFundingOIRiskMetrics]:
        from trader.core.domain.models.crypto_risk import CryptoFundingOIRiskMetrics

        metrics: dict[str, CryptoFundingOIRiskMetrics] = {}
        for symbol in symbols:
            snap = self._find_as_of_snapshot(symbol, self._current_timestamp_ms)
            if snap and snap.funding_oi:
                funding = snap.funding_oi
                metrics[symbol] = CryptoFundingOIRiskMetrics(
                    symbol=symbol,
                    current_funding_rate=funding.funding_rate,
                    current_open_interest=funding.open_interest,
                    funding_data_stale=funding.funding_data_stale,
                    oi_data_stale=funding.oi_data_stale,
                    funding_current_missing=funding.funding_current_missing,
                    oi_current_missing=funding.oi_current_missing,
                )
        return metrics


def create_stale_funding_oi(
    symbol: str,
    timestamp_ms: int,
    stale_funding: bool = False,
    stale_oi: bool = False,
    missing_funding: bool = False,
    missing_oi: bool = False,
) -> HistoricalFundingOI:
    return HistoricalFundingOI(
        timestamp_ms=timestamp_ms,
        symbol=symbol,
        funding_rate=Decimal("-0.0001") if not missing_funding else None,
        open_interest=Decimal("1000000") if not missing_oi else None,
        funding_data_stale=stale_funding,
        oi_data_stale=stale_oi,
        funding_current_missing=missing_funding,
        oi_current_missing=missing_oi,
    )


def create_test_snapshot_input(
    symbol: str,
    timestamp_ms: int,
    mark_price: Decimal,
    account: HistoricalAccountData,
    positions: list[HistoricalPositionData] | None = None,
    funding_oi: HistoricalFundingOI | None = None,
) -> HistoricalSnapshotInput:
    return HistoricalSnapshotInput(
        timestamp_ms=timestamp_ms,
        symbol=symbol,
        ohlcv=HistoricalOHLCV(
            timestamp_ms=timestamp_ms,
            open=mark_price,
            high=mark_price * Decimal("1.01"),
            low=mark_price * Decimal("0.99"),
            close=mark_price,
            volume=Decimal("1000"),
        ),
        mark_prices={symbol: mark_price},
        funding_oi=funding_oi,
        exchange_rules={
            symbol: HistoricalExchangeRule(
                symbol=symbol,
                market_type="usd_m_futures",
                price_tick=Decimal("0.01"),
                qty_step=Decimal("0.001"),
                min_qty=Decimal("0.001"),
                min_notional=Decimal("10"),
            )
        },
        leverage_brackets={
            symbol: [
                HistoricalLeverageBracket(
                    symbol=symbol,
                    notional_floor=Decimal("0"),
                    notional_cap=Decimal("100000"),
                    initial_leverage=Decimal("20"),
                    maint_margin_ratio=Decimal("0.005"),
                )
            ]
        },
        account=account,
        positions=positions or [],
    )
