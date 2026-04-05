Phase-8-multi-agent-portfolio-committee


 

Phase 8 — Task 8：多 Agent 策略组合开发委员会

 

这个命名和你现有系统边界是兼容的：项目主线仍是 crypto-first、单账户、小资金、以趋势/量价/资金结构/链上/事件驱动为正式研究主线；AI 仍只能留在 Insight/Research 层，不能直接穿透到执行；回测主引擎仍以 Lean 为主、VectorBT 为辅。    

 

重排后的计划

 

 

Phase 8 / Task 8.0 — 真相源冻结

 

先做一件事：把本阶段唯一权威文档定下来。

目标不是写功能，而是防止 agent 读到冲突文档后输出错误 proposal。

交付物

docs/PHASE7_TASK7_MULTI_AGENT_PORTFOLIO.md
docs/adr/ADR_phase7_task7_agent_boundary.md
 

必须写死的边界

agent 只做研究与 proposal
agent 不能直接下单
agent 不能绕过 HITL
proposal 进入现有 lifecycle / backtest / approval 流水线
Lean primary，VectorBT secondary
 

 

 

 

Phase 8 / Task 8.1 — 定义组合研究工件

 

先定 schema，再写 agent。

否则最后得到的是聊天记录，不是系统资产。

新增工件

SleeveProposal
PortfolioProposal
ReviewReport
CommitteeRun
 

核心字段

hypothesis
required_features
regime
failure_modes
cost_assumptions
evidence_refs
feature_version
prompt_version
trace_id
 

交付物

insight/committee/schemas.py

adapters/persistence/portfolio_proposal_store.py

PG 表：

committee_runs
sleeve_proposals
portfolio_proposals
review_reports
 

 

 

 

Phase 8 / Task 8.2 — 五个 Specialist Agents

 

按你正式研究主线拆，不搞大而全 agent。

这五个正好对应现有系统的数据与信号主线。

角色

TrendAgent
PriceVolumeAgent
FundingOIAgent
OnChainAgent
EventRegimeAgent
 

职责

每个 agent 只输出 SleeveProposal，不输出交易指令，不输出可直接部署代码。

交付物

insight/committee/router.py
insight/committee/specialists/
tests/test_committee_specialists.py
 

 

 

 

Phase 8 / Task 8.3 — 两个反对派 Agent

 

这是 Task 8 的核心，不是附属件。

OrthogonalityAgent
只判断：

新 sleeve 是否和旧 sleeve 重复
是否只是换皮的同一风险暴露
是否能给组合带来真正增量
 

RiskCostRedTeamAgent
只负责否决：

数据是否脏
成本是否脆弱
流动性是否不足
失效条件是否清楚
是否越过 AI-clean / HITL 边界
 

交付物

insight/committee/orthogonality.py
insight/committee/red_team.py
tests/test_committee_reviewers.py
 

 

 

 

Phase 8 / Task 8.4 — Portfolio Constructor

 

这一步才叫“增强策略组合开发”。

它不研究单个 alpha，而是把多个 sleeve proposal 组合成有限个可测试的组合候选。

输出

active sleeves
每个 sleeve 的 capital cap
regime 启停条件
冲突优先级
组合级风险说明
自动生成评估任务
 

交付物

insight/committee/portfolio_constructor.py
services/portfolio_research_workflow.py
services/backtesting/backtest_batch_job.py
 

这里直接复用你现有回测增强成果，不另起一套 agent 评估体系。Phase 5 已经把 Lean 主回测、样本外验证和回测任务流打下来了。

 

 

 

Phase 8 / Task 8.5 — 接入现有 HITL / Lifecycle

 

不新建第二套审批系统。

直接走你已有的 AI 共创与生命周期链路：AI-clean、CodeSandbox、HITL、LifecycleManager。

链路改成

CommitteeRun -> Review -> Human Approve -> BacktestJob / StrategyDraft -> LifecycleManager

交付物

services/committee_to_lifecycle_adapter.py
api/routes/portfolio_research.py
tests/test_portfolio_research_e2e.py
 

 

 

 

Phase 8 / Task 8.6 — 审计与回放

 

这一步必须做，因为多 agent 最大的风险不是“想法少”，而是“责任不清”。

你现有系统已经把 event log、回放、审计留痕当成核心骨架，所以 Task 8 也必须遵守同样纪律。

每次 committee run 必须留下

输入需求
使用的上下文包版本
每个 agent 输出
review 结果
human decision
进入的 backtest job
最终淘汰/保留结论
 

交付物

services/committee_audit_service.py
tests/test_committee_audit.py
 

 

 

 

Phase 8 / Task 8.7 — 价值证明

 

如果不做这一步，Task 8 很容易变成昂贵的 brainstorm。

只看 5 个指标

proposal 通过率
orthogonality 得分
成本后样本外通过率
人工审查耗时
边界违规次数
 

交付物

reports/phase8_task8_eval.md
scripts/evaluate_committee_vs_baseline.py
 

保留条件

只有在“多 agent 比单 agent / 人工流程更能产生可通过的组合候选”时，Task 8 才继续扩展。

 

 

 

开发顺序

 

顺序固定：

8.0 真相源冻结

→ 8.1 工件 schema

→ 8.2 specialist agents

→ 8.3 反对派 agents

→ 8.4 portfolio constructor

→ 8.5 lifecycle / HITL integration

→ 8.6 audit & replay

→ 8.7 value proof

不要跳步。

因为没有 8.1，就没有结构化资产；

没有 8.3，就只有更多重复策略；

没有 8.7，就无法证明这套复杂度值得保留。

 

一句话版

 

你的编号现在应该写成：

Phase 8 / Task 8：多 Agent 策略组合开发委员会   

而且它的目标不是“让 agent 更会说”，而是：

把多视角研究，变成可审计、可回测、可审批、可淘汰的组合 proposal 流水线。
