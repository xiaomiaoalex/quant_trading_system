CREATE SCHEMA IF NOT EXISTS research;
CREATE TABLE IF NOT EXISTS research.datasets (
 dataset_id text PRIMARY KEY,
 document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
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
