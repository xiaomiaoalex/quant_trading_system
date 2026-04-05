# Phase 8 Task 8.7 - 价值证明评估报告

> **评估日期**: 2026-04-04
> **评估范围**: 多 Agent 策略组合开发委员会
> **状态**: 开发完成，待验证

---

## 1. 评估目标

验证多 Agent 组合开发委员会是否比单 Agent / 人工流程更能产生可通过的组合候选。

**保留条件**：只有在这个目标达成时，Phase 8 才继续扩展。

---

## 2. 核心评估指标

### 2.1 五大指标

| 指标 | 定义 | 目标值 | 测量方法 |
|------|------|--------|----------|
| **proposal 通过率** | 通过数 / 总提交数 | > 20% | 统计 CommitteeRun 中 final_status = APPROVED 的比例 |
| **orthogonality 得分** | 新 sleeve 与现有 sleeve 的余弦相似度 | > 0.7 | OrthogonalityAgent 输出 |
| **成本后样本外通过率** | 1.5x 成本压测后仍正期望的比例 | > 50% | BacktestReport after cost stress |
| **人工审查耗时** | 人工审核时间 / proposal 数 | < 30 min | HITLGovernance 记录 |
| **边界违规次数** | Agent 直接下单次数 | = 0 | AuditService 检测 |

### 2.2 计算公式

```
Propoal通过率 = CommitteeRuns with final_status=APPROVED / Total CommitteeRuns
Orthogonality得分 = avg(OrthogonalityAgent.orthogonality_score) across all reviews
成本后样本外通过率 = Proposals passing 1.5x cost stress / Approved proposals
人工审查耗时 = Total HITL review time / Number of proposals reviewed
边界违规次数 = count(AuditEvent where event_type=BOUNDARY_VIOLATION)
```

---

## 3. 评估方法

### 3.1 对比基准

| 基准 | 说明 |
|------|------|
| 单 Agent 流程 | 只有一个 Specialist Agent 输出提案，无 Red Team 审查 |
| 人工流程 | 纯人工研究和建议，无 AI 辅助 |
| 随机流程 | 随机生成提案作为对照 |

### 3.2 评估场景

| 场景 | 研究请求 | 预期结果 |
|------|----------|----------|
| S1 | 趋势策略研究 | TrendAgent 输出提案，orthogonality > 0.7 |
| S2 | 资金费率异常研究 | FundingOIAgent 输出提案，risk_score > 0.5 |
| S3 | 多信号组合研究 | 多 Agent 协作，portfolio 包含多个 sleeves |
| S4 | 重复提案检测 | OrthogonalityAgent 正确识别重复提案并否决 |
| S5 | 边界违规检测 | 检测到直接下单指令并记录违规 |

---

## 4. 评估数据收集

### 4.1 需要收集的数据

```python
evaluation_data = {
    # Committee Runs
    "total_runs": int,
    "completed_runs": int,
    "failed_runs": int,
    
    # Proposals
    "total_proposals": int,
    "approved_proposals": int,
    "rejected_proposals": int,
    
    # Scores
    "avg_orthogonality_score": float,
    "avg_risk_score": float,
    "avg_cost_score": float,
    
    # Time
    "total_ai_time_seconds": float,
    "total_human_time_seconds": float,
    
    # Violations
    "boundary_violations": int,
    
    # Backtest Results
    "backtest_passed_1x": int,
    "backtest_passed_1_5x": int,
    "backtest_passed_2x": int,
}
```

### 4.2 收集方法

通过 AuditService 和 CommitteeAuditService 的 API 端点收集：

```
GET /api/portfolio-research/audit/violations
GET /api/portfolio-research/audit/decisions
GET /api/portfolio-research/audit/agent-performance
```

---

## 5. 评估通过标准

### 5.1 必须满足（任一不满足则不继续扩展）

| 指标 | 通过标准 | 当前状态 |
|------|----------|----------|
| 边界违规次数 | = 0 | 待验证 |
| Proposal 通过率 | > 20% | 待验证 |

### 5.2 期望满足（用于判断扩展优先级）

| 指标 | 目标 | 当前状态 |
|------|------|----------|
| Orthogonality 得分 | > 0.7 | 待验证 |
| 人工审查耗时 | < 30 min/proposal | 待验证 |
| 成本后样本外通过率 | > 50% | 待验证 |

---

## 6. 评估执行

### 6.1 自动化评估脚本

```bash
python scripts/evaluate_committee_vs_baseline.py \
    --output reports/phase8_task8_eval_results.json \
    --compare-baselines
```

### 6.2 手动验证清单

- [ ] 运行至少 10 个 Committee Runs
- [ ] 验证每个 run 的 trace_id 可追溯
- [ ] 验证边界违规检测工作正常
- [ ] 验证 HITL 审批流程正常工作
- [ ] 检查审计日志完整性

---

## 7. 评估结果模板

```json
{
    "evaluation_date": "2026-04-XX",
    "total_runs": 10,
    "proposal_throughput_rate": 0.XX,
    "orthogonality_score_avg": 0.XX,
    "cost_stress_pass_rate": 0.XX,
    "avg_human_review_minutes": XX.X,
    "boundary_violations": 0,
    "verdict": "PASS|CONDITIONAL|FAIL",
    "recommendation": "EXPAND|CONTINUE|MODIFY|SUSPEND"
}
```

---

## 8. 后续行动

### 8.1 如果评估通过 (VERDICT = PASS)

- 继续扩展 Committee Agents
- 增加更多 Specialist 类型
- 优化 Red Team Agents

### 8.2 如果评估条件通过 (VERDICT = CONDITIONAL)

- 修复识别出的问题
- 重新评估
- 可能需要调整阈值

### 8.3 如果评估失败 (VERDICT = FAIL)

- 暂停 Committee 扩展
- 分析失败原因
- 重新设计流程

---

## 9. 附录

### 9.1 相关文档

- `docs/PHASE7_TASK7_MULTI_AGENT_PORTFOLIO.md` - Committee 完整定义
- `docs/adr/ADR-007-agent-boundary-constraints.md` - Agent 边界约束
- `services/committee_audit_service.py` - 审计服务
- `scripts/evaluate_committee_vs_baseline.py` - 评估脚本

### 9.2 版本历史

| 版本 | 日期 | 修改内容 |
|------|------|----------|
| 1.0.0 | 2026-04-04 | 初始版本 |
