-- Event Store Migration
-- Version: 002
-- Description: Add stream_key, seq, and schema_version for idempotent event append
-- Created: 2026-03-21

-- Add columns for stream-based event ordering (if not exist)
-- Using DO NOTHING ON CONFLICT for idempotent migration
DO $$
BEGIN
    -- Add stream_key column (nullable first, will backfill)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'event_log' AND column_name = 'stream_key') THEN
        ALTER TABLE event_log ADD COLUMN stream_key VARCHAR(255);
    END IF;
    
    -- Add seq column for ordering within stream (nullable first, will backfill)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'event_log' AND column_name = 'seq') THEN
        ALTER TABLE event_log ADD COLUMN seq BIGINT;
    END IF;
    
    -- Add schema_version for future migrations
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'event_log' AND column_name = 'schema_version') THEN
        ALTER TABLE event_log ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;
    END IF;
    
    -- Add ts_ms column for millisecond timestamp (if not exist)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'event_log' AND column_name = 'ts_ms') THEN
        ALTER TABLE event_log ADD COLUMN ts_ms BIGINT;
    END IF;
END $$;

-- Backfill ts_ms from timestamp if empty
UPDATE event_log SET ts_ms = EXTRACT(EPOCH FROM timestamp)::BIGINT * 1000 WHERE ts_ms IS NULL;

-- Backfill stream_key for rows where it is NULL
-- Use the event_id to create a unique legacy stream_key for each existing row
-- Use gen_random_uuid() for NULL event_id to ensure uniqueness (random() can produce duplicates)
UPDATE event_log 
SET stream_key = 'legacy-' || COALESCE(event_id, gen_random_uuid()::varchar)
WHERE stream_key IS NULL;

-- Backfill seq for rows where it is NULL
-- Assign seq = 0 to all existing rows (they become their own single-event streams)
UPDATE event_log SET seq = 0 WHERE seq IS NULL;

-- Now make columns NOT NULL after backfill
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'event_log' AND column_name = 'stream_key' 
               AND is_nullable = 'YES') THEN
        ALTER TABLE event_log ALTER COLUMN stream_key SET NOT NULL;
    END IF;
    
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'event_log' AND column_name = 'seq' 
               AND is_nullable = 'YES') THEN
        ALTER TABLE event_log ALTER COLUMN seq SET NOT NULL;
    END IF;
END $$;

-- Create unique constraint for idempotent append
-- This ensures that (stream_key, seq) is unique, allowing ON CONFLICT DO NOTHING
-- Using CONCURRENTLY to avoid locking the table during index creation
-- 
-- NOTE: IF NOT EXISTS only checks index name, not definition.
-- If an index with same name but different columns exists, it would be silently skipped.
-- To handle this safely, we check if the index exists with correct definition.
DO $
BEGIN
    -- Check if index exists but with wrong definition (not unique or wrong columns)
    IF EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE indexname = 'idx_event_log_stream_key_seq'
        AND (
            -- Index exists but is not unique
            NOT indisunique
            -- Or has different column definition
            OR pg_get_indexdef(indexrelid) NOT LIKE '%(stream_key, seq)%'
        )
    ) THEN
        -- Drop the incorrectly defined index
        DROP INDEX CONCURRENTLY IF EXISTS idx_event_log_stream_key_seq;
        -- Create correct index
        CREATE UNIQUE INDEX CONCURRENTLY idx_event_log_stream_key_seq 
        ON event_log(stream_key, seq);
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE indexname = 'idx_event_log_stream_key_seq'
    ) THEN
        -- Index doesn't exist, create it
        CREATE UNIQUE INDEX CONCURRENTLY idx_event_log_stream_key_seq 
        ON event_log(stream_key, seq);
    END IF;
    -- If index exists with correct definition, do nothing (idempotent)
END $;

-- Create index on stream_key for efficient stream queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_event_log_stream_key 
ON event_log(stream_key);

-- Create index on ts_ms for time-based queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_event_log_ts_ms 
ON event_log(ts_ms DESC);

-- Rename data column to payload if following new naming convention (optional, non-breaking)
-- Note: We keep 'data' as is to maintain backward compatibility

COMMENT ON TABLE event_log IS 'Event store with idempotent append support. Unique constraint on (stream_key, seq) ensures repeated append of same event is idempotent.';
COMMENT ON COLUMN event_log.stream_key IS 'Event stream identifier (e.g., order-123, position-456)';
COMMENT ON COLUMN event_log.seq IS 'Sequence number within stream for ordering';
COMMENT ON COLUMN event_log.schema_version IS 'Schema version for event format evolution';
COMMENT ON COLUMN event_log.ts_ms IS 'Event timestamp in milliseconds for precise ordering';