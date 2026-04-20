-- Executions Migration
-- Version: 005
-- Description: Add executions table with unique constraint on (cl_ord_id, exec_id)
-- Created: 2026-04-20
-- Task 17: Fill Dedup & Idempotency

-- ==================== Executions Table ====================

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    cl_ord_id TEXT NOT NULL,
    exec_id TEXT NOT NULL,
    symbol TEXT,
    side TEXT,
    quantity TEXT,
    price TEXT,
    fee TEXT,
    fee_currency TEXT,
    ts_ms BIGINT,
    strategy_id TEXT,
    venue TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    -- Task 17: Unique constraint for idempotency
    UNIQUE(cl_ord_id, exec_id)
);

COMMENT ON TABLE executions IS 'Execution records with cl_ord_id + exec_id idempotency';
COMMENT ON COLUMN executions.execution_id IS 'Unique execution identifier';
COMMENT ON COLUMN executions.cl_ord_id IS 'Client order ID (strategy prefix + uuid)';
COMMENT ON COLUMN executions.exec_id IS 'Execution ID from exchange';
COMMENT ON COLUMN executions.symbol IS 'Trading symbol';
COMMENT ON COLUMN executions.side IS 'Order side (BUY/SELL)';
COMMENT ON COLUMN executions.quantity IS 'Executed quantity';
COMMENT ON COLUMN executions.price IS 'Execution price';
COMMENT ON COLUMN executions.fee IS 'Execution fee';
COMMENT ON COLUMN executions.fee_currency IS 'Fee currency';
COMMENT ON COLUMN executions.ts_ms IS 'Execution timestamp in milliseconds';
COMMENT ON COLUMN executions.strategy_id IS 'Strategy that generated this execution';
COMMENT ON COLUMN executions.venue IS 'Exchange/venue name';

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_executions_cl_ord_id ON executions(cl_ord_id);
CREATE INDEX IF NOT EXISTS idx_executions_strategy_id ON executions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_executions_symbol ON executions(symbol);
CREATE INDEX IF NOT EXISTS idx_executions_ts_ms ON executions(ts_ms);
