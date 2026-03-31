# Backtesting Framework Comparison Report

**Document Version:** 1.0  
**Date:** 2026-03-31  
**Purpose:** Phase 5 backtesting engine selection  
**Related ADR:** [ADR-001-backtesting-framework-selection.md](./adr/ADR-001-backtesting-framework-selection.md)

---

## Executive Summary

This report evaluates four major Python-based backtesting frameworks for adoption as the primary backtesting engine. Each framework is assessed against criteria critical to quantitative trading system development, including feature completeness, performance, extensibility, and community support.

| Framework | Stars | License | Last Update | Contributors |
|-----------|-------|---------|-------------|--------------|
| Backtrader | 20,976 | GPLv3 | 2026-03-31 | 30 |
| VectorBT | 7,029 | Proprietary | 2026-03-31 | 16 |
| Zipline | 19,569 | Apache 2.0 | 2026-03-31 | 30 |
| QuantConnect Lean | 18,169 | Apache 2.0 | 2026-03-31 | 30 |

---

## 1. Backtrader

### 1.1 Overview
Python backtesting library for trading strategies, launched in 2015. One of the most mature Python backtesting solutions.

### 1.2 Repository Statistics
- **GitHub Stars:** 20,976
- **License:** GNU General Public License v3.0 (GPLv3)
- **Created:** January 10, 2015
- **Last Active:** March 31, 2026
- **Contributors:** 30

### 1.3 Documentation Quality
- **Rating:** Good
- Comprehensive official documentation
- Multiple tutorials and blog posts
- Active community forum
- Example strategies included

### 1.4 Feature Completeness

| Feature | Support |
|---------|---------|
| Multi-Asset | Yes (stocks, futures, forex, options, crypto) |
| Multi-Strategy | Yes (multiple strategies per cerebro instance) |
| Order Types | Market, Limit, Stop, StopLimit, StopTrail, OCO |
| Slippage Models | Volume-based, fixed, percentile |
| Commission Models | Stocks, futures, forex, options, crypto |
| Portfolio Optimization | Yes (via `bt.optimization`) |
| Event-Driven | Yes |

### 1.5 Performance Characteristics
- **Execution Model:** Event-driven
- **Speed:** Moderate (suitable for daily/sub-daily strategies)
- **Memory:** Standard Python memory footprint
- **Scalability:** Good for single-machine backtesting

### 1.6 Learning Curve
- **Difficulty:** Low to Moderate
- Well-documented with extensive examples
- Intuitive API design
- Good for beginners to quantitative trading

### 1.7 Customization/Extensibility
- Custom indicators easy to implement
- Broker integration possible
- Data feed customization supported
- Filter system for data processing

### 1.8 License Implications
**GPLv3 is a copyleft license.** This means:
- Any derivative works must also be released under GPLv3
- Not suitable if you need to keep proprietary strategies
- Commercial use requires careful legal review

### 1.9 Strengths
- Mature and stable codebase
- Excellent documentation
- Large community and examples
- Easy to extend with custom indicators

### 1.10 Weaknesses
- GPLv3 licensing restrictions
- Performance limited compared to vectorized approaches
- No built-in fundamental data support
- Limited to Python only

---

## 2. VectorBT

### 2.1 Overview
Lightning-fast backtesting engine leveraging pandas vectorization. Designed for speed and finding trading edges through rapid iteration.

### 2.2 Repository Statistics
- **GitHub Stars:** 7,029
- **License:** Proprietary (requires verification)
- **Created:** November 14, 2017
- **Last Active:** March 31, 2026
- **Contributors:** 16

### 2.3 Documentation Quality
- **Rating:** Good
- Comprehensive documentation website
- API reference available
- QuantStats integration for analysis
- Example notebooks provided

### 2.4 Feature Completeness

| Feature | Support |
|---------|---------|
| Multi-Asset | Yes |
| Multi-Strategy | Yes (portfolio-wide) |
| Order Types | Market only (vectorized execution) |
| Slippage Models | Percentage-based |
| Commission Models | Fixed, percentage |
| Portfolio Optimization | Yes (built-in optimization engine) |
| Event-Driven | No (vectorized) |

