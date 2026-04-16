# Hermes Research Orchestration Templates

> 本文档定义了 Hermes 研究编排的标准操作流程（SOP）。
> Hermes 只做研发编排和任务调度，不进入 Core/Policy 执行链路。

---

## 1. 标准工作流 (SOP)

### 1.1 完整研究流程

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Hermes Research Orchestration                          │
│                                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │  Data    │───▶│ Training │───▶│ Evaluate │───▶│  Report  │          │
│  │ Prepare  │    │          │    │          │    │  Generate│          │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘          │
│       │               │               │               │                 │
│       ▼               ▼               ▼               ▼                 │
│  ┌─────────────────────────────────────────────────────────────┐       │
│  │                     Audit Trail                             │       │
│  │  who → what → when → config → artifacts → trace_id         │       │
│  └─────────────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 工作流模板

#### Template A: 完整训练流程

```yaml
name: qlib_full_training_pipeline
description: 完整 Qlib 训练流程 - 数据准备到报告生成

steps:
  - name: data_preparation
    script: scripts/qlib_data_converter.py
    args:
      symbol: BTCUSDT
      start_time_ms: "{{ START_TIME_MS }}"
      end_time_ms: "{{ END_TIME_MS }}"
      feature_version: v1
      resample_rule: 1D
    output:
      - dataset_path: data/qlib/{{ symbol }}_{{ feature_version }}.csv
      - quality_report: data/qlib/{{ symbol }}_{{ feature_version }}_quality.json
    on_failure: ABORT

  - name: model_training
    script: scripts/qlib_train_workflow.py
    args:
      config: "{{ TRAINING_CONFIG }}"
      dataset_path: "{{ data_preparation.dataset_path }}"
    output:
      - model_path: models/{{ model_id }}/model.pkl
      - config_path: models/{{ model_id }}/config.json
      - metrics_path: models/{{ model_id }}/metrics.json
    depends_on:
      - data_preparation
    on_failure: ROLLBACK

  - name: factor_analysis
    script: scripts/qlib_factor_miner.py
    args:
      importance_path: "{{ model_training.metrics_path }}"
      model_id: "{{ model_training.model_id }}"
      feature_version: v1
    output:
      - factor_report: models/{{ model_id }}/factor_report.json
    depends_on:
      - model_training

  - name: report_generation
    script: scripts/generate_training_report.py
    args:
      model_id: "{{ model_training.model_id }}"
      dataset_path: "{{ data_preparation.dataset_path }}"
      quality_report: "{{ data_preparation.quality_report }}"
      factor_report: "{{ factor_analysis.factor_report }}"
    output:
      - report_path: reports/{{ model_id }}_report.md
    depends_on:
      - data_preparation
      - model_training
      - factor_analysis

  - name: audit_logging
    script: scripts/log_audit.py
    args:
      trace_id: "{{ EXECUTION_TRACE_ID }}"
      workflow: qlib_full_training_pipeline
      steps: "{{ ALL_STEPS_OUTPUT }}"
      triggered_by: hermmes
    depends_on:
      - report_generation
```

#### Template B: 快速回测流程

```yaml
name: qlib_quick_backtest
description: 快速回测验证流程

steps:
  - name: load_recent_data
    script: scripts/qlib_data_converter.py
    args:
      symbol: "{{ SYMBOL }}"
      days_back: 30
      feature_version: "{{ FEATURE_VERSION }}"
    output:
      - dataset_path: data/qlib/{{ symbol }}_recent.csv

  - name: run_backtest
    script: scripts/backtest_model.py
    args:
      model_path: "{{ MODEL_PATH }}"
      dataset_path: "{{ load_recent_data.dataset_path }}"
      start_pct: 0.7  # 使用70%数据训练，30%测试
    output:
      - backtest_result: results/{{ model_id }}_backtest.json

  - name: validate_results
    script: scripts/validate_backtest.py
    args:
      result_path: "{{ run_backtest.backtest_result }}"
      min_sharpe: 1.0
      max_drawdown: 0.2
    depends_on:
      - run_backtest
```

#### Template C: 模型比较流程

