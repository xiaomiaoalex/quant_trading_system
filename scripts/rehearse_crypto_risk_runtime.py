from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Sequence
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trader.core.application.plugins.crypto_pre_trade_risk_plugin import CryptoPreTradeRiskPlugin
from trader.core.application.risk_engine import (
    RejectionReason,
    RiskCheckResult,
    RiskConfig,
    RiskEngine,
    RiskLevel,
)
from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoFundingOIRiskMetrics,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoRiskBudget,
    CryptoRiskSnapshot,
    LeverageBracket,
    OpenOrderRisk,
)
from trader.core.domain.models.order import OrderSide
from trader.core.domain.models.risk_mode import RiskMode
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.rules.time_window_policy import TimeWindowConfig
from trader.core.domain.services.risk_mode_controller import RiskModeController
from trader.services.crypto_pre_trade_risk_audit import build_audited_crypto_pre_trade_risk_check

DEFAULT_SYMBOL = "BTCUSDT"


@dataclass(frozen=True, slots=True)
class RuntimeRehearsalScenarioResult:
    name: str
    ok: bool
    passed: bool
    rejection_reason: str | None
    order_attempted: bool
    audit_event_found: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeRehearsalReport:
    ok: bool
    scenarios: tuple[RuntimeRehearsalScenarioResult, ...]


class StaticSnapshotProvider:
    def __init__(self, snapshot: CryptoRiskSnapshot | Exception) -> None:
        self._snapshot = snapshot

    async def build(self, signal: Signal) -> CryptoRiskSnapshot:
        if isinstance(self._snapshot, Exception):
            raise self._snapshot
        return self._snapshot


class CapturingAuditRepository:
    def __init__(self, *, fail_append: bool = False) -> None:
        self.fail_append = fail_append
        self.events: list[Any] = []
        self.append_attempts = 0
        self.append_failures = 0

    async def append(self, event: Any) -> dict[str, Any]:
        self.append_attempts += 1
        if self.fail_append:
            self.append_failures += 1
            raise RuntimeError("simulated PG audit outage")
        self.events.append(event)
        return {
            "event_type": event.event_type,
            "trace_id": event.trace_id,
            "payload": event.payload,
        }


class FakeBroker:
    async def get_account(self) -> Any:
        return SimpleNamespace(
            total_equity=Decimal("10000"),
            available_cash=Decimal("100000"),
        )

    async def get_positions(self) -> list[Any]:
        return []


def d(value: str) -> Decimal:
    return Decimal(value)


def rehearsal_risk_config(*, max_order_rate: int = 100) -> RiskConfig:
    return RiskConfig(
        max_order_rate=max_order_rate,
        min_order_value=Decimal("1"),
        time_window_config=TimeWindowConfig(slots=[], default_coefficient=1.0),
    )


def base_signal(
    *,
    signal_id: str,
    signal_type: SignalType = SignalType.LONG,
    qty: str = "0.1",
) -> Signal:
    return Signal(
        signal_id=signal_id,
        strategy_name="p8_rehearsal",
        symbol=DEFAULT_SYMBOL,
        signal_type=signal_type,
        quantity=d(qty),
        price=d("20000"),
        metadata={"decision_trace_id": f"p8:{signal_id}"},
    )


def instrument_spec() -> CryptoInstrumentSpec:
    return CryptoInstrumentSpec(
        symbol=DEFAULT_SYMBOL,
        market_type=CryptoMarketType.USD_M_FUTURES,
        price_tick=d("0.10"),
        qty_step=d("0.001"),
        min_qty=d("0.001"),
        max_qty=d("100"),
        min_notional=d("10"),
        max_notional=d("1000000"),
    )


def leverage_bracket() -> LeverageBracket:
    return LeverageBracket(
        symbol=DEFAULT_SYMBOL,
        notional_floor=d("0"),
        notional_cap=d("50000"),
        initial_leverage=d("20"),
        maint_margin_ratio=d("0.004"),
    )


def account() -> CryptoAccountRisk:
    return CryptoAccountRisk(
        equity=d("10000"),
        available_balance=d("8000"),
        wallet_balance=d("10000"),
        margin_balance=d("10000"),
    )


def base_snapshot(
    *,
    mark_prices: dict[str, Decimal] | None = None,
    brackets: list[LeverageBracket] | None = None,
    open_orders: list[OpenOrderRisk] | None = None,
    risk_budget: CryptoRiskBudget | None = None,
    funding_oi_metrics: dict[str, CryptoFundingOIRiskMetrics] | None = None,
) -> CryptoRiskSnapshot:
    return CryptoRiskSnapshot(
        account=account(),
        instrument_specs={DEFAULT_SYMBOL: instrument_spec()},
        leverage_brackets={DEFAULT_SYMBOL: [leverage_bracket()] if brackets is None else brackets},
        positions=[],
        open_orders=open_orders or [],
        mark_prices={DEFAULT_SYMBOL: d("20000")} if mark_prices is None else mark_prices,
        risk_budget=risk_budget
        or CryptoRiskBudget(
            symbol_notional_caps={DEFAULT_SYMBOL: d("100000")},
            total_notional_cap=d("200000"),
            max_margin_ratio=d("0.8"),
        ),
        funding_oi_metrics=funding_oi_metrics or {},
    )


