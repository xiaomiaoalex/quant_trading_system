# ADR-001: Backtesting Framework Selection

**Status:** Accepted  
**Date:** 2026-03-31  
**Deciders:** Quantitative Trading System Architecture Team  

---

## Title

Selection of QuantConnect Lean as Primary Backtesting Framework

---

## Status

**Accepted** — This ADR is finalized and approved for implementation.

---

## Context

The quantitative trading system requires a robust backtesting engine to support Phase 5 development. The backtesting framework is a critical component that must integrate with the system's five-plane architecture (Core Plane, Adapter Plane, Persistence Plane, Policy Plane, Control Plane) while adhering to core invariants: deterministic execution, absolute idempotency, and fail-closed error handling.

Four frameworks were evaluated:
- **Backtrader** (GPLv3) — Mature event-driven framework
- **VectorBT** (Proprietary) — Vectorized high-speed engine
- **Zipline** (Apache 2.0) — Institutional-grade framework
- **QuantConnect Lean** (Apache 2.0) — Production-grade engine

### Key Requirements

| Requirement | Priority |
|-------------|----------|
| Permissive license (Apache 2.0) | Critical |
| Event-driven execution model | Critical |
| Crypto-native asset support | High |
| Active community and maintenance | High |
| Multi-asset coverage | High |
| Order type completeness | Medium |
| Performance (scalability) | Medium |

---

## Decision

**Chosen Alternative:** QuantConnect Lean (Apache 2.0)

The decision is to adopt **QuantConnect Lean** as the primary backtesting framework for the quantitative trading system.

### Decision Factors

1. **License Compatibility (Apache 2.0)**
   - Permits commercial use and proprietary strategy development
   - No copyleft restrictions (unlike GPLv3)
   - Aligns with project's proprietary trading requirements

2. **Crypto-Native Support**
   - Native support for crypto assets (BTC, ETH, altcoins)
   - Aligns with project's focus on cryptocurrency trading
   - Built-in crypto exchange connectors

3. **Active Community and Maintenance**
   - Very Active maintenance status
   - QuantConnect backed development
   - 30 contributors, 18,169 stars
   - Regular updates and community support

4. **Feature Completeness**
   - Multi-asset: stocks, futures, options, forex, crypto, commodities
   - Order types: Market, Limit, Stop, StopLimit, MarketOnClose, OCO
   - Event-driven execution model
   - Built-in risk management and portfolio analytics

5. **Architecture Alignment**
   - Event-driven model compatible with Core Plane deterministic requirements
   - Modular adapter architecture supports Persistence Plane integration
   - Production-ready design supports Control Plane deployment

---

## Alternatives Considered

### 1. Backtrader (Rejected)

**Reason for Rejection:** GPLv3 licensing conflict.

| Factor | Assessment |
|--------|------------|
| License | GPLv3 — copyleft restrictions |
| Commercial Use | Requires legal review |
| Strategy Protection | Derivative works must be open source |

**Why Not:** The copyleft nature of GPLv3 is incompatible with proprietary strategy protection requirements. While Backtrader is technically excellent, the licensing constraint is a hard blocker for commercial deployment.

### 2. VectorBT (Rejected)

**Reason for Rejection:** Event-driven simulation not supported; proprietary license.

| Factor | Assessment |
|--------|------------|
| License | Proprietary — requires vendor verification |
| Execution Model | Vectorized only (no event-driven) |
| Order Types | Market orders only |
| Team Size | Solo maintainer (16 contributors) |

**Why Not:** VectorBT excels at speed (10-100x faster via vectorization) but lacks event-driven simulation required for complex order management. The proprietary license and solo maintainer present additional risk.

### 3. Zipline (Rejected)

**Reason for Rejection:** Quantopian shutdown created maintenance uncertainty.

| Factor | Assessment |
|--------|------------|
| License | Apache 2.0 — acceptable |
| Maintenance | Community-maintained, slower updates |
| Historical | Quantopian business closure |
| Windows Support | Known limitations |

**Why Not:** While Apache 2.0 licensed and feature-complete, the Quantopian shutdown in 2020 created lasting uncertainty about long-term maintenance. The community has not fully recovered momentum.

---

## Consequences

### Positive Consequences

| Consequence | Impact |
|-------------|--------|
| Proprietary strategy protection | Legal clarity for commercial deployment |
| Crypto-native support | Direct integration with crypto exchanges |
| Production-grade architecture | Designed for live trading from inception |
| Multi-language support | C# engine + Python algorithms |
| Active development | Regular updates and feature additions |
| Community support | Active forums and documentation |

### Negative Consequences

| Consequence | Mitigation |
|-------------|------------|
| Heavier framework (C# + Python) | Use Lean CLI for local backtesting; accept overhead |
| QC-specific data format | Build adapter layer for data transformation |
| Can be overkill for simple strategies | Leverage only required components |
| Learning curve for Lean CLI | Allocate ramp-up time in Phase 5 estimates |

### Risks and Mitigation Strategies

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| QC cloud dependency for data | Low | Medium | Use local Lean CLI with self-hosted data feeds |
| Framework complexity overhead | Medium | Low | Incremental adoption; start with core features |
| Long-term vendor lock-in | Low | Medium | Apache 2.0 allows self-hosting and modifications |
| Performance under extreme load | Low | Medium | Benchmark early; optimize hot paths |

---

## References

- QuantConnect Lean GitHub: https://github.com/QuantConnect/Lean
- QuantConnect Documentation: https://www.quantconnect.com/docs/
- Backtesting Framework Comparison Report: `docs/backtesting_framework_comparison.md`

---

## Change Log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-03-31 | Architecture Team | Initial ADR creation |

---

*This ADR was generated as part of Phase 5 backtesting engine selection.*
