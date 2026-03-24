-- Projections Migration
-- Version: 003
-- Description: Add projection tables for positions, orders, and risk states
-- Created: 2026-03-24

-- ==================== Projection Tables ====================

-- Positions Projection Table
CREATE TABLE IF NOT EXISTS positions_proj (
    aggregate_id VARCHAR(255) PRIMARY KEY,
    state JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_event_seq BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE positions_proj IS 'Position read model projection from event store';
COMMENT ON COLUMN positions_proj.aggregate_id IS 'Position ID (position_id)';
COMMENT ON COLUMN positions_proj.state IS 'Full position state as JSONB';
COMMENT ON COLUMN positions_proj.version IS 'Projection version for optimistic locking';
COMMENT ON COLUMN positions_proj.last_event_seq IS 'Last processed event sequence number';

-- Orders Projection Table
CREATE TABLE IF NOT EXISTS orders_proj (
    aggregate_id VARCHAR(255) PRIMARY KEY,
    state JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_event_seq BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE orders_proj IS 'Order read model projection from event store';
COMMENT ON COLUMN orders_proj.aggregate_id IS 'Order ID (order_id)';
COMMENT ON COLUMN orders_proj.state IS 'Full order state as JSONB';
COMMENT ON COLUMN orders_proj.version IS 'Projection version for optimistic locking';
COMMENT ON COLUMN orders_proj.last_event_seq IS 'Last processed event sequence number';

-- Risk States Projection Table
CREATE TABLE IF NOT EXISTS risk_states_proj (
    aggregate_id VARCHAR(255) PRIMARY KEY,
    state JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_event_seq BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE risk_states_proj IS 'Risk state read model projection from event store';
COMMENT ON COLUMN risk_states_proj.aggregate_id IS 'Risk scope (e.g., GLOBAL, strategy:name, account:id)';
COMMENT ON COLUMN risk_states_proj.state IS 'Full risk state as JSONB';
COMMENT ON COLUMN risk_states_proj.version IS 'Projection version for optimistic locking';
COMMENT ON COLUMN risk_states_proj.last_event_seq IS 'Last processed event sequence number';

-- ==================== Snapshot Tables ====================

-- Positions Snapshot Table
CREATE TABLE IF NOT EXISTS positions_snapshots (
    aggregate_id VARCHAR(255) NOT NULL,
    projection_type VARCHAR(100) NOT NULL,
    state JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_event_seq BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_positions_snapshots PRIMARY KEY (aggregate_id, projection_type)
);

COMMENT ON TABLE positions_snapshots IS 'Position projection snapshots for fast rebuild';
COMMENT ON COLUMN positions_snapshots.aggregate_id IS 'Position ID';
COMMENT ON COLUMN positions_snapshots.projection_type IS 'Projection type identifier';

-- Orders Snapshot Table
CREATE TABLE IF NOT EXISTS orders_snapshots (
    aggregate_id VARCHAR(255) NOT NULL,
    projection_type VARCHAR(100) NOT NULL,
    state JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_event_seq BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_orders_snapshots PRIMARY KEY (aggregate_id, projection_type)
);

COMMENT ON TABLE orders_snapshots IS 'Order projection snapshots for fast rebuild';
COMMENT ON COLUMN orders_snapshots.aggregate_id IS 'Order ID';
COMMENT ON COLUMN orders_snapshots.projection_type IS 'Projection type identifier';

-- Risk Snapshots Table
CREATE TABLE IF NOT EXISTS risk_snapshots (
    aggregate_id VARCHAR(255) NOT NULL,
    projection_type VARCHAR(100) NOT NULL,
    state JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_event_seq BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_risk_snapshots PRIMARY KEY (aggregate_id, projection_type)
);

COMMENT ON TABLE risk_snapshots IS 'Risk state projection snapshots for fast rebuild';
COMMENT ON COLUMN risk_snapshots.aggregate_id IS 'Risk scope';
COMMENT ON COLUMN risk_snapshots.projection_type IS 'Projection type identifier';

-- ==================== Indexes ====================

-- Positions indexes
CREATE INDEX IF NOT EXISTS idx_positions_proj_symbol ON positions_proj ((state->>'symbol'));
CREATE INDEX IF NOT EXISTS idx_positions_proj_updated_at ON positions_proj (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_positions_proj_version ON positions_proj (version);

-- Orders indexes
CREATE INDEX IF NOT EXISTS idx_orders_proj_client_order_id ON orders_proj ((state->>'client_order_id'));
CREATE INDEX IF NOT EXISTS idx_orders_proj_symbol ON orders_proj ((state->>'symbol'));
CREATE INDEX IF NOT EXISTS idx_orders_proj_status ON orders_proj ((state->>'status'));
CREATE INDEX IF NOT EXISTS idx_orders_proj_created_at ON orders_proj (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_proj_version ON orders_proj (version);

-- Risk states indexes
CREATE INDEX IF NOT EXISTS idx_risk_states_proj_scope ON risk_states_proj ((state->>'scope'));
CREATE INDEX IF NOT EXISTS idx_risk_states_proj_current_level ON risk_states_proj ((state->>'current_level')::INTEGER);
CREATE INDEX IF NOT EXISTS idx_risk_states_proj_updated_at ON risk_states_proj (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_states_proj_version ON risk_states_proj (version);

-- Snapshot indexes
CREATE INDEX IF NOT EXISTS idx_positions_snapshots_updated_at ON positions_snapshots (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_snapshots_updated_at ON orders_snapshots (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_snapshots_updated_at ON risk_snapshots (updated_at DESC);

-- ==================== Helper Functions ====================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for auto-updating updated_at
DROP TRIGGER IF EXISTS trigger_positions_proj_updated_at ON positions_proj;
CREATE TRIGGER trigger_positions_proj_updated_at
    BEFORE UPDATE ON positions_proj
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_orders_proj_updated_at ON orders_proj;
CREATE TRIGGER trigger_orders_proj_updated_at
    BEFORE UPDATE ON orders_proj
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_risk_states_proj_updated_at ON risk_states_proj;
CREATE TRIGGER trigger_risk_states_proj_updated_at
    BEFORE UPDATE ON risk_states_proj
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_positions_snapshots_updated_at ON positions_snapshots;
CREATE TRIGGER trigger_positions_snapshots_updated_at
    BEFORE UPDATE ON positions_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_orders_snapshots_updated_at ON orders_snapshots;
CREATE TRIGGER trigger_orders_snapshots_updated_at
    BEFORE UPDATE ON orders_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_risk_snapshots_updated_at ON risk_snapshots;
CREATE TRIGGER trigger_risk_snapshots_updated_at
    BEFORE UPDATE ON risk_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
