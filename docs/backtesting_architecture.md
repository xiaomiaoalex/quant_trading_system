# Backtesting Framework Architecture Document

**Document Version:** 1.0  
**Date:** 2026-03-31  
**Status:** Approved  
**Framework:** QuantConnect Lean (Primary), VectorBT (Secondary)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Architecture](#2-component-architecture)
3. [Data Flow Diagrams](#3-data-flow-diagrams)
4. [Component Interactions](#4-component-interactions)
5. [Deployment Architecture](#5-deployment-architecture)
6. [Interface Contracts](#6-interface-contracts)
7. [Error Handling](#7-error-handling)
8. [Resource Requirements](#8-resource-requirements)

---

## 1. System Overview

### 1.1 Purpose

The backtesting framework provides historical simulation of trading strategies using QuantConnect Lean as the primary engine and VectorBT as a high-performance alternative for vectorized strategies. It integrates with the five-plane architecture while maintaining deterministic execution and absolute idempotency.

### 1.2 Design Principles

| Principle | Description |
|-----------|-------------|
| **Deterministic Execution** | Same inputs produce identical outputs across runs |
| **Port/Adapter Architecture** | Clean separation via抽象接口 |
| **Fail-Closed** | Errors trigger degradation, never silent pass |
| **Idempotent Operations** | Duplicate events handled via `cl_ord_id` + `exec_id` |

### 1.3 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BACKTESTING FRAMEWORK                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐     ┌──────────────────┐     ┌─────────────────────────┐   │
│  │   Control   │────▶│ StrategyLifecycle│────▶│    BacktestEnginePort   │   │
│  │   Plane     │     │    Manager       │     │    (Interface)          │   │
│  └─────────────┘     └──────────────────┘     └───────────┬─────────────┘   │
│                                                           │                 │
│                    ┌──────────────────────────────────────┼───────────────┐ │
│                    │                                      │               │ │
│                    ▼                                      ▼               ▼ │
│          ┌─────────────────┐                    ┌─────────────────┐ ┌────────┤
│          │  QuantConnect   │                    │    VectorBT     │ │Custom  │
│          │  Lean Adapter    │                    │    Adapter       │ │Adapter │
│          └─────────────────┘                    └─────────────────┘ └────────┘
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         DATA PROVIDER LAYER                          │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │   │
│  │  │ DataProvider│  │ FeatureStore│  │ Cache Layer │  │ PostgreSQL │  │   │
│  │  │    Port     │  │   Adapter   │  │  (Redis)    │  │   Adapter  │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                       RESULT REPORTER LAYER                          │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐  │   │
│  │  │ ResultReporter  │  │  Performance    │  │    Trade             │  │   │
│  │  │     Port        │  │    Analyzer     │  │    Analytics         │  │   │
│  │  └─────────────────┘  └─────────────────┘  └──────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.4 Integration with Five-Plane Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                        FIVE-PLANE ARCHITECTURE                       │
├──────────────┬─────────────────────────────────────────────────────┤
│  Control     │  BacktestingTriggered via /api/backtest endpoints   │
│  Plane       │  FastAPI Port 8080                                  │
├──────────────┼─────────────────────────────────────────────────────┤
│  Policy      │  RiskMetrics computed from backtest results          │
│  Plane       │  KillSwitch thresholds validated                     │
├──────────────┼─────────────────────────────────────────────────────┤
│  Persistence │  Backtest results persisted to PostgreSQL            │
│  Plane       │  Event logs replayed through backtest engine         │
├──────────────┼─────────────────────────────────────────────────────┤
│  Adapter     │  QuantConnect Lean Adapter (Primary)                 │
│  Plane       │  VectorBT Adapter (Secondary)                        │
│              │  Data transformations at adapter boundaries          │
├──────────────┼─────────────────────────────────────────────────────┤
│  Core        │  StrategyLifecycleManager (Monotonic State Machine)  │
│  Plane       │  Deterministic execution ensured                     │
└──────────────┴─────────────────────────────────────────────────────┘
```

---

## 2. Component Architecture

### 2.1 Core Components

#### StrategyLifecycleManager

Central coordinator managing the complete backtest lifecycle from initialization through completion.

```
┌─────────────────────────────────────────────────────────────┐
│              STRATEGY LIFECYCLE MANAGER                      │
├─────────────────────────────────────────────────────────────┤
│  States:                                                    │
│  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌───────────┐   │
│  │ INITIAL │──▶│ RUNNING │──▶│ COMPLETE│   │ FAILED    │   │
│  └─────────┘   └─────────┘   └─────────┘   └───────────┘   │
│      │            │                          ▲            │
│      └────────────┴──────────────────────────┘            │
│                     (error transitions)                     │
├─────────────────────────────────────────────────────────────┤
│  Responsibilities:                                          │
│  • Initialize backtest configuration                       │
│  • Coordinate engine execution                              │
│  • Aggregate results from multiple engines                  │
│  • Manage optimization iterations                           │
│  • Emit lifecycle events for monitoring                    │
└─────────────────────────────────────────────────────────────┘
```

**Key Methods:**
- `initialize(config: BacktestConfig) -> StrategyLifecycleManager`
- `execute() -> BacktestResult`
- `optimize(param_grid: ParameterGrid) -> OptimizationResult`
- `cancel() -> None`
- `get_state() -> LifecycleState`

#### BacktestEnginePort (Interface)

Abstract interface defining the contract for all backtest engines.

```
┌─────────────────────────────────────────────────────────────┐
│                    BACKTEST ENGINE PORT                      │
│                    (Abstract Interface)                      │
├─────────────────────────────────────────────────────────────┤
│  + run(BacktestConfig) -> BacktestResult                    │
│  + validate_config(Config) -> ValidationResult               │
│  + get_capabilities() -> EngineCapabilities                  │
│  + supports_order_types(List[OrderType]) -> bool            │
│  + get_required_data_resolution() -> Resolution            │
└─────────────────────────────────────────────────────────────┘
```

#### QuantConnect Lean Adapter

Primary backtest engine adapter implementing `BacktestEnginePort`.

```
┌─────────────────────────────────────────────────────────────┐
│              QUANTCONNECT LEAN ADAPTER                       │
│                    (Primary Engine)                          │
├─────────────────────────────────────────────────────────────┤
│  Framework: QuantConnect Lean (C# + Python)                 │
│  License: Apache 2.0                                         │
│  Execution Model: Event-driven                              │
├─────────────────────────────────────────────────────────────┤
│  Components:                                                │
│  ┌─────────────────┐  ┌─────────────────┐                 │
│  │ LeanCLIWrapper  │  │ AlgorithmLoader  │                 │
│  │ - spawns lean   │  │ - loads Python   │                 │
│  │   CLI process   │  │   algorithms     │                 │
│  │ - handles IPC   │  │ - validates      │                 │
│  └─────────────────┘  └─────────────────┘                 │
│                                                             │
│  ┌─────────────────┐  ┌─────────────────┐                 │
│  │ DataFeeder      │  │ ResultParser    │                 │
│  │ - streams       │  │ - parses Lean   │                 │
│  │   historical    │  │   JSON results  │                 │
│  │   data to Lean  │  │ - normalizes    │                 │
│  └─────────────────┘  └─────────────────┘                 │
├─────────────────────────────────────────────────────────────┤
│  Capabilities:                                              │
│  ✓ Event-driven execution                                   │
│  ✓ Multi-asset support (crypto, stocks, futures, options)  │
│  ✓ Full order type suite (Market, Limit, Stop, OCO)         │
│  ✓ Built-in risk management                                 │
│  ✓ Portfolio analytics                                      │
└─────────────────────────────────────────────────────────────┘
```

#### VectorBT Adapter

High-performance vectorized backtest engine adapter for simple strategies.

```
┌─────────────────────────────────────────────────────────────┐
│                 VECTORBT ADAPTER                             │
│                 (Secondary Engine)                           │
├─────────────────────────────────────────────────────────────┤
│  Framework: VectorBT (Python)                               │
│  License: Proprietary                                        │
│  Execution Model: Vectorized                                │
├─────────────────────────────────────────────────────────────┤
│  Use Cases:                                                 │
│  • Simple single-asset strategies                           │
│  • Parameter sweep optimization                             │
│  • Quick hypothesis testing                                 │
├─────────────────────────────────────────────────────────────┤
│  Limitations:                                               │
│  ✗ No event-driven simulation                               │
│  ✗ Market orders only                                       │
│  ✗ No complex order management                              │
├─────────────────────────────────────────────────────────────┤
│  Performance: 10-100x faster than event-driven engines      │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Data Provider Components

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA PROVIDER LAYER                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                  DataProviderPort                     │   │
│  │  + get_historical_data(params) -> DataFrame          │   │
│  │  + get_live_data(symbols) -> DataFrame               │   │
│  │  + validate_data_availability(symbols, dates) -> bool│   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                │
│                            ▼                                │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                   FeatureStore Adapter                │   │
│  │  Responsibilities:                                    │   │
│  │  • Fetch pre-computed features for backtest period   │   │
│  │  • Handle feature versioning                          │   │
│  │  • Transform features to engine-specific format      │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                │
│                            ▼                                │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                    Cache Layer                         │   │
│  │  (Redis)                                              │   │
│  │  • Cache historical data with TTL                    │   │
│  │  • Cache frequently accessed features                 │   │
│  │  • Invalidate on data source updates                 │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                │
│                            ▼                                │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                  PostgreSQL Adapter                   │   │
│  │  Responsibilities:                                    │   │
│  │  • Store backtest results                            │   │
│  │  • Persist optimization runs                          │   │
│  │  • Archive trade logs for compliance                 │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 Result Reporter Components

```
┌─────────────────────────────────────────────────────────────┐
│                    RESULT REPORTER LAYER                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                  ResultReporterPort                   │   │
│  │  + report(BacktestResult) -> Report                  │   │
│  │  + export_formats() -> List[Format]                  │   │
│  │  + generate_summary(result) -> Summary               │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                │
│                            ▼                                │
│         ┌──────────────────┼──────────────────┐             │
│         ▼                  ▼                  ▼             │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐       │
│  │ Performance │   │    Trade    │   │   Risk      │       │
│  │  Analyzer   │   │  Analytics  │   │   Metrics   │       │
│  └─────────────┘   └─────────────┘   └─────────────┘       │
│                                                             │
│  Output Formats:                                           │
│  • JSON (machine-readable)                                 │
│  • PDF (human-readable report)                             │
│  • CSV (trade log export)                                  │
│  • Plotly HTML (interactive charts)                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.4 Complete Component Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         BACKTESTING FRAMEWORK                                │
│                                                                              │
│  ╔════════════════════════════════════════════════════════════════════════╗  │
│  ║                    STRATEGY LIFECYCLE MANAGER                          ║  │
│  ║                         (Central Coordinator)                            ║  │
│  ╚════════════════════════════════════════════════════════════════════════╝  │
│                    │                           ▲                            │
│                    │                           │                            │
│                    ▼                           │                            │
│  ╔════════════════════════════════════════════════════════════════════════╗  │
│  ║                      BACKTEST ENGINE PORT                               ║  │
│  ║                      (Abstract Interface)                                ║  │
│  ╚════════════════════════════════════════════════════════════════════════╝  │
│                    │                                                         │
│         ┌──────────┴──────────┐                                           │
│         ▼                     ▼                                           │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐           │
│  │   QuantConnect  │   │    VectorBT     │   │     Custom      │           │
│  │   Lean Adapter  │   │     Adapter     │   │     Adapter     │           │
│  │   (Primary)      │   │   (Secondary)   │   │   (Extensible)  │           │
│  └─────────────────┘   └─────────────────┘   └─────────────────┘           │
│                                                                              │
│  ╔════════════════════════════════════════════════════════════════════════╗  │
│  ║                        DATA PROVIDER LAYER                              ║  │
│  ║  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────────┐ ║  │
│  ║  │ DataProviderPort│ │ FeatureStore    │ │      Cache Layer (Redis)    │ ║  │
│  ║  │   (Interface)   │ │    Adapter      │ │  ┌───────┐  ┌───────────┐  │ ║  │
│  ║  └─────────────────┘ └─────────────────┘ │  │ OHLCV │  │  Features  │  │ ║  │
│  ║                                          │  │ Cache │  │   Cache    │  │ ║  │
│  ║  ┌─────────────────────────────────────┐ │  └───────┘  └───────────┘  │ ║  │
│  ║  │         PostgreSQL Adapter          │ │       (TTL-based)        │   ║  │
│  ║  │  • Backtest Results                 │ └─────────────────────────────┘ ║  │
│  ║  │  • Optimization Runs                │                               ║  │
│  ║  │  • Trade Logs                       │                               ║  │
│  ║  └─────────────────────────────────────┘                               ║  │
│  ╚════════════════════════════════════════════════════════════════════════╝  │
│                                                                              │
│  ╔════════════════════════════════════════════════════════════════════════╗  │
│  ║                      RESULT REPORTER LAYER                               ║  │
│  ║  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────────┐ ║  │
│  ║  │ ResultReporterPort│ │ Performance    │ │      Trade Analytics       │ ║  │
│  ║  │   (Interface)   │ │   Analyzer      │ │                             │ ║  │
│  ║  └─────────────────┘ └─────────────────┘ └─────────────────────────────┘ ║  │
│  ║           │                                                         ║  │
│  ║           ▼                                                         ║  │
│  ║  ┌───────────────────────────────────────────────────────────────┐   ║  │
│  ║  │                    Export Formats                              │   ║  │
│  ║  │   JSON      PDF      CSV      Plotly HTML      Prometheus     │   ║  │
│  ║  └───────────────────────────────────────────────────────────────┘   ║  │
│  ╚════════════════════════════════════════════════════════════════════════╝  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Flow Diagrams

### 3.1 Backtest Execution Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        BACKTEST EXECUTION FLOW                               │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌──────────┐     ┌──────────────┐     ┌─────────────────┐     ┌────────────┐
   │  Client  │────▶│ FastAPI      │────▶│ StrategyLifecycle│────▶│ Backtest   │
   │ Request  │     │ /backtest    │     │ Manager         │     │ Engine     │
   └──────────┘     └──────────────┘     └─────────────────┘     └─────┬──────┘
                                                                       │
   ┌──────────────────────────────────────────────────────────────────────┘
   │
   ▼
   
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                         EXECUTION PHASES                                │
   ├─────────────────────────────────────────────────────────────────────────┤
   │                                                                          │
   │   Phase 1: Configuration Validation                                      │
   │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                 │
   │   │ Validate    │───▶│ Validate    │───▶│ Validate    │                 │
   │   │ Strategy    │    │ Data        │    │ Risk        │                 │
   │   │ Code        │    │ Availability│    │ Parameters  │                 │
   │   └─────────────┘    └─────────────┘    └─────────────┘                 │
   │         │                  │                  │                         │
   │         └──────────────────┴──────────────────┘                         │
   │                          │                                              │
   │                          ▼                                              │
   │   Phase 2: Data Loading                                                 │
   │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                 │
   │   │ Check Cache │───▶│ Fetch from   │───▶│ Transform   │                 │
   │   │             │    │ FeatureStore│    │ to Engine   │                 │
   │   │             │    │             │    │ Format      │                 │
   │   └─────────────┘    └─────────────┘    └─────────────┘                 │
   │         │                                       │                       │
   │         │         ┌─────────────┐               │                       │
   │         └────────▶│ Store in    │◀──────────────┘                       │
   │                   │ Cache       │                                      │
   │                   └─────────────┘                                      │
   │                                              │                          │
   │   Phase 3: Engine Execution                  ▼                          │
   │   ┌──────────────────────────────────────────────────────┐              │
   │   │              BACKTEST ENGINE (Lean/VectorBT)          │              │
   │   │  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐     │              │
   │   │  │Initialize│ │Emit   │  │Execute │  │Emit    │     │              │
   │   │  │Engine  │  │Events │  │Bars    │  │Trades  │     │              │
   │   │  └────────┘  └────────┘  └────────┘  └────────┘     │              │
   │   └──────────────────────────────────────────────────────┘              │
   │                          │                                              │
   │                          ▼                                              │
   │   Phase 4: Result Aggregation                                            │
   │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                 │
   │   │ Collect     │───▶│ Compute     │───▶│ Generate    │                 │
   │   │ Trades      │    │ Metrics     │    │ Reports     │                 │
   │   └─────────────┘    └─────────────┘    └─────────────┘                 │
   │         │                                       │                       │
   │         └──────────────────┬────────────────────┘                       │
   │                            ▼                                            │
   │   Phase 5: Persistence                                                  │
   │   ┌─────────────┐    ┌─────────────┐                                   │
   │   │ Persist to │    │ Emit        │                                   │
   │   │ PostgreSQL │    │ Completion  │                                   │
   │   └─────────────┘    └─────────────┘                                   │
   │                                                                          │
   └─────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                               ┌────────────┐
                               │   Client   │
                               │  Receives  │
                               │   Result   │
                               └────────────┘
```

### 3.2 Data Loading Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DATA LOADING FLOW                                   │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────┐
   │   Backtest  │
   │   Config    │
   └──────┬──────┘
          │
          │ symbols, start_date, end_date, resolution
          ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                        DATA LOADING PIPELINE                            │
   └─────────────────────────────────────────────────────────────────────────┘
   
          │
          ▼
   ┌─────────────────┐
   │  Check Cache    │◀────────────────────────────────────┐
   │  (Redis)        │                                     │
   └────────┬────────┘                                     │
            │ cache_hit                                    │
            ▼                                              │
   ┌─────────────────┐     ┌─────────────────┐             │
   │ FeatureStore    │────▶│  Transform      │             │
   │ Adapter         │     │  (Engine Format)│             │
   └────────┬────────┘     └────────┬────────┘             │
            │                       │                       │
            │ no_hit                │                       │
            ▼                       │                       │
   ┌─────────────────┐               │                       │
   │ Primary Data    │               │                       │
   │ Provider        │               │                       │
   │ (Binance, etc.) │               │                       │
   └────────┬────────┘               │                       │
            │                       │                       │
            └───────────┬───────────┘                       │
                        │                                   │
                        ▼                                   │
               ┌─────────────────┐                          │
               │ Store in Cache  │──────────────────────────┘
               │ (Redis, TTL=24h)│
               └─────────────────┘
                        │
                        ▼
               ┌─────────────────┐
               │ Validate Data   │
               │ Completeness     │
               └────────┬────────┘
                        │
                        ▼
               ┌─────────────────┐
               │ Return DataFrame │
               │ to Engine        │
               └─────────────────┘
```

### 3.3 Result Reporting Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        RESULT REPORTING FLOW                                 │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────┐
   │   Engine    │
   │   Results   │
   └──────┬──────┘
          │
          ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                      RESULT AGGREGATION                                  │
   └─────────────────────────────────────────────────────────────────────────┘
   
          │
          ├──────────────────────────────────────────────────────────────┐
          │                                                              │
          ▼                                                              ▼
   ┌─────────────┐                                                ┌─────────────┐
   │  Trade Log  │                                                │  Equity     │
   │  Processor │                                                │  Curve      │
   └──────┬──────┘                                                └──────┬──────┘
          │                                                             │
          ▼                                                             ▼
   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         ┌─────────────┐
   │  Trade      │    │  Order      │    │  Position   │         │  Performance│
   │  Analytics  │    │  Analysis   │    │  Timeline   │         │  Metrics    │
   └─────────────┘    └─────────────┘    └─────────────┘         └─────────────┘
          │                                                             │
          └─────────────────────────┬───────────────────────────────────┘
                                    │
                                    ▼
                           ┌─────────────────┐
                           │  ResultReporter │
                           │     Port        │
                           └────────┬────────┘
                                    │
          ┌──────────────────────────┼──────────────────────────┐
          │                          │                          │
          ▼                          ▼                          ▼
   ┌─────────────┐           ┌─────────────┐           ┌─────────────┐
   │    JSON     │           │    PDF      │           │   Plotly    │
   │   Report    │           │   Report    │           │    HTML     │
   └─────────────┘           └─────────────┘           └─────────────┘
          │                          │                          │
          ▼                          ▼                          ▼
   ┌─────────────┐           ┌─────────────┐           ┌─────────────┐
   │  PostgreSQL │           │  Filesystem │           │   Web UI    │
   │  Archive    │           │  Storage    │           │  Display    │
   └─────────────┘           └─────────────┘           └─────────────┘
```

### 3.4 Optimization Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          OPTIMIZATION FLOW                                   │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────┐
   │   Parameter │
   │   Grid      │
   └──────┬──────┘
          │
          │ generate parameter combinations
          ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                    OPTIMIZATION COORDINATOR                             │
   └─────────────────────────────────────────────────────────────────────────┘
   
          │
          ▼
   ┌─────────────────┐
   │ Generate Batch  │
   │ (N parameter    │
   │  combinations)  │
   └────────┬────────┘
            │
            │ parallel execution
            ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                      PARALLEL ENGINE INSTANCES                         │
   ├─────────────────────────────────────────────────────────────────────────┤
   │                                                                          │
   │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
   │   │  Instance 1 │  │  Instance 2 │  │  Instance 3 │  │  Instance N │    │
   │   │  (params:   │  │  (params:   │  │  (params:   │  │  (params:   │    │
   │   │   p1, p2)   │  │   p1, p3)   │  │   p2, p2)   │  │   pN, pM)   │    │
   │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘    │
   │          │               │               │               │            │
   │          └───────────────┴───────────────┴───────────────┘            │
   │                                  │                                      │
   │                                  ▼                                      │
   │   ┌───────────────────────────────────────────────────────────────┐    │
   │   │                    RESULT COLLECTOR                            │    │
   │   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │    │
   │   │  │ Sharpe     │  │  MaxDD      │  │  WinRate    │  ...        │    │
   │   │  │  Ratio     │  │             │  │             │             │    │
   │   │  └─────────────┘  └─────────────┘  └─────────────┘             │    │
   │   └───────────────────────────────────────────────────────────────┘    │
   │                                  │                                      │
   └──────────────────────────────────┼──────────────────────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────┐
                           │ Optimization    │
                           │ Result Selector │
                           │ (pareto/n-best) │
                           └────────┬────────┘
                                    │
                                    ▼
                           ┌─────────────────┐
                           │ Best Parameters │
                           │ Persisted       │
                           └─────────────────┘
```

---

## 4. Component Interactions

### 4.1 Communication Patterns

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        COMMUNICATION PATTERNS                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. DIRECT CALL (Synchronous)                                              │
│  ┌──────────┐         ┌──────────┐                                         │
│  │ Client   │────────▶│  Manager │                                         │
│  │          │◀────────│          │                                         │
│  └──────────┘  Result  └──────────┘                                         │
│                                                                             │
│  2. PORT/ADAPTER (Polymorphic)                                              │
│  ┌──────────┐         ┌──────────┐         ┌──────────┐                     │
│  │ Manager  │────────▶│  Port    │────────▶│ Adapter │                     │
│  │          │         │(Interface│         │(Impl)   │                     │
│  └──────────┘         └──────────┘         └──────────┘                     │
│                                                                             │
│  3. EVENT-BASED (Async)                                                     │
│  ┌──────────┐         ┌──────────┐         ┌──────────┐                     │
│  │ Engine   │────────▶│ Event    │────────▶│ Handler  │                     │
│  │          │  Event  │  Bus     │  Event  │          │                     │
│  └──────────┘         └──────────┘         └──────────┘                     │
│                                                                             │
│  4. DATA FLOW (Pipeline)                                                    │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │ Source   │───▶│ Transform│───▶│  Cache   │───▶│  Engine  │              │
│  │          │    │          │    │          │    │          │              │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Interface Contracts

#### IBacktestEngine

```python
@dataclass(slots=True)
class BacktestEngineCapabilities:
    supports_event_driven: bool
    supports_vectorized: bool
    supported_order_types: list[OrderType]
    supported_assets: list[AssetClass]
    max_concurrent_runs: int
    supports_optimization: bool

@dataclass(slots=True)
class BacktestConfig:
    strategy_code: str
    symbols: list[str]
    start_date: datetime
    end_date: datetime
    resolution: Resolution
    initial_capital: Decimal
    commission_model: CommissionModel
    risk_free_rate: Decimal
    benchmark_symbol: str | None

@dataclass(slots=True)
class BacktestResult:
    backtest_id: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    sharpe_ratio: Decimal
    max_drawdown: Decimal
    max_drawdown_duration: timedelta
    total_return: Decimal
    annualized_return: Decimal
    volatility: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    trade_log: list[Trade]
    equity_curve: list[EquityPoint]
    equity_drawdown: list[DrawdownPoint]
    execution_time_ms: int
    engine: str
    parameters: dict

class IBacktestEngine(Protocol):
    async def run(self, config: BacktestConfig) -> BacktestResult:
        ...
    
    async def validate_config(self, config: BacktestConfig) -> ValidationResult:
        ...
    
    def get_capabilities(self) -> BacktestEngineCapabilities:
        ...
    
    def supports_order_type(self, order_type: OrderType) -> bool:
        ...
```

#### IDataProvider

```python
@dataclass(slots=True)
class HistoricalDataRequest:
    symbols: list[str]
    start_date: datetime
    end_date: datetime
    resolution: Resolution
    include_trades: bool
    include_quotes: bool

@dataclass(slots=True)
class DataValidationResult:
    is_valid: bool
    missing_symbols: list[str]
    missing_dates: list[datetime]
    data_gaps: list[DataGap]

class IDataProvider(Protocol):
    async def get_historical_data(
        self, request: HistoricalDataRequest
    ) -> dict[str, pd.DataFrame]:
        ...
    
    async def validate_data_availability(
        self, request: HistoricalDataRequest
    ) -> DataValidationResult:
        ...
    
    async def get_live_data(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        ...
```

#### IResultReporter

```python
@dataclass(slots=True)
class PerformanceMetrics:
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    max_drawdown: Decimal
    max_drawdown_duration: timedelta
    annualized_return: Decimal
    volatility: Decimal
    value_at_risk: Decimal

@dataclass(slots=True)
class BacktestReport:
    report_id: str
    backtest_id: str
    created_at: datetime
    metrics: PerformanceMetrics
    trade_log_path: str
    equity_curve_path: str
    charts: dict[str, str]  # chart_type -> file_path

class IResultReporter(Protocol):
    async def report(self, result: BacktestResult) -> BacktestReport:
        ...
    
    def export_json(self, result: BacktestResult, path: Path) -> None:
        ...
    
    def export_pdf(self, report: BacktestReport, path: Path) -> None:
        ...
    
    def export_csv(self, result: BacktestResult, path: Path) -> None:
        ...
```

### 4.3 Error Handling Paths

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ERROR HANDLING PATHS                                │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │                         ERROR CLASSIFICATION                            │
   └─────────────────────────────────────────────────────────────────────────┘
   
          │
          ├──────────────────┬──────────────────┬───────────────────────────┐
          │                  │                  │                           │
          ▼                  ▼                  ▼                           ▼
   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐           ┌─────────────┐
   │   DATA      │    │  ENGINE     │    │   STATE     │           │   SYSTEM    │
   │   ERRORS    │    │   ERRORS    │    │   ERRORS    │           │   ERRORS    │
   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘           └──────┬──────┘
          │                  │                  │                           │
          ▼                  ▼                  ▼                           ▼
   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐           ┌─────────────┐
   │ MissingData │    │ Engine      │    │ Invalid     │           │ OutOfMemory │
   │ DataGap     │    │ Crash       │    │ StateTrans  │           │ DiskFull    │
   │ InvalidTick │    │ Timeout     │    │ Idempotency │           │ NetworkDown │
   └──────┬──────┘    │ OOM         │    │ Violation   │           │ DatabaseDown│
          │           └──────┬──────┘    └──────┬──────┘           └──────┬──────┘
          │                  │                  │                           │
          └──────────────────┼──────────────────┼───────────────────────────┘
                             │                  │
                             ▼                  ▼
                    ┌─────────────────┐  ┌─────────────────┐
                    │  FAIL-CLOSED   │  │   DEGRADED      │
                    │    HANDLER     │  │     MODE        │
                    └────────┬───────┘  └────────┬────────┘
                             │                   │
                             ▼                   ▼
                    ┌─────────────────┐  ┌─────────────────┐
                    │ • Log error     │  │ • Switch engine │
                    │ • Persist state │  │ • Reduce scope  │
                    │ • Emit failure  │  │ • Retry w/bkoff │
                    │ • Alert ops    │  │ • Continue      │
                    └─────────────────┘  └─────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │                        KILL SWITCH ESCALATION                           │
   └─────────────────────────────────────────────────────────────────────────┘
   
          │
          │ Error Severity
          ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                                                                          │
   │   NORMAL(0) ─────▶ NO_NEW_POSITIONS(1) ─────▶ CANCEL_ALL_AND_HALT(2)   │
   │                                                                      │   │
   │                                                                      ▼   │
   │                                                      LIQUIDATE_AND_DISCONNECT(3)
   │                                                                          │
   │   Backtest failures:                                                    │
   │   • Data error ──▶ Retry with fallback data source                     │
   │   • Engine crash ──▶ Switch to backup engine                           │
   │   • State error ──▶ Halt and persist state for manual review           │
   │                                                                          │
   └─────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Deployment Architecture

### 5.1 Integration with Main Trading System

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DEPLOYMENT ARCHITECTURE                                   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                           TRADING SYSTEM                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │ Control     │  │ Policy      │  │ Persistence │  │ Core        │       │
│  │ Plane       │  │ Plane       │  │ Plane       │  │ Plane       │       │
│  │ (FastAPI)   │  │ (Risk)      │  │ (PostgreSQL)│  │ (OMS)       │       │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────────────┘       │
│         │                │                │                                │
└─────────┼────────────────┼────────────────┼────────────────────────────────┘
          │                │                │
          │ REST/WebSocket │ Event Log      │ Trade Data
          │                │                │
──────────┼────────────────┼────────────────┼────────────────────────────────
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         BACKTESTING SYSTEM                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │ Backtest    │  │ Strategy    │  │ Data        │  │ Result      │       │
│  │ API Server  │  │ Lifecycle   │  │ Provider    │  │ Reporter    │       │
│  │ (FastAPI)   │  │ Manager     │  │ Layer       │  │             │       │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────────────┘       │
│         │                │                │                                │
│         │                │         ┌──────┴──────┐                         │
│         │                │         ▼             ▼                         │
│         │                │  ┌─────────────┐ ┌─────────────┐                │
│         │                │  │ Lean CLI    │ │ VectorBT    │                │
│         │                │  │ Process     │ │ Process     │                │
│         │                │  └─────────────┘ └─────────────┘                │
│         │                │                                                │
│         │                └──────────────────────────────────────────────┐  │
│         │                                                                │  │
│         │         ┌─────────────────────────────────────────────┐      │  │
│         │         │              Data Infrastructure             │      │  │
│         │         │  ┌─────────────┐  ┌─────────────┐            │      │  │
│         │         │  │   Redis     │  │ PostgreSQL  │            │      │  │
│         │         │  │   Cache     │  │   (Shared)  │            │      │  │
│         │         │  └─────────────┘  └─────────────┘            │      │  │
│         │         └─────────────────────────────────────────────┘      │  │
│         │                                                                │  │
│         └────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │                         NETWORK TOPOLOGY                                 │
   └─────────────────────────────────────────────────────────────────────────┘
   
   ┌──────────────────┐          ┌──────────────────┐
   │   Developer      │          │   Production    │
   │   Workstation   │          │   Server        │
   │                  │          │                  │
   │  • Lean CLI      │          │  • Lean CLI      │
   │  • VectorBT      │◀────────▶│  • VectorBT      │
   │  • Local Redis   │   SSH    │  • Shared Redis  │
   │  • Local PG      │          │  • Shared PG     │
   └──────────────────┘          └──────────────────┘
           │                              │
           │                              │
           └──────────────┬───────────────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │   Data Sources   │
                 │   • Binance API  │
                 │   • Custom Feed   │
                 │   • Historical DB│
                 └──────────────────┘
```

### 5.2 Container Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CONTAINER ARCHITECTURE                               │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        docker-compose.yml                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  services:                                                                 │
│    backtest-api:                                                            │
│      build: ./backtest-service                                              │
│      ports:                                                                  │
│        - "8081:8080"                                                         │
│      environment:                                                            │
│        - REDIS_URL=redis://redis:6379                                       │
│        - POSTGRES_URL=postgresql://trader:@postgres:5432/trading            │
│        - LEAN_CLI_PATH=/app/lean-cli                                        │
│      depends_on:                                                             │
│        - redis                                                               │
│        - postgres                                                            │
│      volumes:                                                                │
│        - ./strategies:/app/strategies                                        │
│        - ./results:/app/results                                              │
│                                                                             │
│    lean-engine-1:                                                            │
│      build: ./lean-engine                                                    │
│      environment:                                                            │
│        - ENGINE_ID=1                                                         │
│        - REDIS_URL=redis://redis:6379                                        │
│      deploy:                                                                 │
│        replicas: 2                                                           │
│                                                                             │
│    vectorbt-engine:                                                          │
│      build: ./vectorbt-engine                                                │
│      environment:                                                            │
│        - REDIS_URL=redis://redis:6379                                       │
│      deploy:                                                                 │
│        replicas: 2                                                           │
│                                                                             │
│    redis:                                                                    │
│      image: redis:7-alpine                                                   │
│      volumes:                                                                │
│        - redis-data:/data                                                    │
│                                                                             │
│    postgres:                                                                 │
│      image: postgres:15                                                       │
│      volumes:                                                                │
│        - pg-data:/var/lib/postgresql/data                                   │
│      environment:                                                            │
│        - POSTGRES_DB=trading                                                 │
│        - POSTGRES_USER=trader                                                │
│        - POSTGRES_PASSWORD_FILE=/run/secrets/pg_password                    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Interface Contracts

### 6.1 StrategyLifecycleManager API

```python
class StrategyLifecycleManager:
    @dataclass(slots=True)
    class Config:
        engine_type: Literal["lean", "vectorbt", "auto"]
        strategy_path: Path
        symbols: list[str]
        start_date: datetime
        end_date: datetime
        resolution: Resolution
        initial_capital: Decimal
        parameters: dict
        optimization_config: OptimizationConfig | None
    
    async def initialize(self, config: Config) -> "StrategyLifecycleManager":
        """Initialize the manager with configuration."""
    
    async def execute(self) -> BacktestResult:
        """Execute a single backtest."""
    
    async def optimize(
        self, parameter_grid: dict[str, list[Any]]
    ) -> OptimizationResult:
        """Execute parameter optimization."""
    
    async def cancel(self) -> None:
        """Cancel running backtest/optimization."""
    
    def get_state(self) -> LifecycleState:
        """Get current lifecycle state."""
    
    def on_event(self, handler: Callable[[LifecycleEvent], None]) -> None:
        """Register for lifecycle events."""
```

### 6.2 BacktestEnginePort Contract

```python
class BacktestEnginePort(Protocol):
    @property
    def name(self) -> str:
        """Engine identifier."""
    
    @property
    def capabilities(self) -> BacktestEngineCapabilities:
        """Engine capabilities."""
    
    async def run(
        self,
        strategy_module: str,
        config: BacktestConfig,
        data_provider: IDataProvider,
    ) -> BacktestResult:
        """Execute a backtest with given strategy and configuration."""
        raise NotImplementedError
    
    async def validate_strategy(
        self, strategy_code: str
    ) -> ValidationResult:
        """Validate strategy code before execution."""
        raise NotImplementedError
    
    async def validate_config(
        self, config: BacktestConfig
    ) -> ValidationResult:
        """Validate backtest configuration."""
        raise NotImplementedError
    
    def supports_feature(self, feature: str) -> bool:
        """Check if engine supports a specific feature."""
        raise NotImplementedError
```

### 6.3 Data Provider Contract

```python
class DataProviderPort(Protocol):
    async def get_historical_data(
        self,
        symbols: list[str],
        start_date: datetime,
        end_date: datetime,
        resolution: Resolution,
    ) -> dict[str, pd.DataFrame]:
        """Fetch historical OHLCV data for symbols."""
        raise NotImplementedError
    
    async def get_trades(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """Fetch trade-level data for a symbol."""
        raise NotImplementedError
    
    async def get_quotes(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """Fetch quote-level data for a symbol."""
        raise NotImplementedError
    
    async def validate_availability(
        self,
        symbols: list[str],
        start_date: datetime,
        end_date: datetime,
        resolution: Resolution,
    ) -> DataValidationResult:
        """Validate data availability for the specified range."""
        raise NotImplementedError
```

### 6.4 Result Reporter Contract

```python
class ResultReporterPort(Protocol):
    @property
    def supported_formats(self) -> list[ExportFormat]:
        """List of supported export formats."""
    
    async def generate_report(
        self,
        result: BacktestResult,
        include_charts: bool = True,
    ) -> BacktestReport:
        """Generate a complete backtest report."""
        raise NotImplementedError
    
    async def export(
        self,
        result: BacktestResult,
        format: ExportFormat,
        destination: Path,
    ) -> None:
        """Export result to specified format."""
        raise NotImplementedError
    
    def generate_summary(
        self,
        result: BacktestResult,
    ) -> PerformanceSummary:
        """Generate a performance summary."""
        raise NotImplementedError
```

---

## 7. Error Handling

### 7.1 Error Categories and Responses

| Category | Error | Response | Recovery |
|----------|-------|----------|----------|
| **Data** | MissingData | Fallback to backup source | Auto-retry 3x |
| **Data** | DataGap | Interpolate or skip | Log warning |
| **Data** | StaleData | Reject with error | Alert ops |
| **Engine** | EngineCrash | Switch engine | Failover to backup |
| **Engine** | Timeout | Cancel with partial results | Persist state |
| **Engine** | OOM | Reduce scope | Restart process |
| **State** | InvalidTransition | Halt execution | Manual review |
| **State** | IdempotencyViolation | Reject duplicate | Log and alert |
| **System** | ResourceExhausted | Scale down | Queue requests |
| **System** | NetworkError | Retry with backoff | Circuit breaker |

### 7.2 Fail-Closed Error Handling

```python
class BacktestError(Exception):
    """Base exception for backtest errors."""
    
    error_category: ErrorCategory
    severity: ErrorSeverity
    is_retryable: bool

class FailClosedHandler:
    async def handle_error(self, error: BacktestError) -> None:
        if error.severity == ErrorSeverity.CRITICAL:
            await self._trigger_degraded_mode(error)
            await self._escalate_killswitch(error)
        elif error.severity == ErrorSeverity.HIGH:
            await self._retry_with_fallback(error)
        else:
            await self._log_and_continue(error)
    
    async def _trigger_degraded_mode(self, error: BacktestError) -> None:
        """Transition to degraded mode."""
    
    async def _escalate_killswitch(self, error: BacktestError) -> None:
        """Escalate KillSwitch based on error type."""
```

### 7.3 Retry Strategy

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            RETRY STRATEGY                                    │
└─────────────────────────────────────────────────────────────────────────────┘

   Error occurs
       │
       ▼
   ┌─────────────────┐
   │ Retry Count = 0 │
   └────────┬────────┘
            │
            ▼
   ┌─────────────────┐      YES     ┌─────────────────┐
   │ Retry < Max?    │─────────────▶│ Wait (exp backoff)│
   └────────┬────────┘              └────────┬────────┘
            │ NO                              │
            ▼                                 │
   ┌─────────────────┐◀────────────────────────┘
   │ Execute Retry  │          (exponential backoff: 1s, 2s, 4s, 8s...)
   └────────┬────────┘
            │
            ├──────────┬──────────┐
            ▼          ▼          ▼
       Success     Fail       Fail
            │          │          │
            ▼          ▼          ▼
      Return OK   Retry++   Trigger Fail-Closed
                                    │
                                    ▼
                              Persist State
                              Alert Ops
                              Escalate
```

---

## 8. Resource Requirements

### 8.1 Hardware Requirements

| Component | Minimum | Recommended | Notes |
|-----------|---------|-------------|-------|
| **CPU** | 4 cores | 8 cores | Lean CLI is CPU-intensive |
| **RAM** | 8 GB | 16 GB | Per engine instance |
| **Disk** | 50 GB SSD | 200 GB NVMe | For data cache |
| **GPU** | Optional | Optional | Not required |

### 8.2 Software Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.12+ | Primary language |
| QuantConnect Lean CLI | Latest | Via Docker |
| VectorBT | 0.24+ | Via pip |
| Redis | 7.0+ | For caching |
| PostgreSQL | 15+ | Shared with trading system |

### 8.3 Scaling Guidelines

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SCALING ARCHITECTURE                               │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │                          LOAD BALANCER                                  │
   │                    (Backtest API Requests)                              │
   └────────────────────────────┬───────────────────────────────────────────┘
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
          ▼                      ▼                      ▼
   ┌─────────────┐        ┌─────────────┐        ┌─────────────┐
   │  Instance 1 │        │  Instance 2 │        │  Instance N │
   │  (API +     │        │  (API +     │        │  (API +     │
   │   Engine)   │        │   Engine)   │        │   Engine)   │
   └─────────────┘        └─────────────┘        └─────────────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Shared Infrastructure │
                    │  • Redis (cache/locks)  │
                    │  • PostgreSQL (results)  │
                    │  • File Storage (data)   │
                    └─────────────────────────┘

   Scaling Triggers:
   • CPU > 70% for 5 min → Scale up instances
   • Queue depth > 10 → Scale up instances  
   • Memory > 80% → Scale up instance size
```

### 8.4 Resource Allocation for Optimization

| Optimization Type | Concurrent Runs | Memory/Runs | CPU/Runs |
|-------------------|------------------|-------------|----------|
| Grid Search (Lean) | 4 | 2 GB | 2 cores |
| Grid Search (VectorBT) | 8 | 1 GB | 1 core |
| Genetic Algorithm | 6 | 2 GB | 2 cores |
| Walk-Forward | 2 | 4 GB | 4 cores |

---

## Appendix A: File Structure

```
trader/backtesting/
├── __init__.py
├── adapters/
│   ├── __init__.py
│   ├── lean_adapter.py
│   ├── vectorbt_adapter.py
│   └── custom_adapter.py
├── ports/
│   ├── __init__.py
│   ├── backtest_engine_port.py
│   ├── data_provider_port.py
│   └── result_reporter_port.py
├── data/
│   ├── __init__.py
│   ├── feature_store_adapter.py
│   ├── cache_layer.py
│   └── postgres_adapter.py
├── lifecycle/
│   ├── __init__.py
│   ├── strategy_lifecycle_manager.py
│   └── state_machine.py
├── reporting/
│   ├── __init__.py
│   ├── performance_analyzer.py
│   ├── trade_analytics.py
│   └── exporters.py
└── config/
    ├── __init__.py
    └── backtest_config.py
```

---

## Appendix B: Configuration Schema

```yaml
backtesting:
  default_engine: lean  # lean | vectorbt | auto
  
  lean:
    cli_path: /app/lean-cli
    data_dir: /data/lean
    results_dir: /results
    timeout_seconds: 3600
    max_concurrent: 4
  
  vectorbt:
    cache_dir: /data/vectorbt
    max_concurrent: 8
    timeout_seconds: 1800
  
  data:
    provider: feature_store  # feature_store | direct | auto
    cache_ttl_seconds: 86400
    fallback_providers:
      - name: binance
        priority: 1
      - name: custom_feed
        priority: 2
  
  optimization:
    default_method: grid_search  # grid_search | genetic | bayesian
    max_iterations: 1000
    convergence_threshold: 0.001
  
  reporting:
    default_formats:
      - json
      - plotly_html
    include_trade_log: true
    include_equity_curve: true
```

---

*Document generated as part of Phase 5 backtesting framework integration.*  
*Reference: ADR-001-backtesting-framework-selection.md*
