-- Feature Store Migration
-- Version: 001
-- Description: Create feature_values table for versioned feature storage
-- Created: 2026-03-20

-- Feature values table with version control
CREATE TABLE IF NOT EXISTS feature_values (
    symbol VARCHAR(50) NOT NULL,
    feature_name VARCHAR(255) NOT NULL,
    version VARCHAR(50) NOT NULL,
    ts_ms BIGINT NOT NULL,
    value JSONB NOT NULL,
    meta JSONB DEFAULT '{}',
    value_hash VARCHAR(16) NOT NULL,
    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    
    -- Unique constraint: same key (symbol + feature_name + version + ts_ms) can only exist once
    CONSTRAINT uq_feature_values_key UNIQUE (symbol, feature_name, version, ts_ms)
);

-- Index for retrieving features by symbol, feature_name, version
CREATE INDEX IF NOT EXISTS idx_feature_values_symbol_feature_version 
ON feature_values(symbol, feature_name, version);

-- Index for time-based queries
CREATE INDEX IF NOT EXISTS idx_feature_values_ts_ms 
ON feature_values(ts_ms DESC);

-- Index for listing versions
CREATE INDEX IF NOT EXISTS idx_feature_values_version 
ON feature_values(symbol, feature_name, version);

COMMENT ON TABLE feature_values IS 'Versioned feature storage for Feature Store. Unique constraint ensures idempotent writes: same key with same value succeeds (is_duplicate=True), same key with different value raises FeatureVersionConflictError.';
COMMENT ON COLUMN feature_values.symbol IS 'Trading symbol (e.g., BTCUSDT)';
COMMENT ON COLUMN feature_values.feature_name IS 'Feature name (e.g., ema_20, volume_ratio)';
COMMENT ON COLUMN feature_values.version IS 'Feature version (e.g., v1, v2)';
COMMENT ON COLUMN feature_values.ts_ms IS 'Timestamp in milliseconds';
COMMENT ON COLUMN feature_values.value IS 'Feature value (JSON serialized)';
COMMENT ON COLUMN feature_values.meta IS 'Optional metadata';
COMMENT ON COLUMN feature_values.value_hash IS 'SHA256 hash of value for conflict detection';
