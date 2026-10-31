-- Version 3: disposable query projection. Account state remains authoritative.
CREATE TABLE IF NOT EXISTS trading_order_projection (
    account_id text NOT NULL REFERENCES trading_accounts(account_id),
    cl_ord_id text NOT NULL,
    status text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (account_id, cl_ord_id)
);
