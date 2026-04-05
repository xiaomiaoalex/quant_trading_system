# Phase 8 - 多 Agent 策略组合开发委员会

> **唯一权威文档** - 本文档是 Phase 8 多 Agent 策略组合开发委员会的唯一定义来源
> **版本**: 1.0.0
> **创建日期**: 2026-04-04
> **状态**: 正式发布

---

## 1. 项目背景与目标

### 1.1 项目定位

多 Agent 策略组合开发委员会（Portfolio Committee）是现有 AI 共创与策略生命周期体系的**研究增强层**，不是另起炉灶的独立系统。

### 1.2 与现有系统的兼容边界

| 维度 | 约束 |
|------|------|
| 项目主线 | crypto-first、单账户、小资金 |
| 研究主线 | 趋势/量价/资金结构/链上/事件驱动 |
| AI 限制 | 只能留在 Insight/Research 层，不能穿透到执行 |
| 回测主引擎 | Lean primary，VectorBT secondary |
| 审批链路 | 必须走现有 AI-clean / HITL / LifecycleManager |

### 1.3 核心目标

把多视角研究，变成可审计、可回测、可审批、可淘汰的组合 proposal 流水线。

---

## 2. 委员会架构

### 2.1 Agent 角色矩阵

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Portfolio Committee                           │
├─────────────────────────────────────────────────────────────────────┤
│  Specialist Agents (5个)                                            │
│  ├── TrendAgent         → 趋势信号研究                               │
│  ├── PriceVolumeAgent  → 量价关系研究                               │
│  ├── FundingOIAgent    → 资金结构研究                               │
│  ├── OnChainAgent      → 链上数据研究                               │
│  └── EventRegimeAgent  → 事件与状态机研究                           │
├─────────────────────────────────────────────────────────────────────┤
│  Red Team Agents (2个)                                               │
│  ├── OrthogonalityAgent    → 正交性审查（反对重复/换皮策略）        │
│  └── RiskCostRedTeamAgent  → 风险成本否决（数据/成本/流动性/边界）  │
├─────────────────────────────────────────────────────────────────────┤
│  Portfolio Constructor                                              │
│  └── 组合构建器 → 输出可测试的组合候选                               │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 输出工件定义

| 工件 | 说明 | 核心字段 |
|------|------|----------|
| SleeveProposal | 单策略研究提案 | hypothesis, required_features, regime, failure_modes |
| PortfolioProposal | 组合级提案 | sleeves, capital_caps, regime_conditions, conflict_priorities |
| ReviewReport | 评审报告 | reviewer, verdict, concerns, suggestions |
| CommitteeRun | 委员会运行记录 | run_id, inputs, agent_outputs, review_results, final_decision |

---

## 3. 硬边界约束（必须遵守）

### 3.1 Agent 行为边界

| 约束 | 描述 | 违规后果 |
|------|------|----------|
| 只做研究与 proposal | Agent 只输出 SleeveProposal，不输出交易指令 | 提案直接作废 |
| 不能直接下单 | 严禁 Agent 绕过 HITL 直接触发订单 | 触发安全审计 |
| 不能绕过 HITL | 所有 proposal 必须经过人工审批 | KillSwitch 升级 |
| 必须进入现有 lifecycle | proposal → backtest → approval → lifecycle | 孤立提案不受理 |

### 3.2 技术边界

| 边界 | 说明 |
|------|------|
| Lean primary | 主回测引擎使用 QuantConnect Lean |
| VectorBT secondary | 快速原型验证使用 VectorBT |
| PG-first for risk | 风险相关事件优先写入 PostgreSQL |
| Event Sourcing | 采用追加式日志，所有操作可审计 |

### 3.3 质量门槛

| 指标 | 门槛 | 说明 |
|------|------|------|
| proposal 通过率 | > 20% | 经 committee review 后进入 backtest 的比例 |
| orthogonality 得分 | > 0.7 | 新 sleeve 与现有 sleeve 的正交性 |
| 成本后样本外通过率 | > 0 | 扣除成本后仍为正期望 |
| 人工审查耗时 | < 30 min/sleeve | 否则说明自动化不足 |
| 边界违规次数 | = 0 | 任何 AI 直接下单行为必须为零 |

---

## 4. 流程定义

### 4.1 完整链路

```
Research Request
      │
      ▼
┌──────────────────────────────────────────────────────────────────┐
│                     CommitteeRun                                  │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐             │
│  │ Specialist │    │ Specialist  │    │ Specialist  │             │
│  │  Agents    │    │  Agents     │    │  Agents     │             │
│  │ (并行执行)  │    │ (并行执行)   │    │ (并行执行)   │             │
│  └─────┬──────┘    └──────┬─────┘    └──────┬─────┘             │
│        │                  │                  │                   │
│        ▼                  ▼                  ▼                   │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐             │
│  │  Sleeve    │    │  Sleeve    │    │  Sleeve    │             │
│  │ Proposal   │    │ Proposal   │    │ Proposal   │             │
│  └─────┬──────┘    └──────┬─────┘    └──────┬─────┘             │
│        │                  │                  │                   │
│        └──────────────────┼──────────────────┘                   │
│                           ▼                                       │
│              ┌────────────────────────┐                           │
│              │  OrthogonalityAgent    │ → 正交性审查              │
│              │  RiskCostRedTeamAgent │ → 风险成本否决            │
│              └───────────┬────────────┘                           │
│                          ▼                                        │
│              ┌────────────────────────┐                           │
│              │ Portfolio Constructor  │ → 组合构建                │
│              └───────────┬────────────┘                           │
└──────────────────────────┼────────────────────────────────────────┘
                           ▼
                   ReviewReport
                           │
                           ▼
              Human Approve (HITL)
                           │
                           ▼
              BacktestJob / StrategyDraft
                           │
                           ▼
                  LifecycleManager
```

