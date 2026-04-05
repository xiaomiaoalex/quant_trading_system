-- Migration 004: Portfolio Committee Tables
-- ==========================================
-- 多 Agent 策略组合开发委员会相关表
--
-- 创建时间: 2026-04-04
-- 依赖: 001_feature_store, 002_event_log, 003_projections

-- 1. committee_runs - 委员会运行记录
-- ==================================
CREATE TABLE IF NOT EXISTS committee_runs (
    run_id VARCHAR(100) PRIMARY KEY,
    research_request TEXT NOT NULL,
    context_package_version VARCHAR(50),
    
    -- Agent 输出
    sleeve_proposals_json JSONB,
    portfolio_proposal_json JSONB,
    review_results_json JSONB,
    
    -- 决策
    human_decision VARCHAR(50),
    approver VARCHAR(100),
    decision_reason TEXT,
    backtest_job_id VARCHAR(100),
    final_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    
    -- 版本追踪
    feature_version VARCHAR(50),
    prompt_version VARCHAR(50),
    trace_id VARCHAR(100) NOT NULL,
    
    -- 状态
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    
    -- 时间戳
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_committee_runs_status ON committee_runs(status);
CREATE INDEX IF NOT EXISTS idx_committee_runs_trace_id ON committee_runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_committee_runs_created_at ON committee_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_committee_runs_backtest_job_id ON committee_runs(backtest_job_id);


-- 2. sleeve_proposals - 策略提案
-- ==================================
CREATE TABLE IF NOT EXISTS sleeve_proposals (
    proposal_id VARCHAR(100) PRIMARY KEY,
    specialist_type VARCHAR(50) NOT NULL,
    hypothesis TEXT NOT NULL,
    
    -- 特征与状态
    required_features JSONB,
    regime VARCHAR(100),
    failure_modes JSONB,
    cost_assumptions_json JSONB,
    evidence_refs JSONB,
    
    -- 版本追踪
    feature_version VARCHAR(50),
    prompt_version VARCHAR(50),
    trace_id VARCHAR(100) NOT NULL,
    
    -- 状态
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    content_hash VARCHAR(64),
    
    -- 时间戳
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_sleeve_proposals_specialist_type ON sleeve_proposals(specialist_type);
CREATE INDEX IF NOT EXISTS idx_sleeve_proposals_status ON sleeve_proposals(status);
CREATE INDEX IF NOT EXISTS idx_sleeve_proposals_trace_id ON sleeve_proposals(trace_id);
CREATE INDEX IF NOT EXISTS idx_sleeve_proposals_regime ON sleeve_proposals(regime);
CREATE INDEX IF NOT EXISTS idx_sleeve_proposals_content_hash ON sleeve_proposals(content_hash);


-- 3. portfolio_proposals - 组合提案
-- ==================================
CREATE TABLE IF NOT EXISTS portfolio_proposals (
    proposal_id VARCHAR(100) PRIMARY KEY,
    
    -- 组合构成
    sleeves_json JSONB,
    capital_caps_json JSONB,
    regime_conditions_json JSONB,
    conflict_priorities_json JSONB,
    
    -- 风险说明
    risk_explanation TEXT,
    evaluation_task_id VARCHAR(100),
    
    -- 版本追踪
    feature_version VARCHAR(50),
    prompt_version VARCHAR(50),
    trace_id VARCHAR(100) NOT NULL,
    
    -- 状态
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    content_hash VARCHAR(64),
    
    -- 时间戳
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_portfolio_proposals_status ON portfolio_proposals(status);
CREATE INDEX IF NOT EXISTS idx_portfolio_proposals_trace_id ON portfolio_proposals(trace_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_proposals_evaluation_task_id ON portfolio_proposals(evaluation_task_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_proposals_content_hash ON portfolio_proposals(content_hash);


-- 4. review_reports - 评审报告
-- ===============================
CREATE TABLE IF NOT EXISTS review_reports (
    report_id VARCHAR(100) PRIMARY KEY,
    proposal_id VARCHAR(100) NOT NULL,
    reviewer_type VARCHAR(50) NOT NULL,
    
    -- 评审结论
    verdict VARCHAR(50) NOT NULL,
    concerns JSONB,
    suggestions JSONB,
    
    -- 评分
    orthogonality_score FLOAT,
    risk_score FLOAT,
    cost_score FLOAT,
    
    -- 版本追踪
    feature_version VARCHAR(50),
    prompt_version VARCHAR(50),
    trace_id VARCHAR(100) NOT NULL,
    
    -- 时间戳
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_review_reports_proposal_id ON review_reports(proposal_id);
CREATE INDEX IF NOT EXISTS idx_review_reports_reviewer_type ON review_reports(reviewer_type);
CREATE INDEX IF NOT EXISTS idx_review_reports_trace_id ON review_reports(trace_id);
CREATE INDEX IF NOT EXISTS idx_review_reports_verdict ON review_reports(verdict);


-- 5. agent_audit_log - Agent 审计日志
-- =====================================
CREATE TABLE IF NOT EXISTS agent_audit_log (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    agent_type VARCHAR(50),
    trace_id VARCHAR(100),
    content_hash VARCHAR(64),
    context JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_agent_audit_event_type ON agent_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_agent_audit_agent_type ON agent_audit_log(agent_type);
CREATE INDEX IF NOT EXISTS idx_agent_audit_trace_id ON agent_audit_log(trace_id);
CREATE INDEX IF NOT EXISTS idx_agent_audit_created_at ON agent_audit_log(created_at DESC);


-- 约束: content_hash 唯一性（同一 proposal 内容不重复存储）
-- 注意: 只有在 content_hash 不为空时才强制唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_sleeve_proposals_content_hash_unique
    ON sleeve_proposals(content_hash)
    WHERE content_hash IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_proposals_content_hash_unique
    ON portfolio_proposals(content_hash)
    WHERE content_hash IS NOT NULL;
