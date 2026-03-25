# Task 2.2 OnChain/宏观数据适配器 - 详细需求分析与实现规划

> 架构师视角。工程师执行参考。
> 创建时间：2026-03-25

---

## 一、验收标准回顾

| 标准 | 当前状态 | 说明 |
|------|---------|------|
| 至少2个链上指标稳定写入Feature Store | ⚠️ 部分满足 | stablecoin_supply可用；liquidation/exchange_flow是STUB |
| 数据延迟可观测（local_receive_ts vs source_ts） | ❌ 不满足 | meta中缺少local_ts_ms |

---

## 二、当前代码分析

### 2.1 已有功能

[`onchain_market_data_stream.py`](trader/adapters/onchain/onchain_market_data_stream.py:1) 已实现：

| 功能 | 方法 | 状态 | 说明 |
|------|------|------|------|
| 爆仓数据采集 | [`_fetch_binance_liquidation_stream()`](trader/adapters/onchain/onchain_market_data_stream.py:152) | ⚠️ STUB | 返回空列表，Binance无公开API |
| 交易所流量采集 | [`_fetch_exchange_flows()`](trader/adapters/onchain/onchain_market_data_stream.py:210) | ⚠️ STUB | 需要Glassnode API key |
| 稳定币供应采集 | [`_fetch_stablecoin_supply()`](trader/adapters/onchain/onchain_market_data_stream.py:240) | ✅ 可用 | 使用CoinGecko API |
| 写入Feature Store | `_write_*_to_store()` | ✅ 已实现 | 三个方法都有完整实现 |
| 轮询循环 | `_*_poll_loop()` | ✅ 已实现 | 三个循环都有实现 |

### 2.2 数据延迟问题

**问题**：Record中有 `exchange_ts_ms` 和 `local_ts_ms` 字段：

```python
@dataclass
class StablecoinSupplyRecord:
    symbol: str
    total_supply: float
    supply_change_24h: float
    exchange_ts_ms: int   # ✅ 存在
    local_ts_ms: int       # ✅ 存在
```

但写入meta时：

```python
meta = {
    "source": "coingecko",
    "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",  # ❌ 使用本地时间，无法计算延迟
}
```

**缺失**：`local_ts_ms` 没有写入meta，导致无法计算 `latency_ms = local_ts_ms - exchange_ts_ms`

### 2.3 爆仓数据缺口

[`_fetch_binance_liquidation_stream()`](trader/adapters/onchain/onchain_market_data_stream.py:156-169) 注释明确说明：

> [STUB IMPLEMENTATION] 此方法不采集爆仓数据，仅获取 ticker 价格。
> 注意：Binance 没有公开的爆仓历史 API，此方法返回空列表。
> 实际项目中应接入 Coinglass、Binance Liquidation API 等专业数据源。

---

## 三、需求细化

### 3.1 链上指标1：稳定币供应 ✅

- **feature_name**: `stablecoin_supply`
- **symbols**: USDT, USDC
- **数据源**: CoinGecko API
- **字段**: `total_supply`, `supply_change_24h`
- **状态**: 已实现，稳定可用

### 3.2 链上指标2：交易所爆仓数据 ⚠️

**选项A**：实现 Binance 爆仓流适配器
- 使用 Binance WebSocket 或 REST 获取实时爆仓
- 缺点：Binance 爆仓API有频率限制

**选项B**：实现 Coinglass API 适配器
- Coinglass 提供专业的爆仓、清算数据
- 需要 API key（付费服务）
- 更完整的数据

**选项C**：使用 Binance `GET /fapi/v1/allMarketOpenAvgPriceStats` 或类似端点
- 需要调研实际可用的端点

**推荐**：选项A + 降级保护
- 优先使用 Binance 公开数据
- 失败时降级到历史缓存数据

### 3.3 数据延迟可观测性

需要在所有写入meta中添加：

```python
meta = {
    "source": "coingecko",
    "source_ts_ms": record.exchange_ts_ms,      # 数据源时间戳
    "local_receive_ts_ms": record.local_ts_ms,  # 本地接收时间戳
    "latency_ms": record.local_ts_ms - record.exchange_ts_ms,  # 计算延迟
    "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
}
```

---

## 四、需要修改的文件

| 文件 | 修改内容 | 优先级 |
|------|---------|--------|
| `trader/adapters/onchain/onchain_market_data_stream.py` | 1. 添加latency字段到meta<br>2. 实现真实的爆仓数据采集<br>3. 完善exchange_flow降级逻辑 | P0 |
| `trader/tests/test_onchain_market_data_stream.py` | 1. 添加latency字段验证测试<br>2. 添加爆仓数据STUB说明的测试<br>3. 添加集成测试验证2个指标写入 | P1 |

