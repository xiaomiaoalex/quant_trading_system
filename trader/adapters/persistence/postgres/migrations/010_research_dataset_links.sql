-- Research Dataset Links
-- Version: 010
-- Description: Link an imported Binance Vision archive to its Research dataset.
--              Adds history_datasets.research_dataset_id and creates
--              research.dataset_links, which carries provenance only (never the
--              market payload).
-- Created: 2026-10-31

CREATE SCHEMA IF NOT EXISTS research;

-- ==================== History dataset -> Research dataset ====================
ALTER TABLE history_datasets
    ADD COLUMN IF NOT EXISTS research_dataset_id VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_history_datasets_research_dataset
    ON history_datasets(research_dataset_id);

-- ==================== Research provenance links ====================
-- One row per Research dataset that was produced from an official archive.
-- The link is the trusted provenance record: a dataset is only "official"
-- eligible when this row exists and the referenced history dataset is
-- PUBLISHED with persistence_ok and backtest_eligible.
CREATE TABLE IF NOT EXISTS research.dataset_links (
    research_dataset_id text PRIMARY KEY,
    history_dataset_id text UNIQUE,
    source_url text,
    checksum_url text,
    official_sha256 text,
    archive_sha256 text,
    archive_version integer,
    feature_version text,
    symbol text NOT NULL,
    interval text NOT NULL,
    month text NOT NULL,
    coverage_start_ms bigint,
    coverage_end_ms bigint,
    bar_count bigint NOT NULL DEFAULT 0,
    conversion_version text NOT NULL,
    quality jsonb NOT NULL DEFAULT '{}',
    provenance_hash text NOT NULL,
    official_eligible boolean NOT NULL DEFAULT false,
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS research_dataset_links_lookup_idx
    ON research.dataset_links(symbol, month);

COMMENT ON TABLE research.dataset_links IS 'Provenance of Research datasets imported from official Binance Vision archives. Stores URLs, digests, coverage and the provenance hash; never the market payload.';
COMMENT ON COLUMN research.dataset_links.provenance_hash IS 'SHA-256 over the canonical provenance payload. Frozen into the backtest manifest so a run can be traced back to the exact archive bytes.';
