# ADR-007: Agent Boundary Constraints for Portfolio Committee

> **决策记录**: ADR-007
> **标题**: 多 Agent 策略组合开发委员会边界约束
> **状态**: 正式发布
> **创建日期**: 2026-04-04
> **决策者**: AI Assistant
> **评审日期**: 2026-04-04

---

## 1. 背景

Phase 8 引入多 Agent 策略组合开发委员会，需要明确定义 Agent 的行为边界，防止 Agent 绕过现有的安全机制（AITL/HITL/Lifecycle）直接执行交易。

本 ADR 定义了所有 Agent 必须遵守的硬边界约束。

---

## 2. 决策

### 2.1 绝对禁止行为

以下行为被严格禁止，违反任何一条都将触发安全审计和 KillSwitch 升级：

| 禁止行为 | 描述 | 触发后果 |
|----------|------|----------|
| **DIRECT_ORDER** | Agent 直接生成订单并发送到交易所 | KillSwitch L3，审计日志，人工审查 |
| **BYPASS_HITL** | Agent 绕过 HITL 审批直接提交策略 | KillSwitch L2，拒绝提案 |
| **BYPASS_BACKTEST** | Agent 跳过回测直接申请上线 | KillSwitch L2，拒绝提案 |
| **MANIPULATE_POSITION** | Agent 直接修改持仓 | KillSwitch L3，审计日志 |
| **ACCESS_SECRET** | Agent 访问 API keys 或 secrets | KillSwitch L3，立即终止 |

### 2.2 允许行为

| 允许行为 | 说明 |
|----------|------|
| **SLEEVE_PROPOSAL** | 生成并输出 SleeveProposal |
| **RESEARCH_OUTPUT** | 输出研究报告、假设、证据引用 |
| **REGIME_ANALYSIS** | 市场状态分析 |
| **ORTHOGONALITY_CHECK** | 正交性审查 |
| **RISK_COST_REVIEW** | 风险成本审查 |
| **PORTFOLIO_CONSTRUCTION** | 组合构建（输出结构化数据，非指令） |

### 2.3 强制行为

| 强制行为 | 说明 |
|----------|------|
| **TRACEABILITY** | 所有输出必须包含 trace_id |
| **VERSION_TAG** | 所有输出必须标注 feature_version 和 prompt_version |
| **AUDIT_LOG** | 所有操作必须记录到审计日志 |
| **FAIL_CLOSED** | 遇到无法解析的情况必须拒绝，而非放行 |

---

## 3. Agent 分级权限

### 3.1 Specialist Agents (TrendAgent, PriceVolumeAgent, FundingOIAgent, OnChainAgent, EventRegimeAgent)

**权限范围**：
- 读取历史市场数据
- 读取特征存储
- 生成 SleeveProposal
- 访问公开研报

**禁止操作**：
- ❌ 任何写操作到订单/持仓系统
- ❌ 访问 API keys
- ❌ 直接生成交易信号
- ❌ 绕过 HITL

### 3.2 OrthogonalityAgent

**权限范围**：
- 读取现有 SleeveProposal
- 计算向量相似度
- 输出正交性报告

**禁止操作**：
- ❌ 修改任何 proposal
- ❌ 访问持仓/订单数据
- ❌ 生成交易指令

### 3.3 RiskCostRedTeamAgent

**权限范围**：
- 读取数据质量指标
- 读取成本估算
- 读取流动性数据
- 输出否决报告

**禁止操作**：
- ❌ 修改任何 proposal
- ❌ 访问持仓/订单数据
- ❌ 生成交易指令

### 3.4 Portfolio Constructor

**权限范围**：
- 读取 ReviewReport
- 生成 PortfolioProposal
- 计算 capital caps

**禁止操作**：
- ❌ 直接下单
- ❌ 修改持仓
- ❌ 绕过 HITL

---

## 4. 验证机制

### 4.1 代码层面

```python
# 每个 Agent 输出必须包含验证
@dataclass
class AgentOutput:
    output_type: OutputType  # SLEEVE_PROPOSAL / REVIEW_REPORT / etc
    trace_id: str
    feature_version: str
    prompt_version: str
    validation_result: ValidationResult  # 必须通过验证才能继续

# 验证失败时
class ValidationResult:
    is_valid: bool
    violations: List[Violation]  # 如果 is_valid=False，必须有具体违规描述
```

### 4.2 运行时检查

| 检查点 | 时机 | 失败处理 |
|--------|------|----------|
| OutputType 检查 | Agent 输出时 | 拒绝并记录 |
| trace_id 存在性 | 任何输出 | 拒绝 |
| 版本标签检查 | 提案进入下一阶段 | 拒绝 |
| 边界违规扫描 | 定期扫描 | 立即升级 KillSwitch |

---

## 5. 审计要求

### 5.1 必须记录的审计事件

| 事件类型 | 触发条件 | 记录内容 |
|----------|----------|----------|
| AGENT_OUTPUT | Agent 产生输出 | output_type, trace_id, content_hash |
| BOUNDARY_VIOLATION | 任何禁止行为被检测 | agent_type, violation_type, context |
| PROPOSAL_REJECTED | 提案被拒绝 | reason, reviewer, alternatives |
| HITL_APPROVAL | 人工审批 | approver, decision, comments |

### 5.2 审计存储

```sql
CREATE TABLE agent_audit_log (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    agent_type VARCHAR(50),
    trace_id VARCHAR(100),
    content_hash VARCHAR(64),
    context JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 6. 违规处理流程

```
检测到违规
      │
      ▼
┌─────────────────┐
│ 记录违规事件     │ → agent_audit_log
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ KillSwitch 升级 │
│ (根据违规类型)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 人工审查        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 决定是否继续     │
│ Committee 流程   │
└─────────────────┘
```

---

## 7. 相关文档

- `docs/PHASE7_TASK7_MULTI_AGENT_PORTFOLIO.md` - 委员会完整定义
- `trader/core/application/hitl_governance.py` - HITL 治理
- `trader/services/strategy_lifecycle_manager.py` - 生命周期管理
- `insight/ai_strategy_generator.py` - AI 策略生成器

---

## 8. 版本历史

| 版本 | 日期 | 修改内容 |
|------|------|----------|
| 1.0.0 | 2026-04-04 | 初始版本，定义 Agent 边界约束 |