### 4.2 状态机

| 状态 | 说明 |
|------|------|
| PENDING | 等待委员会审查 |
| IN_REVIEW | 正在被 specialist 或 red team 审查 |
| PASSED | 通过审查，进入 backtest |
| REJECTED | 被 red team 否决 |
| APPROVED | 人工审批通过 |
| ARCHIVED | 已废弃或被淘汰 |

---

## 5. 数据模型

### 5.1 SleeveProposal 核心字段

```python
@dataclass(slots=True)
class SleeveProposal:
    proposal_id: str                    # UUID
    hypothesis: str                     # 核心假设
    required_features: List[str]       # 依赖的特征
    regime: str                         # 适用市场状态
    failure_modes: List[str]           # 已知失效条件
    cost_assumptions: CostAssumptions  # 成本假设
    evidence_refs: List[str]           # 证据引用
    feature_version: str                # 特征版本
    prompt_version: str                 # prompt 版本
    trace_id: str                       # 追踪 ID
    specialist_type: str               # Specialist 类型
    created_at: datetime
```

### 5.2 PortfolioProposal 核心字段

```python
@dataclass(slots=True)
class PortfolioProposal:
    proposal_id: str
    sleeves: List[SleeveAssignment]    # 分配的 sleeves
    capital_caps: Dict[str, Decimal]   # 每个 sleeve 的资金上限
    regime_启停条件: Dict[str, RegimeCondition]
    conflict_priorities: List[ConflictResolution]
    risk_explanation: str              # 组合级风险说明
    evaluation_task_id: str            # 自动生成的评估任务 ID
    created_at: datetime
```

---

## 6. 审计要求

### 6.1 必须记录的审计字段

每次 CommitteeRun 必须留下：

| 字段 | 说明 |
|------|------|
| 输入需求 | 原始 research request |
| 上下文包版本 | 使用的 feature/prompt 版本 |
| Agent 输出 | 每个 specialist 的完整输出 |
| Review 结果 | orthogonality 和 red team 的判定 |
| Human Decision | 人工审批结果 |
| Backtest Job | 关联的回测任务 |
| 最终结论 | 淘汰/保留/修改后重提 |

### 6.2 审计存储

- 主存储：PostgreSQL (`committee_runs` 表)
- 事件溯源：所有操作记录到 event_log
- 可回放：基于 trace_id 完整重放

---

## 7. 评估指标

### 7.1 核心 5 指标

| 指标 | 计算方式 | 目标 |
|------|----------|------|
| proposal 通过率 | 通过数 / 总提交数 | > 20% |
| orthogonality 得分 | 新 sleeve 与现有 sleeve 的余弦相似度 | > 0.7 |
| 成本后样本外通过率 | 1.5x 成本压测后仍正期望的比例 | > 50% |
| 人工审查耗时 | 人工审核时间 / proposal 数 | < 30 min |
| 边界违规次数 | Agent 直接下单次数 | = 0 |

### 7.2 保留条件

只有在"多 agent 比单 agent / 人工流程更能产生可通过的组合候选"时，Phase 8 才继续扩展。

---

## 8. 文件结构

```
insight/committee/
├── schemas.py                 # 工件 schema 定义
├── router.py                  # Specialist agents 路由
├── specialists/               # Specialist agents
│   ├── __init__.py
│   ├── base.py               # Base specialist
│   ├── trend_agent.py
│   ├── price_volume_agent.py
│   ├── funding_oi_agent.py
│   ├── onchain_agent.py
│   └── event_regime_agent.py
├── orthogonality.py           # OrthogonalityAgent
├── red_team.py               # RiskCostRedTeamAgent
└── portfolio_constructor.py  # Portfolio constructor

services/
├── portfolio_research_workflow.py    # 研究工作流
├── committee_to_lifecycle_adapter.py # 生命周期适配
└── committee_audit_service.py       # 审计服务

adapters/persistence/
└── portfolio_proposal_store.py      # 提案持久化

api/routes/
└── portfolio_research.py            # API 路由
```

---

## 9. 文档更新要求

任何功能性变更必须同步更新：

1. **PROJECT_STATUS.md** - 记录开发前后状态对比
2. **EXPERIENCE_SUMMARY.md** - 记录踩坑记录和设计模式
3. **PLAN.md** - 保持计划文档新鲜

---

## 10. 版本历史

| 版本 | 日期 | 修改内容 |
|------|------|----------|
| 1.0.0 | 2026-04-04 | 初始版本，定义核心架构和约束 |
