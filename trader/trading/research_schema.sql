CREATE SCHEMA IF NOT EXISTS research;
CREATE TABLE IF NOT EXISTS research.datasets (
 dataset_id text PRIMARY KEY,
 document jsonb NOT NULL,
 metadata jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Older databases predate the catalog metadata column.
ALTER TABLE research.datasets ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
-- Link between an imported Binance Vision archive and its Research dataset.
-- The market payload is never duplicated here: only provenance and coverage.
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
CREATE TABLE IF NOT EXISTS research.backtest_jobs (
 run_id text PRIMARY KEY,
 manifest jsonb NOT NULL,
 manifest_hash text NOT NULL,
 status text NOT NULL DEFAULT 'QUEUED',
 attempt integer NOT NULL DEFAULT 0,
 fencing_token bigint NOT NULL DEFAULT 0,
 lease_owner text,
 lease_until timestamptz,
 cancel_requested boolean NOT NULL DEFAULT false,
 result jsonb,
 error text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED'))
);
CREATE INDEX IF NOT EXISTS research_jobs_claim_idx ON research.backtest_jobs(status,lease_until,created_at);