async def run_runtime_rehearsal() -> RuntimeRehearsalReport:
    scenarios = [
        await _plugin_rejection_scenario(
            name="mark_price_missing",
            signal=base_signal(signal_id="mark-price-missing"),
            snapshot=base_snapshot(mark_prices={}),
            expected_reason=RejectionReason.RISK_SYSTEM_ERROR,
        ),
        await _plugin_rejection_scenario(
            name="leverage_bracket_missing",
            signal=base_signal(signal_id="leverage-bracket-missing"),
            snapshot=base_snapshot(brackets=[]),
            expected_reason=RejectionReason.RISK_SYSTEM_ERROR,
        ),
        await _plugin_rejection_scenario(
            name="open_orders_spike",
            signal=base_signal(signal_id="open-orders-spike"),
            snapshot=base_snapshot(
                open_orders=[
                    OpenOrderRisk(
                        cl_ord_id="existing-open",
                        symbol=DEFAULT_SYMBOL,
                        side=OrderSide.BUY,
                        qty=d("0.5"),
                        filled_qty=d("0"),
                        price=d("20000"),
                    )
                ],
                risk_budget=CryptoRiskBudget(
                    symbol_notional_caps={DEFAULT_SYMBOL: d("10000")},
                    total_notional_cap=d("200000"),
                    max_margin_ratio=d("0.8"),
                ),
            ),
            expected_reason=RejectionReason.CRYPTO_OPEN_ORDER_EXPOSURE,
        ),
        await _plugin_rejection_scenario(
            name="funding_oi_data_stale",
            signal=base_signal(signal_id="funding-oi-data-stale"),
            snapshot=base_snapshot(
                risk_budget=CryptoRiskBudget(max_abs_funding_rate_z_score=d("2.0")),
                funding_oi_metrics={
                    DEFAULT_SYMBOL: CryptoFundingOIRiskMetrics(
                        symbol=DEFAULT_SYMBOL,
                        current_funding_rate=d("0.0001"),
                        funding_rate_z_score=None,
                        funding_data_stale=True,
                        funding_history_count=20,
                    )
                },
            ),
            expected_reason=RejectionReason.CRYPTO_FUNDING_OI_RISK,
        ),
        await _plugin_rejection_scenario(
            name="binance_source_timeout",
            signal=base_signal(signal_id="binance-source-timeout"),
            snapshot=TimeoutError("simulated Binance source timeout"),
            expected_reason=RejectionReason.RISK_SYSTEM_ERROR,
        ),
        await _duplicate_signal_rate_scenario(),
        await _close_only_open_signal_scenario(),
        await _pg_audit_unavailable_scenario(),
    ]
    return RuntimeRehearsalReport(
        ok=all(scenario.ok for scenario in scenarios),
        scenarios=tuple(scenarios),
    )


async def _plugin_rejection_scenario(
    *,
    name: str,
    signal: Signal,
    snapshot: CryptoRiskSnapshot | Exception,
    expected_reason: RejectionReason,
) -> RuntimeRehearsalScenarioResult:
    plugin = CryptoPreTradeRiskPlugin(StaticSnapshotProvider(snapshot))
    engine = RiskEngine(
        broker=FakeBroker(),
        config=rehearsal_risk_config(),
        pre_trade_plugins=[plugin],
    )

    async def check(candidate: Signal) -> RiskCheckResult:
        return await engine.check_pre_trade(candidate)

    return await _run_audited_scenario(
        name=name,
        signal=signal,
        check=check,
        expected_reason=expected_reason,
    )


async def _duplicate_signal_rate_scenario() -> RuntimeRehearsalScenarioResult:
    engine = RiskEngine(
        broker=FakeBroker(),
        config=rehearsal_risk_config(max_order_rate=1),
    )
    await engine.check_pre_trade(base_signal(signal_id="continuous-duplicate-warmup"))
    engine.record_order()
    signal = base_signal(signal_id="continuous-duplicate-signal")

    async def check(candidate: Signal) -> RiskCheckResult:
        return await engine.check_pre_trade(candidate)

    return await _run_audited_scenario(
        name="continuous_duplicate_signal",
        signal=signal,
        check=check,
        expected_reason=RejectionReason.MAX_ORDER_RATE,
    )


async def _close_only_open_signal_scenario() -> RuntimeRehearsalScenarioResult:
    controller = RiskModeController()
    controller.force_mode(RiskMode.CLOSE_ONLY, "p8 rehearsal", "system")
    signal = base_signal(signal_id="close-only-open-signal")

    async def check(candidate: Signal) -> RiskCheckResult:
        if candidate.is_open_signal() and not controller.can_open_new_position():
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=RejectionReason.RISK_MODE_CLOSE_ONLY,
                message="Risk mode CLOSE_ONLY blocks new positions",
                details={
                    "risk_mode": controller.mode.name,
                    "can_close_position": controller.can_close_position(),
                },
            )
        return RiskCheckResult(passed=True, risk_level=RiskLevel.LOW)

    return await _run_audited_scenario(
        name="close_only_open_signal",
        signal=signal,
        check=check,
        expected_reason=RejectionReason.RISK_MODE_CLOSE_ONLY,
    )