### 2.5 Performance Characteristics
- **Execution Model:** Vectorized (pandas/numpy)
- **Speed:** Excellent (10-100x faster than event-driven)
- **Memory:** Optimized for large datasets
- **Scalability:** Excellent for parameter scanning

### 2.6 Learning Curve
- **Difficulty:** Low
- Pandas-native interface
- Minimal code required for simple strategies
- Steeper for complex order management

### 2.7 Customization/Extensibility
- Highly modular design
- Flexible indicator system via `ta-lib` or custom
- Can be extended with custom signals
- Limited broker integration (paper trading only)

### 2.8 License Implications
**Proprietary license** must be verified. May restrict:
- Commercial usage
- Redistribution
- Modification rights

### 2.9 Strengths
- Exceptional speed via vectorization
- Excellent for strategy discovery
- Built-in optimization capabilities
- Modern, clean API

### 2.10 Weaknesses
- Event-driven simulation not supported
- Limited order types (market orders only)
- Proprietary license may restrict enterprise use
- Smaller community than alternatives

---

## 3. Zipline

### 3.1 Overview
Pythonic Algorithmic Trading Library, originally developed by Quantopian. One of the oldest Python backtesting frameworks with institutional adoption.

### 3.2 Repository Statistics
- **GitHub Stars:** 19,569
- **License:** Apache License 2.0
- **Created:** October 19, 2012
- **Last Active:** March 31, 2026
- **Contributors:** 30

### 3.3 Documentation Quality
- **Rating:** Good
- Official documentation (zipline.io)
- Algorithm templates provided
- In-depth tutorials available
- Active user community

### 3.4 Feature Completeness

| Feature | Support |
|---------|---------|
| Multi-Asset | Yes (stocks, futures, options, forex) |
| Multi-Strategy | Yes (via multiple algorithms) |
| Order Types | Market, Limit, Stop, StopLimit, MarketOnClose |
| Slippage Models | Volume-based, fixed, dollar volume |
| Commission Models | Per-share, percentage, futures-style |
| Portfolio Optimization | Via `alphastreams` extension |
| Event-Driven | Yes |

### 3.5 Performance Characteristics
- **Execution Model:** Event-driven
- **Speed:** Moderate (slower than vectorized approaches)
- **Memory:** Standard Python memory footprint
- **Scalability:** Good; can handle multiple algorithms

### 3.6 Learning Curve
- **Difficulty:** Moderate
- Pipeline API for data handling
- Requires understanding of institutional data formats
- Steeper than Backtrader for simple strategies

### 3.7 Customization/Extensibility
- Pipeline API for custom data processing
- Custom factors and transforms
- Integrates with `alphalens` for factor analysis
- `pyfolio` integration for performance analysis

### 3.8 License Implications
**Apache 2.0 is permissive:**
- Commercial use allowed
- Modification and redistribution permitted
- No patent litigation restrictions
- Suitable for proprietary trading systems

### 3.9 Strengths
- Apache 2.0 license (permissive)
- Institutional-grade architecture
- Excellent risk analytics via `pyfolio`
- Pipeline API for complex data workflows
- Integrates with `alphalens`, `pyfolio`, `empirical`

### 3.10 Weaknesses
- Quantopian's business closure created uncertainty
- Community maintenance may be slower
- Heavier dependencies
- Windows support limitations

---

## 4. QuantConnect (Lean)

### 4.1 Overview
Lean Algorithmic Trading Engine by QuantConnect. Production-grade engine powering the QuantConnect cloud platform.

### 4.2 Repository Statistics
- **GitHub Stars:** 18,169
- **License:** Apache License 2.0
- **Created:** November 28, 2014
- **Last Active:** March 31, 2026
- **Contributors:** 30

