# Qlib/Hermes 集成 - 数据契约文档

> 本文档定义 Qlib 训练与推理的数据语义约束，确保"训练与实盘两套宇宙"的一致性。

---

## 1. 数据契约原则

1. **版本冻结**：同一 `feature_version` 簇下的数据不可更改
2. **确定性**：相同输入必须产生相同输出
3. **可追溯**：训练输入必须能回放到源数据
4. **Fail-Closed**：缺失数据、异常跳点、对齐失败必须触发告警

---

## 2. 字段定义

### 2.1 OHLCV 基础字段

| 字段名 | 类型 | 说明 | 时区 |
|--------|------|------|------|
| open | float | 开盘价 | UTC |
| high | float | 最高价 | UTC |
| low | float | 最低价 | UTC |
| close | float | 收盘价 | UTC |
| volume | float | 成交量 | UTC |

### 2.2 技术指标字段

| Qlib字段名 | 原始字段名 | 版本 | 说明 |
|------------|------------|------|------|
| EMA20 | ema_20 | v1 | 20周期指数移动平均 |
| EMA50 | ema_50 | v1 | 50周期指数移动平均 |
| EMA200 | ema_200 | v1 | 200周期指数移动平均 |
| RSI14 | rsi_14 | v1 | 14周期相对强弱指数 |
| RSI28 | rsi_28 | v1 | 28周期相对强弱指数 |
| BOLL_UPPER | boll_upper | v1 | 布林带上轨 |
| BOLL_MIDDLE | boll_middle | v1 | 布林带中轨 |
| BOLL_LOWER | boll_lower | v1 | 布林带下轨 |
| VOLUME_RATIO | volume_ratio | v1 | 成交量比率 |
| PRICE_MOMENTUM | price_momentum | v1 | 价格动量 |

### 2.3 资金结构字段

| Qlib字段名 | 原始字段名 | 版本 | 说明 |
|------------|------------|------|------|
| FUNDING_RATE | funding_rate | v1 | funding rate 百分比 |
| FUNDING_RATE_ZSCORE | funding_rate_zscore | v1 | funding rate z-score |
| OI | open_interest | v1 | 未平仓合约量 |
| OI_CHANGE_RATE | oi_change_rate | v1 | OI变化率 |
| LS_RATIO | long_short_ratio | v1 | 多空比 |
| LS_RATIO_ZSCORE | long_short_ratio_zscore | v1 | 多空比z-score |

### 2.4 情绪字段

| Qlib字段名 | 原始字段名 | 版本 | 说明 |
|------------|------------|------|------|
| SC_SUPPLY | stablecoin_supply | v1 | 稳定币供应量 |
| LIQ_BID | liquidation_bid_notional | v1 | 买入清算量 |
| LIQ_ASK | liquidation_ask_notional | v1 | 卖出清算量 |
| LIQ_NET | liquidation_net_imbalance | v1 | 净清算失衡度 |

---

## 3. 特征版本映射规范

### 3.1 版本标签格式

```
feature_version: v{major}{minor}
  - major: 不兼容的字段变更
  - minor: 向后兼容的新增字段
```

### 3.2 版本追踪要求

每个模型训练必须记录：

```json
{
  "model_version": "m1.0.0",
  "feature_version": "v1.2",
  "train_window": ["2024-01-01", "2024-06-30"],
  "label_def": "next_1d_return",
  "contract_hash": "a1b2c3d4"
}
```

### 3.3 版本兼容性矩阵

| 训练版本 | 推理版本 | 兼容条件 |
|----------|----------|----------|
| v1.0 | v1.0 | 完全兼容 |
| v1.0 | v1.1 | 向前兼容（可用v1.0模型推理v1.1特征） |
| v1.1 | v1.0 | 不兼容（需要重训练） |

---

## 4. 时间戳与时区规则

1. **存储格式**：毫秒级 Unix 时间戳
2. **时区**：统一使用 UTC
3. **K线周期**：默认 1D，可配置 1H/4H/1W
4. **重采样**：收盘价用 last，成交量用 sum

---

## 5. 缺失值策略

| 场景 | 处理策略 |
|------|----------|
| 单点缺失 | 前向填充 (ffill) |
| 连续缺失 < 5% | 线性插值 |
| 连续缺失 >= 5% | 标记为 NaN，报告告警 |
| 整列缺失 | 报告 ERROR，阻断训练 |

---

## 6. 异常跳点检测

使用 3x IQR 方法：

```
lower_bound = Q1 - 3 * IQR
upper_bound = Q3 + 3 * IQR
outlier = value < lower_bound OR value > upper_bound
```

检测到的异常点：
- 记录到 `DataQualityReport.outlier_count`
- **不自动修正**，由人工确认后决定是否剔除

---

## 7. 数据对齐失败规则

以下情况记录为对齐失败：

1. **长度不一致**：不同特征的时间序列长度不同
2. **时间错位**：特征时间戳不在同一周期边界
3. **符号不一致**：同一 symbol 的不同特征数据不一致

对齐失败触发 `DataQualityError`，阻断训练。

---

## 8. 数据契约哈希

每个数据集生成唯一的 `contract_hash`：

```
contract_hash = SHA256(
  symbol + feature_names + version + resample_rule
)[:16]
```

用于：
- 版本追踪
- 训练/推理一致性验证
- 审计追溯

---

## 9. 数据质量阈值

| 指标 | 阈值 | 超过阈值行为 |
|------|------|-------------|
| 缺失率 | < 5% | FAIL - 阻断训练 |
| 时间间隙数 | = 0 | FAIL - 阻断训练 |
| 异常跳点数 | = 0 | WARN - 记录但继续 |
| 对齐失败 | = 0 | FAIL - 阻断训练 |

---

## 10. Qlib 格式要求

输出格式：`Pandas DataFrame`

```
datetime,EMA20,EMA50,RSI14,BOLL_UPPER,...,FUNDING_RATE,SC_SUPPLY
2024-01-01,45000.0,44500.0,65.5,46000.0,...,0.0001,20000000000
2024-01-02,45100.0,44600.0,68.2,46200.0,...,0.0002,21000000000
```

索引：`datetime` (Pandas Timestamp)  
时间范围：inclusive [start_time, end_time]  
频率：由 `resample_rule` 定义