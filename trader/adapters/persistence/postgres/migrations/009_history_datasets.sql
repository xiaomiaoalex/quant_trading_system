-- History Data Migration
-- Version: 009
-- Description: Create history_ingest_jobs / history_datasets for Binance Vision
--              spot 1m archive ingestion (download -> verify -> import -> publish)
-- Created: 2026-10-29

-- ==================== History ingestion jobs ====================
-- One row per user-visible ingestion request.
-- Lifecycle: QUEUED -> RUNNING -> SUCCEEDED / FAILED
CREATE TABLE IF NOT EXISTS history_ingest_jobs (
    job_id VARCHAR(64) PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    market VARCHAR(16) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    interval VARCHAR(8) NOT NULL,
    month VARCHAR(7) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
    stage VARCHAR(32) NOT NULL DEFAULT 'QUEUED',
    dataset_id VARCHAR(64),
    feature_version VARCHAR(128),
    rows_processed BIGINT NOT NULL DEFAULT 0,
    bytes_downloaded BIGINT NOT NULL DEFAULT 0,
    error_code VARCHAR(64),
    error_reason TEXT,
    requested_by VARCHAR(128) NOT NULL DEFAULT 'console_user',
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_history_ingest_jobs_status
        CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'))
);

-- At most one active job per (source, symbol, interval, month).
-- Duplicate submissions reuse the active job instead of creating a second one.
CREATE UNIQUE INDEX IF NOT EXISTS uq_history_ingest_jobs_active
    ON history_ingest_jobs(source, symbol, interval, month)
    WHERE status IN ('QUEUED', 'RUNNING');

CREATE INDEX IF NOT EXISTS idx_history_ingest_jobs_status
    ON history_ingest_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_history_ingest_jobs_lookup
    ON history_ingest_jobs(source, market, symbol, interval, month);

-- ==================== History datasets ====================
-- A published dataset is an immutable, versioned snapshot of one archive.
-- feature_version points at the FeatureStore rows holding the OHLCV payload.
CREATE TABLE IF NOT EXISTS history_datasets (
    dataset_id VARCHAR(64) PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    market VARCHAR(16) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    interval VARCHAR(8) NOT NULL,
    month VARCHAR(7) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    feature_version VARCHAR(128) NOT NULL,
    archive_path TEXT NOT NULL,
    archive_sha256 VARCHAR(64) NOT NULL,
    official_sha256 VARCHAR(64),
    source_url TEXT NOT NULL,
    checksum_url TEXT NOT NULL,
    row_count BIGINT NOT NULL DEFAULT 0,
    expected_points BIGINT NOT NULL DEFAULT 0,
    gap_count BIGINT NOT NULL DEFAULT 0,
    first_ts_ms BIGINT,
    last_ts_ms BIGINT,
    quality JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    persistence_ok BOOLEAN NOT NULL DEFAULT FALSE,
    backtest_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    job_id VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    published_at TIMESTAMP WITH TIME ZONE,

    CONSTRAINT uq_history_datasets_version
        UNIQUE (source, market, symbol, interval, month, version),
    CONSTRAINT ck_history_datasets_status
        CHECK (status IN ('PENDING', 'PUBLISHED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_history_datasets_lookup
    ON history_datasets(source, market, symbol, interval, month);

CREATE INDEX IF NOT EXISTS idx_history_datasets_status
    ON history_datasets(status, backtest_eligible);

CREATE INDEX IF NOT EXISTS idx_history_datasets_feature_version
    ON history_datasets(feature_version);

COMMENT ON TABLE history_ingest_jobs IS 'Binance Vision archive ingestion jobs. Active job uniqueness is enforced by a partial unique index so duplicate submissions reuse the same job.';
COMMENT ON TABLE history_datasets IS 'Versioned, immutable history datasets. Only PUBLISHED rows with persistence_ok and backtest_eligible may be used for research backtests.';
COMMENT ON COLUMN history_datasets.archive_sha256 IS 'Actual SHA-256 computed from the downloaded archive bytes.';
COMMENT ON COLUMN history_datasets.official_sha256 IS 'SHA-256 published by the source in the official .CHECKSUM file.';