```yaml
name: model_comparison
description: 多模型比较流程

steps:
  - name: prepare_baseline_data
    script: scripts/qlib_data_converter.py
    args:
      symbol: BTCUSDT
      start_time_ms: "{{ START_TIME_MS }}"
      end_time_ms: "{{ END_TIME_MS }}"
    output:
      - dataset_path: data/qlib/baseline.csv

  - name: train_model_a
    script: scripts/qlib_train_workflow.py
    args:
      model_type: lightgbm
      dataset_path: "{{ prepare_baseline_data.dataset_path }}"
    output:
      - model_a_path: models/model_a/model.pkl

  - name: train_model_b
    script: scripts/qlib_train_workflow.py
    args:
      model_type: xgboost
      dataset_path: "{{ prepare_baseline_data.dataset_path }}"
    output:
      - model_b_path: models/model_b/model.pkl

  - name: compare_models
    script: scripts/compare_models.py
    args:
      model_a_path: "{{ train_model_a.model_a_path }}"
      model_b_path: "{{ train_model_b.model_b_path }}"
      test_data: "{{ prepare_baseline_data.dataset_path }}"
    output:
      - comparison_report: reports/model_comparison.json
    depends_on:
      - train_model_a
      - train_model_b
```

---

## 2. 任务触发方式

### 2.1 手动触发

```bash
# 触发完整训练流程
hermes run qlib_full_training_pipeline \
  --var START_TIME_MS=1704067200000 \
  --var END_TIME_MS=1711996800000 \
  --var TRAINING_CONFIG=config/train_lgb.yaml

# 触发快速回测
hermes run qlib_quick_backtest \
  --var SYMBOL=BTCUSDT \
  --var MODEL_PATH=models/m240416.abcd/model.pkl
```

### 2.2 定时触发

```yaml
# .hermes/schedules.yml
schedules:
  daily_retrain:
    cron: "0 2 * * *"  # 每天凌晨2点
    workflow: qlib_full_training_pipeline
    vars:
      START_TIME_MS: "{{ 7_days_ago }}"
      END_TIME_MS: "{{ yesterday }}"
  
  weekly_factor_analysis:
    cron: "0 3 * * 1"  # 每周一凌晨3点
    workflow: factor_analysis_only
```

### 2.3 事件触发

```yaml
# .hermes/triggers.yml
triggers:
  on_data_update:
    watch:
      - path: data/qlib/
        events: [create, modify]
    workflow: data_validation_pipeline
  
  on_model_approved:
    watch:
      - path: models/registry.json
        events: [modify]
    condition: "trigger.new_status == 'approved'"
    workflow: shadow_mode_deployment
```

---

## 3. 审计记录规范

### 3.1 审计日志格式

```json
{
  "trace_id": "tr_20240416_abcd1234",
  "workflow": "qlib_full_training_pipeline",
  "triggered_by": "hermes/scheduler",
  "triggered_at": "2026-04-16T02:00:00Z",
  "steps": [
    {
      "step": "data_preparation",
      "status": "completed",
      "started_at": "2026-04-16T02:00:01Z",
      "completed_at": "2026-04-16T02:00:15Z",
      "config": {
        "symbol": "BTCUSDT",
        "feature_version": "v1"
      },
      "artifacts": {
        "dataset_path": "data/qlib/BTCUSDT_v1_20240416.csv"
      }
    },
    {
      "step": "model_training",
      "status": "completed",
      "started_at": "2026-04-16T02:00:16Z",
      "completed_at": "2026-04-16T02:05:30Z",
      "config": {
        "model_type": "lightgbm",
        "n_estimators": 100
      },
      "artifacts": {
        "model_path": "models/m240416.abcd/model.pkl",
        "metrics": {"val_r2": 0.72, "test_r2": 0.68}
      }
    }
  ],
  "final_artifacts": [
    "models/m240416.abcd/model.pkl",
    "reports/m240416.abcd_report.md"
  ]
}
```

### 3.2 产物存储

所有工作流产物统一存储在以下目录：

```
artifacts/
├── models/          # 模型文件
│   └── {model_id}/
│       ├── model.pkl
│       ├── config.json
│       └── feature_importance.json
├── reports/         # 报告文件
│   └── {model_id}/
│       ├── training_report.md
│       └── factor_report.json
├── datasets/        # 数据集文件
│   └── {symbol}_{version}/
│       └── data.csv
└── audit/           # 审计日志
    └── {trace_id}.json
```

