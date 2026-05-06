-- Market-neutral risk audit events.
-- `risk:crypto` is a filtered stream_key view over this table, not a separate schema.

CREATE TABLE IF NOT EXISTS risk_audit_events (
    id BIGSERIAL PRIMARY KEY,
    stream_key VARCHAR(255) NOT NULL,
    event_type VARCHAR(255) NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    trace_id VARCHAR(255) NOT NULL,
    ts_ms BIGINT NOT NULL,
    asset_class VARCHAR(64) NOT NULL,
    venue VARCHAR(255) NOT NULL,
    account_id VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_audit_events_stream_key_ts
    ON risk_audit_events(stream_key, ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_risk_audit_events_event_type_ts
    ON risk_audit_events(event_type, ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_risk_audit_events_trace_id
    ON risk_audit_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_risk_audit_events_asset_venue_ts
    ON risk_audit_events(asset_class, venue, ts_ms DESC);
