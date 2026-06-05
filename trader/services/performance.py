"""Performance accounting service.

This service is the control/service layer around the pure accounting helpers.
It never pulls exchange data directly; callers must provide fills, cash flows,
and mark/account snapshots that were already cleaned at the adapter boundary.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

from trader.adapters.persistence.performance_repository import (
    PerformanceRepository,
    get_performance_repository,
)
from trader.core.domain.models.performance import (
    AccountingLedgerEntry,
    AccountPortfolioNAV,
    AssetClassification,
    AttributionItem,
    AttributionResult,
    BenchmarkHolding,
    CashFlowEvent,
    FactorExposureFact,
    FactorReturnFact,
    MarkPriceSnapshot,
    NAVPointV2,
    PerformanceSnapshot,
    PortfolioHoldingFact,
    RiskBudgetDecisionTrace,
    StrategyRun,
)
from trader.core.domain.services.performance_accounting import (
    LedgerState,
    annualized_sharpe,
    annualized_sortino,
    apply_fill_to_state,
    build_account_portfolio_nav,
    calculate_brinson_attribution,
    calculate_brinson_carino_linking,
    calculate_daily_returns,
    calculate_factor_attribution,
    calculate_historical_var_cvar,
    calculate_max_drawdown,
    calculate_risk_budget_attribution,
    calculate_trade_statistics,
    calculate_turnover,
    calculate_twr,
    explain_pnl,
    mark_unrealized_pnl,
    simple_mwr,
    to_decimal,
)


class PerformanceRunNotFoundError(ValueError):
    """Raised when a performance operation references an unknown run."""


_RUN_PROJECTION_LOCKS: dict[str, asyncio.Lock] = {}
_ACCOUNT_PROJECTION_LOCKS: dict[str, asyncio.Lock] = {}


class PerformanceService:
    """Build PG-first performance ledger projections for strategy runs."""

    def __init__(
        self,
        repository: PerformanceRepository | None = None,
        *,
        position_lot_provider: Any | None = None,
        allocation_provider: Any | None = None,
    ) -> None:
        self._repository = repository or get_performance_repository()
        self._position_lot_provider = position_lot_provider
        self._allocation_provider = allocation_provider

    async def create_run(self, run: StrategyRun) -> StrategyRun:
        return await self._repository.save_run(run)

    async def close_run(
        self,
        run_id: str,
        *,
        stopped_at_ms: int | None = None,
        stop_reason: str | None = None,
    ) -> StrategyRun:
        run = await self._require_run(run_id)
        stopped = replace(
            run,
            status="STOPPED",
            stopped_at_ms=stopped_at_ms or _now_ms(),
            stop_reason=stop_reason,
        )
        return await self._repository.save_run(stopped)

    async def get_run(self, run_id: str) -> StrategyRun | None:
        return await self._repository.get_run(run_id)

    async def list_runs(
        self,
        *,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[StrategyRun]:
        return await self._repository.list_runs(
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            status=status,
            limit=limit,
        )

    async def record_fill(
        self,
        *,
        run_id: str,
        cl_ord_id: str,
        exec_id: str,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        ts_ms: int | None = None,
        fee: Decimal = Decimal("0"),
        fee_currency: str | None = None,
        fee_in_run_currency: Decimal | None = None,
        fee_conversion_rate: Decimal | None = None,
        fee_conversion_source: str | None = None,
        slippage: Decimal = Decimal("0"),
        funding: Decimal = Decimal("0"),
        mark_prices: dict[str, Decimal] | None = None,
        account_cash: Decimal | None = None,
        account_equity: Decimal | None = None,
        source: str = "performance_service",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[AccountingLedgerEntry, bool]:
        async with self._run_lock(run_id), self._repository.run_projection_lock(run_id):
            return await self._record_fill_unlocked(
                run_id=run_id,
                cl_ord_id=cl_ord_id,
                exec_id=exec_id,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                ts_ms=ts_ms,
                fee=fee,
                fee_currency=fee_currency,
                fee_in_run_currency=fee_in_run_currency,
                fee_conversion_rate=fee_conversion_rate,
                fee_conversion_source=fee_conversion_source,
                slippage=slippage,
                funding=funding,
                mark_prices=mark_prices,
                account_cash=account_cash,
                account_equity=account_equity,
                source=source,
                quality=quality,
                metadata=metadata,
            )

    async def _record_fill_unlocked(
        self,
        *,
        run_id: str,
        cl_ord_id: str,
        exec_id: str,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        ts_ms: int | None,
        fee: Decimal,
        fee_currency: str | None,
        fee_in_run_currency: Decimal | None,
        fee_conversion_rate: Decimal | None,
        fee_conversion_source: str | None,
        slippage: Decimal,
        funding: Decimal,
        mark_prices: dict[str, Decimal] | None,
        account_cash: Decimal | None,
        account_equity: Decimal | None,
        source: str,
        quality: str,
        metadata: dict[str, Any] | None,
    ) -> tuple[AccountingLedgerEntry, bool]:
        run = await self._require_run(run_id)
        event_ts = ts_ms or _now_ms()
        idempotency_key = f"fill:{run_id}:{cl_ord_id}:{exec_id}"
        entry_metadata = dict(metadata or {})
        fee_to_charge, fee_quality = _normalize_fee_for_run_currency(
            fee=fee,
            fee_currency=fee_currency,
            run_currency=run.currency,
            fee_in_run_currency=fee_in_run_currency,
            fee_conversion_rate=fee_conversion_rate,
            fee_conversion_source=fee_conversion_source,
            metadata=entry_metadata,
            quality=quality,
        )

        existing_entries = await self._repository.list_all_ledger_entries(run_id=run_id)
        prior_state = self._replay_state(run, existing_entries)
        gross_pnl, realized_pnl = apply_fill_to_state(
            prior_state,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            fee=fee_to_charge,
        )

        entry = AccountingLedgerEntry(
            ledger_entry_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            account_id=run.account_id,
            venue=run.venue,
            event_type="FILL",
            ts_ms=event_ts,
            symbol=symbol,
            side=side.upper(),
            qty=qty,
            price=price,
            trade_notional=qty * price,
            gross_pnl=gross_pnl,
            realized_pnl=realized_pnl,
            fee=fee_to_charge,
            fee_currency=fee_currency,
            slippage=slippage,
            funding=funding,
            cl_ord_id=cl_ord_id,
            exec_id=exec_id,
            source=source,
            quality=fee_quality,
            metadata=entry_metadata,
        )
        _, created = await self._repository.save_ledger_entry(entry)
        should_record_nav = created
        if not created:
            existing_points = await self._repository.list_all_nav_points(run_id=run_id)
            should_record_nav = not any(point.timestamp_ms == event_ts for point in existing_points)
        if should_record_nav:
            await self._record_nav_unlocked(
                run_id,
                timestamp_ms=event_ts,
                mark_prices=mark_prices or {},
                account_cash=account_cash,
                account_equity=account_equity,
                quality=fee_quality,
            )
        return entry, created

    async def record_funding_settlement(
        self,
        *,
        run_id: str,
        amount: Decimal,
        symbol: str | None = None,
        settlement_id: str | None = None,
        ts_ms: int | None = None,
        currency: str | None = None,
        source: str = "funding_settlement",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[AccountingLedgerEntry, bool]:
        async with self._run_lock(run_id), self._repository.run_projection_lock(run_id):
            run = await self._require_run(run_id)
            event_ts = ts_ms or _now_ms()
            normalized_currency = currency or run.currency
            normalized_settlement_id = (
                settlement_id or f"{run_id}:{symbol or 'account'}:{event_ts}:{amount}"
            )
            entry_metadata = {
                "funding_settlement_id": normalized_settlement_id,
                "funding_currency": normalized_currency,
                "funding_source": source,
                **(metadata or {}),
            }
            entry_quality = quality
            if normalized_currency != run.currency:
                entry_quality = "partial"
                entry_metadata.setdefault("funding_conversion_source", "unavailable")
                entry_metadata.setdefault("funding_converted_currency", run.currency)
                amount_in_run_currency = Decimal("0")
            else:
                entry_metadata.setdefault("funding_conversion_source", "not_required")
                amount_in_run_currency = amount

            entry = AccountingLedgerEntry(
                ledger_entry_id=str(uuid.uuid4()),
                idempotency_key=f"funding:{run_id}:{normalized_settlement_id}",
                run_id=run.run_id,
                deployment_id=run.deployment_id,
                strategy_id=run.strategy_id,
                account_id=run.account_id,
                venue=run.venue,
                event_type="FUNDING",
                ts_ms=event_ts,
                symbol=symbol,
                funding=amount_in_run_currency,
                source=source,
                quality=entry_quality,
                metadata=entry_metadata,
            )
            _, created = await self._repository.save_ledger_entry(entry)
            if created:
                await self._record_nav_unlocked(
                    run_id,
                    timestamp_ms=event_ts,
                    mark_prices={},
                    quality=entry_quality,
                    source=source,
                )
            return entry, created

    async def record_cash_flow(
        self,
        *,
        run_id: str,
        amount: Decimal,
        flow_type: str,
        ts_ms: int | None = None,
        cash_flow_id: str | None = None,
        currency: str | None = None,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> CashFlowEvent:
        async with self._run_lock(run_id), self._repository.run_projection_lock(run_id):
            return await self._record_cash_flow_unlocked(
                run_id=run_id,
                amount=amount,
                flow_type=flow_type,
                ts_ms=ts_ms,
                cash_flow_id=cash_flow_id,
                currency=currency,
                source=source,
                metadata=metadata,
            )

    async def _record_cash_flow_unlocked(
        self,
        *,
        run_id: str,
        amount: Decimal,
        flow_type: str,
        ts_ms: int | None,
        cash_flow_id: str | None,
        currency: str | None,
        source: str,
        metadata: dict[str, Any] | None,
    ) -> CashFlowEvent:
        run = await self._require_run(run_id)
        event_ts = ts_ms or _now_ms()
        normalized_amount = _normalize_cash_flow_amount(amount, flow_type)
        flow = CashFlowEvent(
            cash_flow_id=cash_flow_id or str(uuid.uuid4()),
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            account_id=run.account_id,
            venue=run.venue,
            amount=normalized_amount,
            currency=currency or run.currency,
            flow_type=flow_type,
            ts_ms=event_ts,
            source=source,
            metadata=metadata or {},
        )
        await self._repository.save_cash_flow(flow)
        entry = AccountingLedgerEntry(
            ledger_entry_id=str(uuid.uuid4()),
            idempotency_key=f"cash_flow:{flow.cash_flow_id}",
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            account_id=run.account_id,
            venue=run.venue,
            event_type="CASH_FLOW",
            ts_ms=event_ts,
            cash_flow=normalized_amount,
            source=source,
            metadata={"cash_flow_id": flow.cash_flow_id, **(metadata or {})},
        )
        _, created = await self._repository.save_ledger_entry(entry)
        if created:
            await self._record_nav_unlocked(
                run_id,
                timestamp_ms=event_ts,
                mark_prices={},
                period_cash_flow=normalized_amount,
            )
        return flow

    async def record_nav(
        self,
        run_id: str,
        *,
        timestamp_ms: int | None = None,
        mark_prices: dict[str, Decimal] | None = None,
        account_cash: Decimal | None = None,
        account_equity: Decimal | None = None,
        period_cash_flow: Decimal = Decimal("0"),
        quality: str = "complete",
        source: str = "performance_ledger",
    ) -> NAVPointV2:
        async with self._run_lock(run_id), self._repository.run_projection_lock(run_id):
            return await self._record_nav_unlocked(
                run_id,
                timestamp_ms=timestamp_ms,
                mark_prices=mark_prices,
                account_cash=account_cash,
                account_equity=account_equity,
                period_cash_flow=period_cash_flow,
                quality=quality,
                source=source,
            )

    async def _record_nav_unlocked(
        self,
        run_id: str,
        *,
        timestamp_ms: int | None = None,
        mark_prices: dict[str, Decimal] | None = None,
        account_cash: Decimal | None = None,
        account_equity: Decimal | None = None,
        period_cash_flow: Decimal = Decimal("0"),
        quality: str = "complete",
        source: str = "performance_ledger",
    ) -> NAVPointV2:
        run = await self._require_run(run_id)
        entries = await self._repository.list_all_ledger_entries(run_id=run_id)
        state = self._replay_state(run, entries)
        position_value, unrealized = mark_unrealized_pnl(state.positions or {}, mark_prices or {})
        cash = account_cash if account_cash is not None else state.cash
        equity = account_equity if account_equity is not None else cash + position_value
        total_pnl = equity - run.initial_capital - state.cash_flow
        net_return = _safe_div(total_pnl, run.initial_capital)
        gross_pnl = total_pnl + state.fees + state.slippage - state.funding
        gross_return = _safe_div(gross_pnl, run.initial_capital)

        point = NAVPointV2(
            nav_id=str(uuid.uuid4()),
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            account_id=run.account_id,
            venue=run.venue,
            timestamp_ms=timestamp_ms or _now_ms(),
            equity=equity,
            cash=cash,
            position_value=position_value,
            realized_pnl=state.realized_pnl,
            unrealized_pnl=unrealized,
            fee=state.fees,
            slippage=state.slippage,
            funding=state.funding,
            cash_flow=period_cash_flow,
            total_pnl=total_pnl,
            net_return=net_return,
            gross_return=gross_return,
            twr=Decimal("0"),
            quality="partial" if run.quality == "partial" else quality,
            source=source,
        )
        existing_points = await self._repository.list_all_nav_points(run_id=run_id)
        point.twr = calculate_twr([*existing_points, point])
        flows = [
            (flow.ts_ms, flow.amount) for flow in await self._repository.list_cash_flows(run_id)
        ]
        point.mwr = simple_mwr(run.initial_capital, [*existing_points, point], flows)
        await self._repository.save_nav_point(point)
        await self._save_snapshot(run, [*existing_points, point])
        return point

    async def record_mark(
        self,
        *,
        run_id: str,
        symbol: str,
        mark_price: Decimal,
        timestamp_ms: int | None = None,
        source: str = "performance_service",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> NAVPointV2:
        if mark_price <= 0:
            raise ValueError("mark_price must be positive")
        async with self._run_lock(run_id), self._repository.run_projection_lock(run_id):
            run = await self._require_run(run_id)
            event_ts = timestamp_ms or _now_ms()
            snapshot = MarkPriceSnapshot(
                mark_id=str(uuid.uuid4()),
                run_id=run.run_id,
                deployment_id=run.deployment_id,
                strategy_id=run.strategy_id,
                account_id=run.account_id,
                venue=run.venue,
                symbol=symbol,
                timestamp_ms=event_ts,
                mark_price=mark_price,
                source=source,
                quality=quality,
                metadata=metadata or {},
            )
            await self._repository.save_mark_snapshot(snapshot)
            latest_marks = await self._repository.list_latest_mark_snapshots(
                run_id=run_id,
                as_of_ms=event_ts,
            )
            mark_prices = {point.symbol: point.mark_price for point in latest_marks}
            return await self._record_nav_unlocked(
                run_id,
                timestamp_ms=event_ts,
                mark_prices=mark_prices,
                quality=quality,
                source="mark_projector",
            )

    async def list_nav(
        self,
        *,
        run_id: str | None = None,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        account_id: str | None = None,
        venue: str | None = None,
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[NAVPointV2]:
        return await self._repository.list_nav_points(
            run_id=run_id,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            account_id=account_id,
            venue=venue,
            since_ms=since_ms,
            limit=limit,
        )

    async def list_all_nav(self, *, run_id: str) -> list[NAVPointV2]:
        await self._require_run(run_id)
        return await self._repository.list_all_nav_points(run_id=run_id)

    async def record_account_nav(
        self,
        *,
        account_id: str,
        venue: str,
        equity: Decimal,
        cash: Decimal,
        position_value: Decimal,
        liabilities: Decimal,
        initial_capital: Decimal,
        cumulative_cash_flow: Decimal,
        timestamp_ms: int | None = None,
        currency: str = "USDT",
        quality: str = "complete",
        source: str = "account_snapshot",
        metadata: dict[str, Any] | None = None,
    ) -> AccountPortfolioNAV:
        account_lock_key = f"account:{account_id}:{venue}"
        async with (
            self._account_lock(account_lock_key),
            self._repository.run_projection_lock(account_lock_key),
        ):
            event_ts = timestamp_ms or _now_ms()
            nav_points = await self._repository.list_all_nav_points(
                account_id=account_id,
                venue=venue,
            )
            latest_by_run = self._latest_nav_by_run(
                [point for point in nav_points if point.timestamp_ms <= event_ts]
            )
            point = build_account_portfolio_nav(
                account_nav_id=str(uuid.uuid4()),
                account_id=account_id,
                venue=venue,
                timestamp_ms=event_ts,
                currency=currency,
                equity=equity,
                cash=cash,
                position_value=position_value,
                liabilities=liabilities,
                initial_capital=initial_capital,
                cumulative_cash_flow=cumulative_cash_flow,
                latest_run_nav=latest_by_run,
                quality=quality,
                source=source,
                metadata=metadata,
            )
            return await self._repository.save_account_nav_point(point)

    async def list_account_nav(
        self,
        *,
        account_id: str,
        venue: str | None = None,
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[AccountPortfolioNAV]:
        return await self._repository.list_account_nav_points(
            account_id=account_id,
            venue=venue,
            since_ms=since_ms,
            limit=limit,
        )

    async def get_pnl_explain(self, *, run_id: str) -> dict[str, Decimal]:
        await self._require_run(run_id)
        entries = await self._repository.list_all_ledger_entries(run_id=run_id)
        nav_points = await self._repository.list_all_nav_points(run_id=run_id)
        latest_nav = max(nav_points, key=lambda p: p.timestamp_ms) if nav_points else None
        return explain_pnl(entries, latest_nav)

    async def get_latest_snapshot(self, run_id: str) -> PerformanceSnapshot | None:
        await self._require_run(run_id)
        return await self._repository.get_latest_snapshot(run_id)

    async def record_asset_classification(
        self,
        *,
        symbol: str,
        asset_class: str,
        effective_from_ms: int,
        effective_to_ms: int | None = None,
        classification_id: str | None = None,
        source: str = "manual",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> AssetClassification:
        fact = AssetClassification(
            classification_id=classification_id or str(uuid.uuid4()),
            symbol=symbol,
            asset_class=asset_class,
            effective_from_ms=effective_from_ms,
            effective_to_ms=effective_to_ms,
            source=source,
            quality=quality,
            metadata=metadata or {},
        )
        return await self._repository.save_asset_classification(fact)

    async def record_benchmark_holding(
        self,
        *,
        benchmark_id: str,
        symbol: str,
        period_start_ms: int,
        period_end_ms: int,
        weight: Decimal,
        period_return: Decimal,
        holding_id: str | None = None,
        source: str = "manual",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> BenchmarkHolding:
        fact = BenchmarkHolding(
            holding_id=holding_id or str(uuid.uuid4()),
            benchmark_id=benchmark_id,
            symbol=symbol,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            weight=weight,
            period_return=period_return,
            source=source,
            quality=quality,
            metadata=metadata or {},
        )
        return await self._repository.save_benchmark_holding(fact)

    async def list_benchmark_holdings(
        self,
        *,
        benchmark_id: str,
        period_start_ms: int,
        period_end_ms: int,
    ) -> list[BenchmarkHolding]:
        """List benchmark holdings for a specific period (public API for projectors)."""
        return await self._repository.list_benchmark_holdings(
            benchmark_id=benchmark_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )

    async def record_portfolio_holding_fact(
        self,
        *,
        run_id: str,
        symbol: str,
        period_start_ms: int,
        period_end_ms: int,
        weight: Decimal,
        period_return: Decimal,
        holding_id: str | None = None,
        source: str = "performance_service",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> PortfolioHoldingFact:
        await self._require_run(run_id)
        fact = PortfolioHoldingFact(
            holding_id=holding_id or str(uuid.uuid4()),
            run_id=run_id,
            symbol=symbol,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            weight=weight,
            period_return=period_return,
            source=source,
            quality=quality,
            metadata=metadata or {},
        )
        return await self._repository.save_portfolio_holding_fact(fact)

    async def list_portfolio_holding_facts(
        self,
        *,
        run_id: str,
        period_start_ms: int,
        period_end_ms: int,
    ) -> list[PortfolioHoldingFact]:
        """List portfolio holding facts for a specific period (public API for projectors)."""
        return await self._repository.list_portfolio_holding_facts(
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )

    async def calculate_brinson_attribution(
        self,
        *,
        run_id: str,
        benchmark_id: str,
        period_start_ms: int,
        period_end_ms: int,
        timestamp_ms: int | None = None,
    ) -> AttributionResult:
        run = await self._require_run(run_id)
        portfolio_holdings = await self._repository.list_portfolio_holding_facts(
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
        benchmark_holdings = await self._repository.list_benchmark_holdings(
            benchmark_id=benchmark_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
        classifications = await self._repository.list_asset_classifications(
            as_of_ms=period_start_ms,
        )
        if not portfolio_holdings or not benchmark_holdings:
            result = AttributionResult(
                attribution_id=str(uuid.uuid4()),
                scope="run",
                group_by="asset_class",
                timestamp_ms=timestamp_ms or _now_ms(),
                account_id=run.account_id,
                venue=run.venue,
                run_id=run.run_id,
                deployment_id=run.deployment_id,
                strategy_id=run.strategy_id,
                total_pnl=Decimal("0"),
                explained_pnl=Decimal("0"),
                residual_pnl=Decimal("0"),
                quality="partial",
                method="brinson_single_period_v2",
                metadata={
                    "benchmark_id": benchmark_id,
                    "period_start_ms": period_start_ms,
                    "period_end_ms": period_end_ms,
                    "portfolio_holdings_source": (
                        "available" if portfolio_holdings else "unavailable"
                    ),
                    "benchmark_holdings_source": (
                        "available" if benchmark_holdings else "unavailable"
                    ),
                },
            )
            return await self._repository.save_attribution_result(result)

        brinson = calculate_brinson_attribution(
            portfolio_holdings=portfolio_holdings,
            benchmark_holdings=benchmark_holdings,
            classifications=classifications,
        )
        items = [
            AttributionItem(
                key=str(item["asset_class"]),
                group_by="asset_class",
                pnl=item["active_return"],
                gross_pnl=item["active_return"],
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                fees=Decimal("0"),
                slippage=Decimal("0"),
                funding=Decimal("0"),
                contribution_return=item["active_return"],
                contribution_percent=(
                    _safe_div(
                        item["active_return"],
                        brinson["total_active_return"],
                    )
                    if brinson["total_active_return"]
                    else None
                ),
                metadata={
                    "portfolio_weight": item["portfolio_weight"],
                    "benchmark_weight": item["benchmark_weight"],
                    "portfolio_return": item["portfolio_return"],
                    "benchmark_return": item["benchmark_return"],
                    "allocation_effect": item["allocation_effect"],
                    "selection_effect": item["selection_effect"],
                    "interaction_effect": item["interaction_effect"],
                },
            )
            for item in brinson["items"]
        ]
        result = AttributionResult(
            attribution_id=str(uuid.uuid4()),
            scope="run",
            group_by="asset_class",
            timestamp_ms=timestamp_ms or _now_ms(),
            account_id=run.account_id,
            venue=run.venue,
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            total_pnl=brinson["total_active_return"],
            explained_pnl=brinson["total_active_return"],
            residual_pnl=Decimal("0"),
            items=items,
            quality=str(brinson["quality"]),
            method=str(brinson["method"]),
            metadata={
                "benchmark_id": benchmark_id,
                "period_start_ms": period_start_ms,
                "period_end_ms": period_end_ms,
                "portfolio_holding_count": len(portfolio_holdings),
                "benchmark_holding_count": len(benchmark_holdings),
                "classification_count": len(classifications),
                "allocation_effect": brinson["allocation_effect"],
                "selection_effect": brinson["selection_effect"],
                "interaction_effect": brinson["interaction_effect"],
            },
        )
        return await self._repository.save_attribution_result(result)

    async def calculate_brinson_linked_attribution(
        self,
        *,
        run_id: str,
        benchmark_id: str,
        periods: list[tuple[int, int]],
        timestamp_ms: int | None = None,
    ) -> AttributionResult:
        run = await self._require_run(run_id)
        period_results: list[dict[str, object]] = []
        missing_periods: list[dict[str, object]] = []
        for period_start_ms, period_end_ms in periods:
            portfolio_holdings = await self._repository.list_portfolio_holding_facts(
                run_id=run_id,
                period_start_ms=period_start_ms,
                period_end_ms=period_end_ms,
            )
            benchmark_holdings = await self._repository.list_benchmark_holdings(
                benchmark_id=benchmark_id,
                period_start_ms=period_start_ms,
                period_end_ms=period_end_ms,
            )
            if not portfolio_holdings or not benchmark_holdings:
                missing_periods.append(
                    {
                        "period_start_ms": period_start_ms,
                        "period_end_ms": period_end_ms,
                        "portfolio_holdings_source": (
                            "available" if portfolio_holdings else "unavailable"
                        ),
                        "benchmark_holdings_source": (
                            "available" if benchmark_holdings else "unavailable"
                        ),
                    }
                )
                continue
            classifications = await self._repository.list_asset_classifications(
                as_of_ms=period_start_ms,
            )
            period_result = calculate_brinson_attribution(
                portfolio_holdings=portfolio_holdings,
                benchmark_holdings=benchmark_holdings,
                classifications=classifications,
            )
            period_result["period_start_ms"] = period_start_ms
            period_result["period_end_ms"] = period_end_ms
            period_results.append(period_result)

        if missing_periods or not period_results:
            result = AttributionResult(
                attribution_id=str(uuid.uuid4()),
                scope="run",
                group_by="asset_class",
                timestamp_ms=timestamp_ms or _now_ms(),
                account_id=run.account_id,
                venue=run.venue,
                run_id=run.run_id,
                deployment_id=run.deployment_id,
                strategy_id=run.strategy_id,
                total_pnl=Decimal("0"),
                explained_pnl=Decimal("0"),
                residual_pnl=Decimal("0"),
                quality="partial",
                method="brinson_carino_linked_v2",
                metadata={
                    "benchmark_id": benchmark_id,
                    "period_count": len(periods),
                    "linked_period_count": len(period_results),
                    "missing_periods": missing_periods,
                    "linking_method": "carino",
                },
            )
            return await self._repository.save_attribution_result(result)

        linked = calculate_brinson_carino_linking(period_results)
        items = [
            AttributionItem(
                key=str(item["asset_class"]),
                group_by="asset_class",
                pnl=item["linked_active_return"],
                gross_pnl=item["linked_active_return"],
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                fees=Decimal("0"),
                slippage=Decimal("0"),
                funding=Decimal("0"),
                contribution_return=item["linked_active_return"],
                contribution_percent=(
                    _safe_div(
                        item["linked_active_return"],
                        linked["total_active_return"],
                    )
                    if linked["total_active_return"]
                    else None
                ),
                metadata={
                    "linked_allocation_effect": item["linked_allocation_effect"],
                    "linked_selection_effect": item["linked_selection_effect"],
                    "linked_interaction_effect": item["linked_interaction_effect"],
                    "period_count": item["period_count"],
                },
            )
            for item in linked["items"]
        ]
        explained = sum((item.contribution_return for item in items), Decimal("0"))
        total_active = linked["total_active_return"]
        result = AttributionResult(
            attribution_id=str(uuid.uuid4()),
            scope="run",
            group_by="asset_class",
            timestamp_ms=timestamp_ms or _now_ms(),
            account_id=run.account_id,
            venue=run.venue,
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            total_pnl=total_active,
            explained_pnl=explained,
            residual_pnl=total_active - explained,
            items=items,
            quality=str(linked["quality"]),
            method=str(linked["method"]),
            metadata={
                "benchmark_id": benchmark_id,
                "period_count": len(periods),
                "linked_period_count": len(period_results),
                "portfolio_total_return": linked["portfolio_total_return"],
                "benchmark_total_return": linked["benchmark_total_return"],
                "allocation_effect": linked["allocation_effect"],
                "selection_effect": linked["selection_effect"],
                "interaction_effect": linked["interaction_effect"],
                "linking_method": "carino",
            },
        )
        return await self._repository.save_attribution_result(result)

    async def record_factor_exposure(
        self,
        *,
        run_id: str,
        symbol: str,
        factor_name: str,
        period_start_ms: int,
        period_end_ms: int,
        exposure: Decimal,
        weight: Decimal,
        exposure_id: str | None = None,
        source: str = "manual",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> FactorExposureFact:
        await self._require_run(run_id)
        fact = FactorExposureFact(
            exposure_id=exposure_id or str(uuid.uuid4()),
            run_id=run_id,
            symbol=symbol,
            factor_name=factor_name,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            exposure=exposure,
            weight=weight,
            source=source,
            quality=quality,
            metadata=metadata or {},
        )
        return await self._repository.save_factor_exposure(fact)

    async def record_factor_return(
        self,
        *,
        factor_name: str,
        period_start_ms: int,
        period_end_ms: int,
        period_return: Decimal,
        return_id: str | None = None,
        source: str = "manual",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> FactorReturnFact:
        fact = FactorReturnFact(
            return_id=return_id or str(uuid.uuid4()),
            factor_name=factor_name,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            period_return=period_return,
            source=source,
            quality=quality,
            metadata=metadata or {},
        )
        return await self._repository.save_factor_return(fact)

    async def calculate_factor_attribution(
        self,
        *,
        run_id: str,
        period_start_ms: int,
        period_end_ms: int,
        timestamp_ms: int | None = None,
    ) -> AttributionResult:
        run = await self._require_run(run_id)
        exposures = await self._repository.list_factor_exposures(
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
        factor_returns = await self._repository.list_factor_returns(
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
        if not exposures:
            result = AttributionResult(
                attribution_id=str(uuid.uuid4()),
                scope="run",
                group_by="factor",
                timestamp_ms=timestamp_ms or _now_ms(),
                account_id=run.account_id,
                venue=run.venue,
                run_id=run.run_id,
                deployment_id=run.deployment_id,
                strategy_id=run.strategy_id,
                total_pnl=Decimal("0"),
                explained_pnl=Decimal("0"),
                residual_pnl=Decimal("0"),
                quality="partial",
                method="factor_return_attribution_v3",
                metadata={
                    "period_start_ms": period_start_ms,
                    "period_end_ms": period_end_ms,
                    "factor_exposures_source": "unavailable",
                },
            )
            return await self._repository.save_attribution_result(result)

        factor_result = calculate_factor_attribution(
            exposures=exposures,
            factor_returns=factor_returns,
        )
        version_metadata = self._factor_version_metadata(
            exposures=exposures,
            factor_returns=factor_returns,
        )
        items = [
            AttributionItem(
                key=str(item["factor_name"]),
                group_by="factor",
                pnl=item["contribution_return"],
                gross_pnl=item["contribution_return"],
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                fees=Decimal("0"),
                slippage=Decimal("0"),
                funding=Decimal("0"),
                contribution_return=item["contribution_return"],
                contribution_percent=(
                    _safe_div(
                        item["contribution_return"],
                        factor_result["total_factor_return"],
                    )
                    if factor_result["total_factor_return"]
                    else None
                ),
                metadata={
                    "weighted_exposure": item["weighted_exposure"],
                    "factor_return": item["factor_return"],
                    "weight": item["weight"],
                    "sample_count": item["sample_count"],
                    "model_versions": version_metadata["by_factor"][str(item["factor_name"])][
                        "model_versions"
                    ],
                    "feature_versions": version_metadata["by_factor"][str(item["factor_name"])][
                        "feature_versions"
                    ],
                },
            )
            for item in factor_result["items"]
        ]
        quality = str(factor_result["quality"])
        if version_metadata["missing"]:
            quality = "partial"
        result = AttributionResult(
            attribution_id=str(uuid.uuid4()),
            scope="run",
            group_by="factor",
            timestamp_ms=timestamp_ms or _now_ms(),
            account_id=run.account_id,
            venue=run.venue,
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            total_pnl=factor_result["total_factor_return"],
            explained_pnl=factor_result["total_factor_return"],
            residual_pnl=Decimal("0"),
            items=items,
            quality=quality,
            method=str(factor_result["method"]),
            metadata={
                "period_start_ms": period_start_ms,
                "period_end_ms": period_end_ms,
                "factor_exposure_count": len(exposures),
                "factor_return_count": len(factor_returns),
                "missing_factor_returns": factor_result["missing_factor_returns"],
                "model_versions": version_metadata["model_versions"],
                "feature_versions": version_metadata["feature_versions"],
                "source_traces": version_metadata["source_traces"],
                "missing_factor_version_metadata": version_metadata["missing"],
            },
        )
        return await self._repository.save_attribution_result(result)

    def _factor_version_metadata(
        self,
        *,
        exposures: list[FactorExposureFact],
        factor_returns: list[FactorReturnFact],
    ) -> dict[str, Any]:
        by_factor: dict[str, dict[str, set[str]]] = {}
        model_versions: set[str] = set()
        feature_versions: set[str] = set()
        source_traces: set[str] = set()
        missing: list[str] = []

        def collect(
            factor_name: str,
            metadata: dict[str, Any],
            missing_key: str,
        ) -> None:
            bucket = by_factor.setdefault(
                factor_name,
                {
                    "model_versions": set(),
                    "feature_versions": set(),
                    "source_traces": set(),
                },
            )
            model_version = str(metadata.get("model_version") or "").strip()
            feature_version = str(metadata.get("feature_version") or "").strip()
            source_trace = str(metadata.get("source_trace") or "").strip()
            if model_version:
                bucket["model_versions"].add(model_version)
                model_versions.add(model_version)
            if feature_version:
                bucket["feature_versions"].add(feature_version)
                feature_versions.add(feature_version)
            if source_trace:
                bucket["source_traces"].add(source_trace)
                source_traces.add(source_trace)
            if not model_version or not feature_version:
                missing.append(missing_key)

        for exposure in exposures:
            collect(
                exposure.factor_name,
                exposure.metadata,
                f"factor_exposure:{exposure.symbol}:{exposure.factor_name}",
            )
        for factor_return in factor_returns:
            collect(
                factor_return.factor_name,
                factor_return.metadata,
                f"factor_return:{factor_return.factor_name}",
            )

        return {
            "model_versions": sorted(model_versions),
            "feature_versions": sorted(feature_versions),
            "source_traces": sorted(source_traces),
            "missing": sorted(set(missing)),
            "by_factor": {
                factor_name: {
                    "model_versions": sorted(values["model_versions"]),
                    "feature_versions": sorted(values["feature_versions"]),
                    "source_traces": sorted(values["source_traces"]),
                }
                for factor_name, values in by_factor.items()
            },
        }

    async def record_risk_budget_trace(
        self,
        *,
        run_id: str,
        period_start_ms: int,
        period_end_ms: int,
        constraint_type: str,
        requested_notional: Decimal,
        allowed_notional: Decimal,
        final_notional: Decimal,
        decision: str,
        trace_id: str | None = None,
        source: str = "risk_sizing_engine",
        quality: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> RiskBudgetDecisionTrace:
        await self._require_run(run_id)
        fact = RiskBudgetDecisionTrace(
            trace_id=trace_id or str(uuid.uuid4()),
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
            constraint_type=constraint_type,
            requested_notional=requested_notional,
            allowed_notional=allowed_notional,
            final_notional=final_notional,
            decision=decision,
            source=source,
            quality=quality,
            metadata=metadata or {},
        )
        return await self._repository.save_risk_budget_trace(fact)

    async def calculate_risk_budget_attribution(
        self,
        *,
        run_id: str,
        period_start_ms: int,
        period_end_ms: int,
        timestamp_ms: int | None = None,
    ) -> AttributionResult:
        run = await self._require_run(run_id)
        traces = await self._repository.list_risk_budget_traces(
            run_id=run_id,
            period_start_ms=period_start_ms,
            period_end_ms=period_end_ms,
        )
        risk_result = calculate_risk_budget_attribution(traces)
        items = [
            AttributionItem(
                key=str(item["constraint_type"]),
                group_by="risk_budget_constraint",
                pnl=Decimal("0"),
                gross_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                fees=Decimal("0"),
                slippage=Decimal("0"),
                funding=Decimal("0"),
                contribution_return=Decimal("0"),
                contribution_percent=item["contribution_percent"],
                metadata={
                    "requested_notional": item["requested_notional"],
                    "allowed_notional": item["allowed_notional"],
                    "final_notional": item["final_notional"],
                    "avoided_notional": item["avoided_notional"],
                    "decision_count": item["decision_count"],
                },
            )
            for item in risk_result["items"]
        ]
        result = AttributionResult(
            attribution_id=str(uuid.uuid4()),
            scope="run",
            group_by="risk_budget_constraint",
            timestamp_ms=timestamp_ms or _now_ms(),
            account_id=run.account_id,
            venue=run.venue,
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            total_pnl=Decimal("0"),
            explained_pnl=Decimal("0"),
            residual_pnl=Decimal("0"),
            items=items,
            quality=str(risk_result["quality"]),
            method=str(risk_result["method"]),
            metadata={
                "period_start_ms": period_start_ms,
                "period_end_ms": period_end_ms,
                "risk_budget_trace_count": len(traces),
                "total_avoided_notional": risk_result["total_avoided_notional"],
                "risk_budget_traces_source": "available" if traces else "unavailable",
            },
        )
        return await self._repository.save_attribution_result(result)

    async def calculate_attribution(
        self,
        *,
        scope: str,
        group_by: str,
        timestamp_ms: int | None = None,
        run_id: str | None = None,
        account_id: str | None = None,
        venue: str | None = None,
    ) -> AttributionResult:
        entries = await self._repository.list_all_ledger_entries(
            run_id=run_id,
            account_id=account_id,
            venue=venue,
        )
        nav_points = await self._repository.list_all_nav_points(
            run_id=run_id,
            account_id=account_id,
            venue=venue,
        )
        latest_points = self._latest_nav_by_run(nav_points)
        latest_nav = max(latest_points, key=lambda p: p.timestamp_ms) if latest_points else None
        total_pnl = sum((point.total_pnl for point in latest_points), Decimal("0"))
        result_quality = "complete"
        result_metadata: dict[str, Any] = {}
        if scope == "account":
            account_nav = (
                await self._repository.get_latest_account_nav(account_id=account_id, venue=venue)
                if account_id
                else None
            )
            if account_nav is not None:
                total_pnl = account_nav.total_pnl
                result_quality = account_nav.quality
                result_metadata = {
                    "account_nav_id": account_nav.account_nav_id,
                    "account_nav_source": account_nav.source,
                    "reconciliation_residual": account_nav.reconciliation_residual,
                }
            else:
                result_quality = "partial"
                result_metadata = {"account_nav_source": "unavailable"}

        buckets: dict[str, dict[str, Decimal]] = {}
        bucket_metadata: dict[str, dict[str, Any]] = {}

        def bucket_for(key: str) -> dict[str, Decimal]:
            return buckets.setdefault(
                key,
                {
                    "pnl": Decimal("0"),
                    "gross_pnl": Decimal("0"),
                    "realized_pnl": Decimal("0"),
                    "unrealized_pnl": Decimal("0"),
                    "fees": Decimal("0"),
                    "slippage": Decimal("0"),
                    "funding": Decimal("0"),
                },
            )

        def metadata_for(key: str) -> dict[str, Any]:
            return bucket_metadata.setdefault(key, {})

        for entry in entries:
            key = self._bucket_key(entry, group_by)
            bucket = bucket_for(key)
            bucket["gross_pnl"] += entry.gross_pnl
            bucket["realized_pnl"] += entry.realized_pnl
            bucket["fees"] += entry.fee
            bucket["slippage"] += entry.slippage
            bucket["funding"] += entry.funding
            bucket["pnl"] += entry.realized_pnl + entry.funding - entry.fee - entry.slippage

        if group_by in {"strategy", "deployment"}:
            for point in latest_points:
                key = point.strategy_id if group_by == "strategy" else point.deployment_id
                bucket = bucket_for(key)
                metadata_for(key).update(
                    await self._allocation_metadata_for(
                        deployment_id=point.deployment_id,
                    )
                )
                bucket["unrealized_pnl"] += point.unrealized_pnl
                bucket["gross_pnl"] += point.unrealized_pnl
                bucket["pnl"] += point.unrealized_pnl
        elif group_by == "symbol":
            for point in latest_points:
                run_entries = [entry for entry in entries if entry.run_id == point.run_id]
                run = await self._require_run(point.run_id)
                state = self._replay_state(run, run_entries)
                marks = await self._repository.list_latest_mark_snapshots(
                    run_id=point.run_id,
                    as_of_ms=point.timestamp_ms,
                )
                mark_prices = {mark.symbol: mark.mark_price for mark in marks}
                lot_symbols = set(mark_prices)
                for symbol in lot_symbols:
                    lots = await self._open_lots_for(run.strategy_id, symbol)
                    if not lots:
                        continue
                    unrealized, open_qty = self._unrealized_from_lots(
                        lots,
                        mark_prices[symbol],
                    )
                    if open_qty <= 0:
                        continue
                    bucket = bucket_for(symbol)
                    metadata_for(symbol).update(
                        {
                            "position_lots_source": "position_lots",
                            "position_lot_count": len(lots),
                            "position_lot_open_qty": str(open_qty),
                        }
                    )
                    metadata_for(symbol).update(
                        await self._allocation_metadata_for(
                            deployment_id=point.deployment_id,
                            symbol=symbol,
                        )
                    )
                    bucket["unrealized_pnl"] += unrealized
                    bucket["gross_pnl"] += unrealized
                    bucket["pnl"] += unrealized
                for symbol, position in (state.positions or {}).items():
                    if position.qty <= 0 or symbol not in mark_prices:
                        continue
                    if metadata_for(symbol).get("position_lots_source") == "position_lots":
                        continue
                    unrealized = (mark_prices[symbol] - position.avg_cost) * position.qty
                    bucket = bucket_for(symbol)
                    metadata_for(symbol).setdefault("position_lots_source", "unavailable")
                    metadata_for(symbol).update(
                        await self._allocation_metadata_for(
                            deployment_id=point.deployment_id,
                            symbol=symbol,
                        )
                    )
                    bucket["unrealized_pnl"] += unrealized
                    bucket["gross_pnl"] += unrealized
                    bucket["pnl"] += unrealized

        explained = sum((bucket["pnl"] for bucket in buckets.values()), Decimal("0"))
        items = [
            AttributionItem(
                key=key,
                group_by=group_by,
                pnl=bucket["pnl"],
                gross_pnl=bucket["gross_pnl"],
                realized_pnl=bucket["realized_pnl"],
                unrealized_pnl=bucket["unrealized_pnl"],
                fees=bucket["fees"],
                slippage=bucket["slippage"],
                funding=bucket["funding"],
                contribution_return=_safe_div(
                    bucket["pnl"], total_pnl if total_pnl else Decimal("0")
                ),
                contribution_percent=_safe_div(bucket["pnl"], explained) if explained else None,
                metadata=bucket_metadata.get(key, {}),
            )
            for key, bucket in sorted(buckets.items())
        ]
        result = AttributionResult(
            attribution_id=str(uuid.uuid4()),
            scope=scope,
            group_by=group_by,
            timestamp_ms=timestamp_ms or _now_ms(),
            account_id=account_id,
            venue=venue,
            run_id=run_id,
            deployment_id=latest_nav.deployment_id if latest_nav else None,
            strategy_id=latest_nav.strategy_id if latest_nav else None,
            total_pnl=total_pnl,
            explained_pnl=explained,
            residual_pnl=total_pnl - explained,
            items=items,
            quality=result_quality,
            metadata=result_metadata,
        )
        return await self._repository.save_attribution_result(result)

    async def get_latest_attribution(
        self,
        *,
        scope: str,
        group_by: str,
        run_id: str | None = None,
        account_id: str | None = None,
    ) -> AttributionResult | None:
        return await self._repository.get_latest_attribution(
            scope=scope,
            group_by=group_by,
            run_id=run_id,
            account_id=account_id,
        )

    async def _open_lots_for(self, strategy_id: str, symbol: str) -> list[dict[str, Any]]:
        if self._position_lot_provider is None:
            return []
        provider = self._position_lot_provider
        result = provider.list_lots(strategy_id, symbol, open_only=True)
        lots = await _maybe_await(result)
        return [_as_mapping(lot) for lot in lots or []]

    def _unrealized_from_lots(
        self,
        lots: list[dict[str, Any]],
        mark_price: Decimal,
    ) -> tuple[Decimal, Decimal]:
        unrealized = Decimal("0")
        open_qty = Decimal("0")
        for lot in lots:
            remaining_qty = to_decimal(lot.get("remaining_qty"))
            if remaining_qty <= 0:
                continue
            fill_price = to_decimal(lot.get("fill_price"))
            unrealized += (mark_price - fill_price) * remaining_qty
            open_qty += remaining_qty
        return unrealized, open_qty

    async def _allocation_metadata_for(
        self,
        *,
        deployment_id: str,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        if self._allocation_provider is None:
            return {}
        metadata: dict[str, Any] = {"allocation_source": "allocation_traces"}
        provider = self._allocation_provider
        profile_getter = getattr(provider, "get_profile", None)
        if profile_getter is not None:
            profile = _as_mapping(await _maybe_await(profile_getter(deployment_id)))
            if profile:
                metadata["allocation_profile"] = {
                    "max_notional": profile.get("max_notional"),
                    "max_symbol_exposure": profile.get("max_symbol_exposure"),
                    "max_portfolio_weight": profile.get("max_portfolio_weight"),
                    "current_notional": profile.get("current_notional"),
                    "remaining_notional": profile.get("remaining_notional"),
                    "enabled": profile.get("enabled"),
                }
        trace_lister = getattr(provider, "list_traces", None)
        if trace_lister is None:
            return metadata
        traces = await _maybe_await(trace_lister(deployment_id, limit=100))
        trace_items = [_as_mapping(trace) for trace in traces or []]
        if symbol is not None:
            trace_items = [trace for trace in trace_items if trace.get("symbol") == symbol]
        if not trace_items:
            metadata["allocation_trace_source"] = "unavailable"
            return metadata
        latest = trace_items[-1]
        metadata["allocation_trace"] = {
            "trace_id": latest.get("trace_id"),
            "symbol": latest.get("symbol"),
            "allocation_decision": latest.get("allocation_decision"),
            "allocated_qty": latest.get("allocated_qty"),
            "final_order_qty": latest.get("final_order_qty"),
            "reject_or_clip_reason": latest.get("reject_or_clip_reason"),
        }
        return metadata

    async def _require_run(self, run_id: str) -> StrategyRun:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise PerformanceRunNotFoundError(f"Unknown performance run: {run_id}")
        return run

    def _run_lock(self, run_id: str) -> asyncio.Lock:
        lock = _RUN_PROJECTION_LOCKS.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            _RUN_PROJECTION_LOCKS[run_id] = lock
        return lock

    def _account_lock(self, account_lock_key: str) -> asyncio.Lock:
        lock = _ACCOUNT_PROJECTION_LOCKS.get(account_lock_key)
        if lock is None:
            lock = asyncio.Lock()
            _ACCOUNT_PROJECTION_LOCKS[account_lock_key] = lock
        return lock

    def _replay_state(
        self,
        run: StrategyRun,
        entries: list[AccountingLedgerEntry],
    ) -> LedgerState:
        state = LedgerState(cash=run.initial_capital)
        for entry in sorted(entries, key=lambda item: item.ts_ms):
            if entry.event_type == "FILL" and entry.symbol and entry.side:
                apply_fill_to_state(
                    state,
                    symbol=entry.symbol,
                    side=entry.side,
                    qty=entry.qty,
                    price=entry.price,
                    fee=entry.fee,
                )
            elif entry.event_type == "CASH_FLOW":
                state.cash += entry.cash_flow
                state.cash_flow += entry.cash_flow
            state.cash -= entry.slippage
            state.cash += entry.funding
            state.slippage += entry.slippage
            state.funding += entry.funding
        return state

    async def _save_snapshot(self, run: StrategyRun, nav_points: list[NAVPointV2]) -> None:
        latest = max(nav_points, key=lambda p: p.timestamp_ms)
        returns = calculate_daily_returns(nav_points)
        entries = await self._repository.list_all_ledger_entries(run_id=run.run_id)
        trade_stats = calculate_trade_statistics(entries)
        var_95, cvar_95 = calculate_historical_var_cvar(returns)
        fees = latest.fee
        snapshot = PerformanceSnapshot(
            snapshot_id=str(uuid.uuid4()),
            run_id=run.run_id,
            deployment_id=run.deployment_id,
            strategy_id=run.strategy_id,
            account_id=run.account_id,
            venue=run.venue,
            timestamp_ms=latest.timestamp_ms,
            equity=latest.equity,
            total_pnl=latest.total_pnl,
            net_return=latest.net_return,
            twr=latest.twr,
            mwr=latest.mwr,
            max_drawdown=calculate_max_drawdown(nav_points),
            sharpe=annualized_sharpe(returns),
            sortino=annualized_sortino(returns),
            calmar=_safe_div(latest.net_return, abs(calculate_max_drawdown(nav_points))),
            turnover=calculate_turnover(entries, nav_points),
            exposure=_safe_div(latest.position_value, latest.equity),
            fees=fees,
            slippage=latest.slippage,
            funding=latest.funding,
            realized_pnl=latest.realized_pnl,
            unrealized_pnl=latest.unrealized_pnl,
            win_rate=trade_stats["win_rate"],
            profit_factor=trade_stats["profit_factor"],
            avg_win=trade_stats["avg_win"],
            avg_loss=trade_stats["avg_loss"],
            payoff_ratio=trade_stats["payoff_ratio"],
            var_95=var_95,
            cvar_95=cvar_95,
        )
        await self._repository.save_performance_snapshot(snapshot)

    def _bucket_key(self, entry: AccountingLedgerEntry, group_by: str) -> str:
        if group_by == "symbol":
            return entry.symbol or "unattributed"
        if group_by == "strategy":
            return entry.strategy_id
        if group_by == "deployment":
            return entry.deployment_id
        return "unattributed"

    def _latest_nav_by_run(self, nav_points: list[NAVPointV2]) -> list[NAVPointV2]:
        latest_by_run: dict[str, NAVPointV2] = {}
        for point in nav_points:
            current = latest_by_run.get(point.run_id)
            if current is None or point.timestamp_ms > current.timestamp_ms:
                latest_by_run[point.run_id] = point
        return list(latest_by_run.values())


def get_performance_service() -> PerformanceService:
    from trader.adapters.persistence.position_repository import get_position_repository
    from trader.services.allocation_management import AllocationManagementService

    return PerformanceService(
        position_lot_provider=get_position_repository(),
        allocation_provider=AllocationManagementService(),
    )


def _safe_div(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator


def _normalize_cash_flow_amount(amount: Decimal, flow_type: str) -> Decimal:
    normalized_type = flow_type.lower()
    if normalized_type in {"withdrawal", "withdraw", "outflow", "redeem", "redemption"}:
        return -abs(amount)
    if normalized_type in {"deposit", "inflow", "subscription", "contribution"}:
        return abs(amount)
    return amount


def _normalize_fee_for_run_currency(
    *,
    fee: Decimal,
    fee_currency: str | None,
    run_currency: str,
    fee_in_run_currency: Decimal | None,
    fee_conversion_rate: Decimal | None,
    fee_conversion_source: str | None,
    metadata: dict[str, Any],
    quality: str,
) -> tuple[Decimal, str]:
    if fee == 0:
        return fee, quality

    normalized_fee_currency = fee_currency or run_currency
    if normalized_fee_currency == run_currency:
        metadata.setdefault("fee_conversion_source", "not_required")
        return fee, quality

    metadata.setdefault("fee_original_amount", str(fee))
    metadata.setdefault("fee_original_currency", normalized_fee_currency)
    metadata.setdefault("fee_converted_currency", run_currency)
    if fee_conversion_rate is not None:
        metadata.setdefault("fee_conversion_rate", str(fee_conversion_rate))

    if fee_in_run_currency is not None:
        metadata.setdefault("fee_conversion_source", fee_conversion_source or "provided")
        return fee_in_run_currency, quality

    metadata.setdefault("fee_conversion_source", "unavailable")
    return Decimal("0"), "partial"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return dict(value)


def _now_ms() -> int:
    return int(time.time() * 1000)


def normalize_decimal_mapping(values: dict[str, Any] | None) -> dict[str, Decimal]:
    if not values:
        return {}
    return {key: to_decimal(value) for key, value in values.items()}
