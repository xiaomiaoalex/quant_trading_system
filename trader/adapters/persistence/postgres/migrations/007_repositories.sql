-- Repositories Migration
-- Version: 007
-- Description: PG-first repository support tables

CREATE TABLE IF NOT EXISTS killswitch_log (
    id SERIAL PRIMARY KEY,
    scope TEXT NOT NULL,
    level INTEGER NOT NULL,
    reason TEXT,
    updated_by TEXT,
    previous_level INTEGER NOT NULL DEFAULT 0,
    ts_ms BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_killswitch_log_scope ON killswitch_log(scope);
CREATE INDEX IF NOT EXISTS idx_killswitch_log_ts_ms ON killswitch_log(ts_ms);

CREATE TABLE IF NOT EXISTS strategy_runtime_states (
    deployment_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    status TEXT NOT NULL,
    config JSONB DEFAULT '{}',
    symbols JSONB DEFAULT '[]',
    account_id TEXT,
    venue TEXT,
    mode TEXT,
    env TEXT,
    started_at BIGINT,
    last_tick_at BIGINT,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_runtime_states_strategy_id ON strategy_runtime_states(strategy_id);
CREATE INDEX IF NOT EXISTS idx_runtime_states_status ON strategy_runtime_states(status);
