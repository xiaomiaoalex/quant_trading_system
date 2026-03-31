# 策略管理用户手册

> 本文档描述策略管理系统的使用方法，包括策略开发、部署、监控、热更新等完整生命周期。

## 目录

1. [概述](#1-概述)
2. [策略生命周期](#2-策略生命周期)
3. [策略开发指南](#3-策略开发指南)
4. [策略部署](#4-策略部署)
5. [策略监控](#5-策略监控)
6. [策略热更新](#6-策略热更新)
7. [AI策略共创](#7-ai策略共创)
8. [API参考](#8-api参考)

---

## 1. 概述

### 1.1 系统架构

策略管理系统采用分层架构：

```
┌─────────────────────────────────────────────────────────────┐
│                    Strategy Management Plane                │
├─────────────────────────────────────────────────────────────┤
│  StrategyChatInterface  │  AIStrategyGenerator  │  HITL     │
├─────────────────────────┼───────────────────────┼───────────┤
│  StrategyLifecycleManager                                  │
├─────────────────────────┬───────────────────────┬───────────┤
│  StrategyRunner         │  StrategyEvaluator    │  HotSwap  │
├─────────────────────────┴───────────────────────┴───────────┤
│                    Core Plane (OMS, Risk, Events)           │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 核心组件

| 组件 | 职责 |
|------|------|
| StrategyRunner | 策略执行器，管理策略运行时 |
| StrategyEvaluator | 策略评估器，回测与实时指标 |
| StrategyHotSwapper | 热插拔管理器，策略版本切换 |
| AIStrategyGenerator | AI策略生成器，LLM辅助开发 |
| StrategyChatInterface | 聊天界面，自然语言交互 |
| StrategyLifecycleManager | 生命周期管理，完整闭环 |

---

## 2. 策略生命周期

### 2.1 状态机

```
DRAFT → VALIDATED → BACKTESTED → APPROVED → RUNNING → STOPPED
   │         │           │           │          │
   └─────────┴───────────┴───────────┴──────────┴──→ FAILED
                                                      │
                                                      └──→ ARCHIVED
```

### 2.2 状态说明

| 状态 | 说明 | 允许操作 |
|------|------|----------|
| DRAFT | 策略代码已创建 | validate, delete |
| VALIDATED | 代码验证通过 | backtest, delete |
| BACKTESTED | 回测完成 | approve, delete |
| APPROVED | 审批通过 | start, delete |
| RUNNING | 策略运行中 | stop, pause, hotswap |
| STOPPED | 策略已停止 | start, archive |
| FAILED | 策略异常 | restart, archive |
| ARCHIVED | 策略已归档 | 无 |

---

## 3. 策略开发指南

### 3.1 StrategyPlugin 协议

所有策略必须实现 `StrategyPlugin` 协议：

```python
from typing import Protocol, Dict, Any, Optional, Literal
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime

@dataclass(slots=True)
class MarketData:
    """市场数据"""
    symbol: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    volume: Decimal

@dataclass(slots=True)
class Signal:
    """交易信号"""
    signal_id: str
    symbol: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    quantity: Decimal
    order_type: str  # MARKET, LIMIT
    price: Optional[Decimal]
    reason: str
    confidence: float  # 0.0-1.0

class StrategyPlugin(Protocol):
    """策略插件协议"""
    
    @property
    def plugin_id(self) -> str:
        """策略唯一标识"""
        ...
    
    @property
    def version(self) -> str:
        """策略版本"""
        ...
    
    @property
    def risk_level(self) -> Literal["LOW", "MEDIUM", "HIGH"]:
        """风险等级"""
        ...
    
    async def initialize(self, config: Dict[str, Any]) -> None:
        """初始化策略"""
        ...
    
    async def on_tick(self, market_data: MarketData) -> Optional[Signal]:
        """处理市场数据Tick"""
        ...
    
    async def on_fill(self, fill_data: Dict[str, Any]) -> None:
        """处理成交回报"""
        ...
    
    async def on_cancel(self, cancel_data: Dict[str, Any]) -> None:
        """处理撤单回报"""
        ...
    
    async def shutdown(self) -> None:
        """关闭策略"""
        ...
```

### 3.2 策略示例

```python
# strategies/ema_cross.py

from trader.core.application.strategy_protocol import (
    StrategyPlugin, MarketData, Signal
)
from typing import Dict, Any, Optional, Literal
from decimal import Decimal
import uuid

class EMACrossStrategy:
    """EMA交叉策略"""
    
    def __init__(self):
        self._plugin_id = "ema_cross_v1"
        self._version = "1.0.0"
        self._risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
        
        self._ema_fast_period = 10
        self._ema_slow_period = 20
        self._ema_fast: list[Decimal] = []
        self._ema_slow: list[Decimal] = []
        self._prices: list[Decimal] = []
    
    @property
    def plugin_id(self) -> str:
        return self._plugin_id
    
    @property
    def version(self) -> str:
        return self._version
    
    @property
    def risk_level(self) -> Literal["LOW", "MEDIUM", "HIGH"]:
        return self._risk_level
    
    async def initialize(self, config: Dict[str, Any]) -> None:
        self._ema_fast_period = config.get("ema_fast_period", 10)
        self._ema_slow_period = config.get("ema_slow_period", 20)
    
    async def on_tick(self, market_data: MarketData) -> Optional[Signal]:
        # 更新价格序列
        self._prices.append(market_data.bid)
        
        # 计算EMA
        if len(self._prices) >= self._ema_slow_period:
            self._calculate_ema()
            
            # 检测交叉
            if len(self._ema_fast) >= 2 and len(self._ema_slow) >= 2:
                if self._ema_fast[-1] > self._ema_slow[-1]:
                    if self._ema_fast[-2] <= self._ema_slow[-2]:
                        # 金叉 - 做多信号
                        return Signal(
                            signal_id=str(uuid.uuid4()),
                            symbol=market_data.symbol,
                            direction="LONG",
                            quantity=Decimal("0.1"),
                            order_type="MARKET",
                            price=None,
                            reason="EMA金叉",
                            confidence=0.7
                        )
                elif self._ema_fast[-1] < self._ema_slow[-1]:
                    if self._ema_fast[-2] >= self._ema_slow[-2]:
                        # 死叉 - 平仓信号
                        return Signal(
                            signal_id=str(uuid.uuid4()),
                            symbol=market_data.symbol,
                            direction="FLAT",
                            quantity=Decimal("0"),
                            order_type="MARKET",
                            price=None,
                            reason="EMA死叉",
                            confidence=0.7
                        )
        
        return None
    
    async def on_fill(self, fill_data: Dict[str, Any]) -> None:
        pass
    
    async def on_cancel(self, cancel_data: Dict[str, Any]) -> None:
        pass
    
    async def shutdown(self) -> None:
        pass
    
    def _calculate_ema(self) -> None:
        # 简化的EMA计算
        alpha_fast = 2 / (self._ema_fast_period + 1)
        alpha_slow = 2 / (self._ema_slow_period + 1)
        
        if not self._ema_fast:
            self._ema_fast = [self._prices[-1]]
        else:
            self._ema_fast.append(
                alpha_fast * self._prices[-1] + (1 - alpha_fast) * self._ema_fast[-1]
            )
        
        if not self._ema_slow:
            self._ema_slow = [self._prices[-1]]
        else:
            self._ema_slow.append(
                alpha_slow * self._prices[-1] + (1 - alpha_slow) * self._ema_slow[-1]
            )


def get_plugin() -> StrategyPlugin:
    """策略入口函数"""
    return EMACrossStrategy()
```

### 3.3 资源限制

策略可配置资源限制：

```python
@dataclass(slots=True)
class StrategyResourceLimits:
    max_memory_mb: int = 512           # 最大内存
    max_concurrent_orders: int = 10    # 最大并发订单
    max_order_rate_per_minute: int = 60  # 每分钟最大订单数
    timeout_seconds: int = 30          # 执行超时
```

---

## 4. 策略部署

### 4.1 注册策略

```bash
POST /api/v1/strategies/registry
{
    "strategy_id": "ema_cross_v1",
    "name": "EMA Cross Strategy",
    "description": "基于EMA交叉的趋势跟踪策略",
    "entrypoint": "strategies.ema_cross:get_plugin",
    "language": "python"
}
```

### 4.2 创建版本

```bash
POST /api/v1/strategies/ema_cross_v1/versions
{
    "version": 1,
    "code_ref": "git:abc1234",
    "param_schema": {
        "ema_fast_period": {"type": "integer", "default": 10},
        "ema_slow_period": {"type": "integer", "default": 20}
    }
}
```

### 4.3 加载策略

```bash
POST /api/v1/strategies/ema_cross_v1/load
{
    "version": 1,
    "module_path": "strategies.ema_cross:get_plugin",
    "config": {
        "ema_fast_period": 10,
        "ema_slow_period": 20
    },
    "resource_limits": {
        "max_concurrent_orders": 5,
        "max_order_rate_per_minute": 30
    }
}
```

### 4.4 启动策略

```bash
POST /api/v1/strategies/ema_cross_v1/start
```

### 4.5 停止策略

```bash
POST /api/v1/strategies/ema_cross_v1/stop
```

---

## 5. 策略监控

### 5.1 获取状态

```bash
GET /api/v1/strategies/ema_cross_v1/status
```

响应：
```json
{
    "strategy_id": "ema_cross_v1",
    "status": "RUNNING",
    "version": "1.0.0",
    "loaded_at": "2026-03-30T10:00:00Z",
    "tick_count": 1000,
    "signal_count": 5,
    "error_count": 0,
    "last_tick_at": "2026-03-30T12:00:00Z",
    "blocked_reason": null
}
```

### 5.2 获取指标

```bash
GET /api/v1/strategies/ema_cross_v1/metrics
```

响应：
```json
{
    "total_pnl": "1250.50",
    "sharpe_ratio": 1.85,
    "max_drawdown": 0.08,
    "win_rate": 0.65,
    "trade_count": 20,
    "profit_factor": 2.1
}
```

### 5.3 触发回测

```bash
POST /api/v1/strategies/ema_cross_v1/backtest
{
    "start_time": "2026-01-01T00:00:00Z",
    "end_time": "2026-03-30T00:00:00Z",
    "symbol": "BTCUSDT",
    "initial_capital": 10000
}
```

---

## 6. 策略热更新

### 6.1 热插拔模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| IMMEDIATE | 立即切换 | 紧急修复 |
| GRADUAL | 灰度切换 | 大版本升级 |
| WAIT_ORDERS | 等待挂单成交 | 有未结订单时 |

### 6.2 执行热切换

```bash
POST /api/v1/deployments/ema_cross_v1/hotswap
{
    "new_version": 2,
    "mode": "WAIT_ORDERS",
    "timeout_seconds": 60
}
```

### 6.3 热切换状态机

```
IDLE → LOADING → VALIDATING → PREPARING → SWITCHING → ACTIVE
  │                                            │
  └──────── ROLLING_BACK ←─────────────────────┘
```

### 6.4 手动回滚

```bash
POST /api/v1/deployments/ema_cross_v1/rollback
{
    "target_version": 1
}
```

---

## 7. AI策略共创

### 7.1 创建聊天会话

```bash
POST /api/chat/sessions
{
    "trader_id": "trader_001"
}
```

### 7.2 发送策略需求

```bash
POST /api/chat/sessions/{session_id}/messages
{
    "message": "我想开发一个基于布林带和RSI的组合策略，当价格触及下轨且RSI低于30时做多"
}
```

### 7.3 AI生成策略流程

```
用户描述需求 → AI生成代码 → 代码安全验证 → 提交HITL审批
                                              ↓
                                          Trader审批
                                              ↓
                                          注册部署
```

### 7.4 审批并注册

```bash
POST /api/chat/sessions/{session_id}/approve
{
    "approver": "trader_001",
    "reason": "策略逻辑清晰，风险可控"
}
```

### 7.5 拒绝策略

```bash
POST /api/chat/sessions/{session_id}/reject
{
    "reason": "风险等级过高，需要调整仓位控制"
}
```

---

## 8. API参考

### 8.1 策略管理API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/strategies/registry` | GET | 列出所有策略 |
| `/api/v1/strategies/registry` | POST | 注册策略 |
| `/api/v1/strategies/{id}` | GET | 获取策略详情 |
| `/api/v1/strategies/{id}/versions` | GET | 列出版本 |
| `/api/v1/strategies/{id}/versions` | POST | 创建版本 |
| `/api/v1/strategies/{id}/load` | POST | 加载策略 |
| `/api/v1/strategies/{id}/unload` | POST | 卸载策略 |
| `/api/v1/strategies/{id}/start` | POST | 启动策略 |
| `/api/v1/strategies/{id}/stop` | POST | 停止策略 |
| `/api/v1/strategies/{id}/pause` | POST | 暂停策略 |
| `/api/v1/strategies/{id}/resume` | POST | 恢复策略 |
| `/api/v1/strategies/{id}/status` | GET | 获取状态 |
| `/api/v1/strategies/{id}/metrics` | GET | 获取指标 |
| `/api/v1/strategies/{id}/backtest` | POST | 触发回测 |

### 8.2 热插拔API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/deployments/{id}/hotswap` | POST | 热切换版本 |
| `/api/v1/deployments/{id}/rollback` | POST | 手动回滚 |

### 8.3 聊天API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat/sessions` | GET | 列出会话 |
| `/api/chat/sessions` | POST | 创建会话 |
| `/api/chat/sessions/{id}` | GET | 获取会话 |
| `/api/chat/sessions/{id}` | DELETE | 删除会话 |
| `/api/chat/sessions/{id}/messages` | POST | 发送消息 |
| `/api/chat/sessions/{id}/history` | GET | 获取历史 |
| `/api/chat/sessions/{id}/approve` | POST | 审批策略 |
| `/api/chat/sessions/{id}/reject` | POST | 拒绝策略 |

---

## 附录

### A. 错误码

| 错误码 | 说明 |
|--------|------|
| 1001 | 策略不存在 |
| 1002 | 策略版本不存在 |
| 1003 | 策略状态不允许该操作 |
| 1004 | 策略代码验证失败 |
| 1005 | 策略加载失败 |
| 1006 | 策略执行超时 |
| 1007 | 资源限制超出 |
| 1008 | 热切换失败 |
| 1009 | 回滚失败 |
| 1010 | AI生成代码包含危险操作 |

### B. 安全约束

AI生成的策略代码必须通过以下安全检查：

1. **禁止导入**：os, sys, subprocess, requests, aiohttp, urllib, socket
2. **禁止操作**：eval, exec, compile, open, file
3. **禁止网络调用**：HTTP/WebSocket/Socket
4. **执行超时**：默认30秒
5. **内存限制**：默认512MB

### C. 最佳实践

1. **策略隔离**：每个策略运行在独立Task中
2. **异常处理**：策略异常不影响其他策略
3. **资源限制**：配置合理的资源限制
4. **回测验证**：部署前必须通过回测
5. **审批流程**：AI生成的策略必须经过HITL审批
6. **版本管理**：保留历史版本，支持回滚
7. **监控告警**：配置策略监控规则
