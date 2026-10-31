-- Spot trading schema (version 2).
-- One account transaction owns commands, executions, ledger, events and outbox.
CREATE TABLE IF NOT EXISTS trading_accounts (
    account_id text PRIMARY KEY,
    version bigint NOT NULL DEFAULT 0,
    state jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS trading_commands (
    account_id text NOT NULL REFERENCES trading_accounts(account_id),
    command_id text NOT NULL,
    cl_ord_id text NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    payload_hash text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (account_id, command_id)
);
CREATE INDEX IF NOT EXISTS trading_commands_status ON trading_commands (status);
CREATE TABLE IF NOT EXISTS trading_executions (
    execution_key text PRIMARY KEY,
    account_id text NOT NULL REFERENCES trading_accounts(account_id),
    cl_ord_id text NOT NULL,
    data jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS trading_executions_order ON trading_executions (account_id, cl_ord_id);
CREATE TABLE IF NOT EXISTS trading_ledger (
    account_id text NOT NULL REFERENCES trading_accounts(account_id),
    entry_id text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (account_id, entry_id)
);
CREATE TABLE IF NOT EXISTS trading_events (
    account_id text NOT NULL REFERENCES trading_accounts(account_id),
    event_id text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (account_id, event_id)
);
CREATE TABLE IF NOT EXISTS trading_outbox (
    account_id text NOT NULL REFERENCES trading_accounts(account_id),
    event_id text NOT NULL,
    seq bigserial,
    data jsonb NOT NULL,
    delivered boolean NOT NULL DEFAULT false,
    PRIMARY KEY (account_id, event_id)
);
ALTER TABLE trading_outbox ADD COLUMN IF NOT EXISTS seq bigserial;
CREATE INDEX IF NOT EXISTS trading_outbox_pending ON trading_outbox (account_id, delivered, seq);

-- Only one execution process may own an account at a time.
CREATE TABLE IF NOT EXISTS trading_runtime_leases (
    scope text PRIMARY KEY,
    owner text NOT NULL,
    fencing_token bigint NOT NULL DEFAULT 1,
    expires_at timestamptz NOT NULL
);