### 4.3 Documentation Quality
- **Rating:** Excellent
- Comprehensive documentation (quantconnect.com/docs)
- Algorithm cookbook
- Video tutorials
- Active community forums

### 4.4 Feature Completeness

| Feature | Support |
|---------|---------|
| Multi-Asset | Yes (stocks, futures, options, forex, crypto, commodities) |
| Multi-Strategy | Yes (portfolio-level multiple algorithms) |
| Order Types | Market, Limit, Stop, StopLimit, MarketOnClose, OCO |
| Slippage Models | Volume-based, fixed, custom |
| Commission Models | Per-trade, per-share, percentage, custom |
| Portfolio Optimization | Via custom algorithms |
| Event-Driven | Yes |

### 4.5 Performance Characteristics
- **Execution Model:** Event-driven
- **Speed:** Good
- **Memory:** Standard Python/C# memory footprint
- **Scalability:** Excellent (designed for cloud deployment)

### 4.6 Learning Curve
- **Difficulty:** Moderate
- Well-documented with examples
- QC cloud provides data
- Dual-language support (C#, Python)
- Algorithm framework is comprehensive

### 4.7 Customization/Extensibility
- Highly modular architecture
- Custom data sources
- Custom brokerages
- Algorithm framework extensible
- Open source engine modifications

### 4.8 License Implications
**Apache 2.0 is permissive:**
- Commercial use allowed
- Modification and redistribution permitted
- No patent litigation restrictions
- Suitable for proprietary trading systems

### 4.9 Strengths
- Apache 2.0 license (permissive)
- Multi-language support (C# and Python)
- Production-grade architecture
- Extensive asset class coverage
- Cloud platform available for live trading
- Active development and community

### 4.10 Weaknesses
- Heavier framework (C# + Python)
- QC-specific data format
- Local execution requires Lean CLI
- Can be overkill for simple strategies

---

## 5. Comparative Analysis

### 5.1 Feature Matrix

| Criteria | Backtrader | VectorBT | Zipline | QuantConnect Lean |
|----------|------------|----------|---------|-------------------|
| Multi-Asset | ✓ | ✓ | ✓ | ✓ |
| Multi-Strategy | ✓ | Limited | ✓ | ✓ |
| Order Types | 6+ | 1 | 5 | 6+ |
| Slippage Models | ✓ | ✓ | ✓ | ✓ |
| Commission Models | ✓ | ✓ | ✓ | ✓ |
| Optimization Built-in | ✓ | ✓ | Via extension | Via extension |
| Event-Driven | ✓ | ✗ | ✓ | ✓ |
| Vectorized Speed | ✗ | ✓ | ✗ | ✗ |

### 5.2 Community Activity Assessment

| Framework | Activity Level | Maintenance Status |
|-----------|----------------|-------------------|
| Backtrader | Moderate | Active (self-hosted) |
| VectorBT | Good | Active (solo maintainer) |
| Zipline | Moderate | Community-maintained |
| QuantConnect Lean | High | Very Active (QuantConnect backed) |

### 5.3 License Comparison

| Framework | License | Commercial Use | Proprietary Derivatives |
|-----------|---------|---------------|------------------------|
| Backtrader | GPLv3 | Requires review | Must be GPLv3 |
| VectorBT | Proprietary | Verify with vendor | Verify with vendor |
| Zipline | Apache 2.0 | ✓ | ✓ |
| QuantConnect Lean | Apache 2.0 | ✓ | ✓ |

### 5.4 Performance Comparison

| Framework | Speed | Best Use Case |
|-----------|-------|---------------|
| Backtrader | Moderate | Daily/bar-based strategies |
| VectorBT | Excellent | Strategy discovery, parameter scanning |
| Zipline | Moderate | Institutional research |
| QuantConnect Lean | Good | Production systems |

### 5.5 Integration with Existing Architecture

Based on the project's five-plane architecture:

| Framework | Core Plane | Adapter Plane | Control Plane |
|-----------|------------|---------------|---------------|
| Backtrader | Good fit | Requires adapter | Direct integration |
| VectorBT | Limited | Good fit | Requires adapter |
| Zipline | Good fit | Requires adapter | Direct integration |
| QuantConnect Lean | Good fit | Good fit | Direct integration |

---

## 6. Risk Assessment

### 6.1 Framework-Specific Risks

| Framework | Primary Risk |
|-----------|--------------|
| Backtrader | GPLv3 licensing may conflict with proprietary strategy protection |
| VectorBT | Solo maintainer; proprietary license may restrict enterprise use |
| Zipline | Quantopian shutdown created uncertainty; slower maintenance |
| QuantConnect Lean | QC cloud dependency for data; heavier framework |

### 6.2 Mitigation Strategies

- **GPLv3 Concerns:** Avoid Backtrader if proprietary strategy protection is critical
- **VectorBT:** Evaluate proprietary license terms before adoption
- **Zipline:** Consider long-term maintenance implications
- **QuantConnect Lean:** Leverage Apache 2.0 license benefits; use local Lean CLI

---

## 7. Recommendation

### 7.1 Primary Recommendation: **QuantConnect Lean**

**Rationale:**

1. **Apache 2.0 License:** Permissive licensing enables proprietary strategy development and commercialization without legal restrictions.

2. **Active Development:** Most active maintenance among compared frameworks, with QuantConnect's full backing.

3. **Feature Completeness:** Best coverage of asset classes (crypto-native support aligns with project focus), order types, and risk management features.

4. **Architecture Alignment:** Event-driven model matches the project's deterministic core plane requirements.

5. **Multi-Language Support:** C# engine with Python algorithms provides flexibility for team skill sets.

6. **Production Ready:** Designed for live trading from the start, with established cloud infrastructure.

### 7.2 Alternative Recommendation: **VectorBT**

If strategy discovery speed is paramount and event-driven simulation is not required:

1. **Speed Advantage:** 10-100x faster for parameter scanning and strategy discovery
2. **Modern API:** Pandas-native interface reduces learning curve
3. **Optimization Built-in:** No extension required

**Limitations:** Cannot simulate complex order types; proprietary license requires verification.

### 7.3 Not Recommended: **Backtrader**

GPLv3 licensing creates legal ambiguity for proprietary trading systems. The framework is excellent but the license incompatibility outweighs technical merits for commercial deployment.

### 7.4 Not Recommended: **Zipline**

While Apache 2.0 licensed and feature-complete, the Quantopian shutdown creates maintenance uncertainty. The framework has not recovered full community momentum.

---

## 8. Implementation Considerations

### 8.1 Migration Path
1. Begin with QuantConnect Lean local CLI
2. Validate performance characteristics against current system
3. Build adapter layer for five-plane architecture integration
4. Implement deterministic layer compatibility

### 8.2 Next Steps for Phase 5
1. Obtain legal review of QuantConnect Lean license terms
2. Prototype integration with existing OMS
3. Benchmark performance against project requirements
4. Evaluate data feed integration requirements

---

## Appendix A: Data Sources

| Framework | Stars (GitHub) | Contributors | License |
|-----------|---------------|---------------|---------|
| Backtrader | 20,976 | 30 | GPLv3 |
| VectorBT | 7,029 | 16 | Proprietary |
| Zipline | 19,569 | 30 | Apache 2.0 |
| QuantConnect Lean | 18,169 | 30 | Apache 2.0 |

*Data collected: March 31, 2026*

---

## Appendix B: References

- [Backtrader Documentation](https://www.backtrader.com/)
- [VectorBT Documentation](https://vectorbt.dev/)
- [Zipline Documentation](https://www.zipline.io/)
- [QuantConnect Documentation](https://www.quantconnect.com/docs/)

---

## Appendix C: ADR Reference

| Decision Record | Framework Selected | Status |
|-----------------|---------------------|--------|
| [ADR-001-backtesting-framework-selection.md](./adr/ADR-001-backtesting-framework-selection.md) | QuantConnect Lean | Accepted |