async def _pg_audit_unavailable_scenario() -> RuntimeRehearsalScenarioResult:
    signal = base_signal(signal_id="pg-audit-unavailable")

    async def check(candidate: Signal) -> RiskCheckResult:
        del candidate
        return RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
            message="Simulated PG audit unavailable scenario still rejects",
            details={"scenario": "pg_audit_unavailable"},
        )

    repo = CapturingAuditRepository(fail_append=True)
    audited = build_audited_crypto_pre_trade_risk_check(check)
    with patch(
        "trader.services.crypto_pre_trade_risk_audit.get_market_risk_audit_repository",
        return_value=repo,
    ):
        result = await audited(signal)

    errors = _validate_rejection_result(
        result=result,
        expected_reason=RejectionReason.RISK_SYSTEM_ERROR,
        audit_event_found=False,
        allow_missing_audit=True,
    )
    if repo.append_attempts != 1:
        errors.append(f"expected one audit append attempt, got {repo.append_attempts}")
    if repo.append_failures != 1:
        errors.append(f"expected one audit append failure, got {repo.append_failures}")
    return RuntimeRehearsalScenarioResult(
        name="pg_audit_unavailable",
        ok=not errors,
        passed=result.passed,
        rejection_reason=_reason_value(result.rejection_reason),
        order_attempted=result.passed,
        audit_event_found=False,
        evidence={
            "audit_append_attempted": repo.append_attempts > 0,
            "audit_append_attempts": repo.append_attempts,
            "audit_append_failed": repo.append_failures > 0,
            "audit_append_failures": repo.append_failures,
            "message": result.message,
        },
        errors=tuple(errors),
    )


async def _run_audited_scenario(
    *,
    name: str,
    signal: Signal,
    check: Callable[[Signal], Awaitable[RiskCheckResult]],
    expected_reason: RejectionReason,
) -> RuntimeRehearsalScenarioResult:
    repo = CapturingAuditRepository()
    audited = build_audited_crypto_pre_trade_risk_check(check)
    with patch(
        "trader.services.crypto_pre_trade_risk_audit.get_market_risk_audit_repository",
        return_value=repo,
    ):
        result = await audited(signal)

    audit_event_found = bool(repo.events)
    errors = _validate_rejection_result(
        result=result,
        expected_reason=expected_reason,
        audit_event_found=audit_event_found,
    )
    return RuntimeRehearsalScenarioResult(
        name=name,
        ok=not errors,
        passed=result.passed,
        rejection_reason=_reason_value(result.rejection_reason),
        order_attempted=result.passed,
        audit_event_found=audit_event_found,
        evidence={
            "message": result.message,
            "details": result.details,
            "audit_event_count": len(repo.events),
        },
        errors=tuple(errors),
    )


def _validate_rejection_result(
    *,
    result: RiskCheckResult,
    expected_reason: RejectionReason,
    audit_event_found: bool,
    allow_missing_audit: bool = False,
) -> list[str]:
    errors: list[str] = []
    if result.passed:
        errors.append("scenario unexpectedly passed; an order would be allowed")
    if result.rejection_reason != expected_reason:
        errors.append(
            f"expected rejection {expected_reason.value}, got {_reason_value(result.rejection_reason)}"
        )
    if not audit_event_found and not allow_missing_audit:
        errors.append("rejection audit event was not captured")
    return errors


def _reason_value(reason: RejectionReason | None) -> str | None:
    return reason.value if reason is not None else None


def report_to_dict(report: RuntimeRehearsalReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "scenarios": [
            {
                "name": scenario.name,
                "ok": scenario.ok,
                "passed": scenario.passed,
                "rejection_reason": scenario.rejection_reason,
                "order_attempted": scenario.order_attempted,
                "audit_event_found": scenario.audit_event_found,
                "evidence": scenario.evidence,
                "errors": list(scenario.errors),
            }
            for scenario in report.scenarios
        ],
    }


def format_human_report(report: RuntimeRehearsalReport) -> str:
    lines = [
        "Crypto Risk Runtime Fail-Closed Rehearsal",
        f"status: {'PASS' if report.ok else 'FAIL'}",
    ]
    for scenario in report.scenarios:
        lines.append(
            f"- {scenario.name}: {'PASS' if scenario.ok else 'FAIL'} "
            f"reason={scenario.rejection_reason} "
            f"order_attempted={scenario.order_attempted} "
            f"audit={scenario.audit_event_found}"
        )
        for error in scenario.errors:
            lines.append(f"  error: {error}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic Crypto Risk runtime fail-closed rehearsal scenarios."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = asyncio.run(run_runtime_rehearsal())
    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_human_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