---

## 4. 错误处理与重试

### 4.1 重试策略

```yaml
retry_policy:
  max_attempts: 3
  backoff:
    initial: 60  # 初始重试间隔 (秒)
    multiplier: 2  # 重试间隔倍数
    max_interval: 3600  # 最大重试间隔 (秒)

steps:
  - name: model_training
    retry_on_failure: true
    retry_config:
      max_attempts: 5
      retry_on:
        - "DatabaseError"
        - "NetworkTimeout"
        - "ResourceExhausted"
```

### 4.2 失败处理

```yaml
failure_handling:
  # 策略: ABORT | ROLLBACK | CONTINUE
  
  on_failure: ROLLBACK
  
  rollback_steps:
    - name: cleanup_models
      script: scripts/cleanup_failed_model.py
      args:
        model_id: "{{ failed_model_id }}"
```

---

## 5. 与现有系统的边界

### 5.1 Hermes 职责范围

| 职责 | Hermes | 其他组件 |
|------|--------|----------|
| 数据准备 | ✅ | |
| 模型训练 | ✅ | |
| 因子分析 | ✅ | |
| 报告生成 | ✅ | |
| 模型注册 | ✅ | |
| **下单执行** | ❌ | StrategyRunner |
| **实时风控** | ❌ | RiskEngine |
| **订单管理** | ❌ | OMS |

### 5.2 接口约束

Hermes **不得**直接调用以下接口：

- `POST /v1/orders` - 禁止直接下单
- `POST /v1/strategies/{id}/start` - 禁止启动策略
- `POST /v1/killswitch/upgrade` - 禁止升级 KillSwitch

Hermes **只能**通过以下接口间接影响：

- 写入模型到 `models/` 目录
- 更新 `models/registry.json`
- 生成报告到 `reports/` 目录

---

## 6. 配置示例

### 6.1 本地开发配置

```yaml
# .hermes/local.yml
environment:
  data_root: data/qlib
  models_root: models
  reports_root: reports
  registry_path: models/registry.json

execution:
  mode: local
  parallel: false
  timeout_seconds: 3600

notifications:
  on_completion: true
  on_failure: true
```

### 6.2 生产配置

```yaml
# .hermes/production.yml
environment:
  data_root: s3://prod-artifacts/data
  models_root: s3://prod-artifacts/models
  reports_root: s3://prod-artifacts/reports
  registry_path: s3://prod-artifacts/registry.json

execution:
  mode: distributed
  parallel: true
  timeout_seconds: 7200

monitoring:
  metrics_enabled: true
  metrics_endpoint: http://monitoring:8080/metrics
  alert_on_failure: true

approvals:
  auto_approve_threshold: 0.8  # 高置信度自动批准
  require_manual_approval:
    - model_type: lightgbm
      min_sharpe: 1.5
```

---

## 7. 执行记录

所有 Hermes 执行都必须记录到审计存储：

```python
async def log_audit(trace_id: str, workflow: str, result: Dict[str, Any]):
    """记录工作流执行审计"""
    audit_entry = {
        "trace_id": trace_id,
        "workflow": workflow,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "artifacts": list(result.get("artifacts", {}).values()),
    }
    # 写入 PostgreSQL 或文件存储
    await audit_storage.write(audit_entry)
```

---

## 8. CLI 命令参考

```bash
# 列出可用工作流
hermes list workflows

# 查看工作流详情
hermes describe workflow <workflow_name>

# 执行工作流
hermes run <workflow_name> [OPTIONS]

# 查看执行状态
hermes status <trace_id>

# 查看执行历史
hermes history --workflow <workflow_name> --limit 10

# 取消执行
hermes cancel <trace_id>

# 重试失败执行
hermes retry <trace_id>
```

---

## 9. 安全约束

1. **Hermes 只读**：不修改交易执行相关配置
2. **审计可追溯**：所有操作必须记录 trace_id
3. **隔离执行**：研究环境与交易环境分离
4. **最小权限**：Hermes service account 仅有研究域权限