# ADR-002: Backtesting and Research Architecture Convergence

**Status:** Accepted  
**Date:** 2026-05-14  
**Deciders:** Quantitative Trading System Architecture Team

---

## Context

The repository contains historical QuantConnect Lean selection material, current
VectorBT implementation, and Qlib/Hermes research tooling. This created an
ambiguous architecture narrative: different documents described different
"primary" engines.

The current code truth is:

- `VectorBTAdapter` and `VectorBTAdapterWithRisk` are the implemented backtest
  path.
- Qlib tools are research/data/model/prediction utilities and do not execute
  orders.
- P7 requires backtests to call `RiskEngine.check_pre_trade()` before simulated
  execution.

## Decision

Adopt a three-layer architecture:

1. **Qlib Research Layer**  
   Produces factors, model artifacts, versioned predictions, and research
   proposals. It must not create executable orders.

2. **VectorBT Fast Backtest Layer**  
   Provides fast vectorized research backtests and risk-adjusted equity curves.
   It is the active implemented engine for current backtest workflows.

3. **EventDrivenRiskReplay Layer**  
   Future target for production-like account, OMS, order, and risk replay. It
   will reuse internal signals, market-neutral contracts, and the same
   `RiskEngine.check_pre_trade()` entrypoint.

QuantConnect Lean remains historical/legacy reference only. ADR-001 is
superseded and should not be used as the current implementation guide.

## Consequences

- New research signals must flow through:
  `Qlib predictions -> QlibToStrategyBridge -> internal Signal -> RiskEngine`.
- VectorBT is suitable for fast validation, parameter scans, and risk-adjusted
  performance views, but not full production-like replay semantics.
- Future A-share work should prioritize market-neutral data providers, trading
  calendars, cost models, and an event-driven replay layer rather than forcing
  Qlib or VectorBT to become the execution simulator.
- Legacy Lean files may remain until a separate cleanup task audits references
  and migration risk.