---

## 五、实现步骤

### Step 1: 修复数据延迟可观测性 [P0]

**文件**: `trader/adapters/onchain/onchain_market_data_stream.py`

修改三个 `_write_*_to_store` 方法，在meta中添加延迟字段：

```python
async def _write_supply_to_store(self, record: StablecoinSupplyRecord) -> None:
    # 计算延迟
    latency_ms = record.local_ts_ms - record.exchange_ts_ms
    
    meta = {
        "source": "coingecko",
        "source_ts_ms": record.exchange_ts_ms,
        "local_receive_ts_ms": record.local_ts_ms,
        "latency_ms": latency_ms,
        "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
    # ... 后续写入逻辑不变
```

**同步修改**: `_write_liquidation_to_store`, `_write_flow_to_store`

### Step 2: 实现爆仓数据采集 [P1]

**方案**：使用 Binance 公开可用数据 + Coinglass API 降级

1. 调研 Binance 是否有公开的爆仓/清算统计端点
2. 如果有，实现采集逻辑
3. 如果没有，保持STUB但添加降级说明

```python
async def _fetch_binance_liquidation_stream(self) -> List[LiquidationRecord]:
    """
    获取 Binance 爆仓数据
    
    [IMPROVED] 尝试使用 Binance 公开数据源
    降级：如果无法获取，返回空列表并记录日志
    """
    # 实现...
```

### Step 3: 添加数据延迟验证测试 [P1]

**文件**: `trader/tests/test_onchain_market_data_stream.py`

```python
@pytest.mark.asyncio
async def test_supply_latency_observable(self):
    """验证稳定币供应数据的延迟可观测"""
    record = StablecoinSupplyRecord(
        symbol="USDT",
        total_supply=83000000000.0,
        supply_change_24h=0.05,
        exchange_ts_ms=1700000000000,
        local_ts_ms=1700000000500,  # 500ms延迟
    )
    
    await self.adapter._write_supply_to_store(record)
    
    feature = await self.adapter._feature_store.read_feature(...)
    
    # 验证延迟字段存在
    assert "latency_ms" in feature["meta"]
    assert feature["meta"]["latency_ms"] == 500
    assert "source_ts_ms" in feature["meta"]
    assert "local_receive_ts_ms" in feature["meta"]
```

### Step 4: 添加集成测试验证2个指标 [P2]

验证稳定币供应和爆仓数据（或exchange_flow）都能写入Feature Store

### Step 5: 文档更新 [P3]

- 更新代码注释说明当前STUB部分
- 添加数据源说明文档

---

## 六、风险与备选方案

| 风险 | 影响 | 备选方案 |
|------|------|---------|
| Binance 无公开爆仓API | 无法实现liquidation指标 | 使用 exchange_flow 作为第二指标（如果Glassnode可用），或标记为"部分完成" |
| CoinGecko 限流 | 稳定币供应不稳定 | 增加重试次数 + 延长轮询间隔 |
| API key 配置缺失 | exchange_flow 不可用 | 保持STUB，不影响主流程 |

---

## 七、建议的Work Breakdown

```
Task 2.2 OnChain适配器
├── [P0] 修复数据延迟可观测性
│   ├── 修改 _write_supply_to_store meta字段
│   ├── 修改 _write_liquidation_to_store meta字段
│   ├── 修改 _write_flow_to_store meta字段
│   └── 添加单元测试验证延迟字段
│
├── [P1] 完善爆仓数据采集（如果可行）
│   ├── 调研Binance公开爆仓API
│   ├── 实现真实采集逻辑或保持STUB
│   └── 添加降级保护
│
└── [P2] 集成测试
    └── 验证至少2个指标稳定写入
```

---

## 八、结论

**当前状态**：部分实现
- 稳定币供应 ✅ 可用
- 数据延迟可观测 ❌ 需修复

**最小可行实现**：
1. 修复meta字段（2小时工作量）
2. 验证稳定币供应正常工作（1小时）
3. 添加延迟验证测试（1小时）
4. 调研爆仓数据源（2小时，如果可行）

**第二指标选择**：
- 如果 Binance 有公开爆仓API → 使用 liquidation
- 如果没有 → 使用 exchange_flow（如果Glassnode可用）
- 如果都不可用 → 标记任务为"部分完成"，说明原因
