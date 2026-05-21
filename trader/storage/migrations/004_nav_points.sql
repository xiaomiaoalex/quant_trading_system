-- Migration 004: NAV 净值时序表
-- 用于记录每次成交后的净资产价值快照

CREATE TABLE IF NOT EXISTS nav_points (
    id             BIGSERIAL PRIMARY KEY,
    deployment_id  TEXT NOT NULL,
    strategy_id    TEXT NOT NULL,
    timestamp_ms   BIGINT NOT NULL,
    equity         NUMERIC(20, 8) NOT NULL,
    cash           NUMERIC(20, 8) DEFAULT 0,
    unrealized_pnl NUMERIC(20, 8) DEFAULT 0,
    realized_pnl   NUMERIC(20, 8) DEFAULT 0,
    total_pnl      NUMERIC(20, 8) DEFAULT 0,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nav_deployment_ts
    ON nav_points(deployment_id, timestamp_ms);
