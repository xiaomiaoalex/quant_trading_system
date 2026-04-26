-- Migration 006: 混合持仓跟踪系统（Batch 1）
-- 策略级持仓投影 + Lot 追踪 + 账户持仓投影 + 对账日志
-- =============================================================================

-- ------------------------------------------------------------------
-- 策略级持仓投影表
-- aggregate_id = {strategy_id}:{symbol}
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_positions_proj (
    aggregate_id      TEXT PRIMARY KEY,
    state             JSONB NOT NULL DEFAULT '{}',
    version           INT NOT NULL DEFAULT 1,
    last_event_seq    INT NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sp_symbol
    ON strategy_positions_proj ((state->>'symbol'));
CREATE INDEX IF NOT EXISTS idx_sp_strategy
    ON strategy_positions_proj ((state->>'strategy_id'));
CREATE INDEX IF NOT EXISTS idx_sp_status
    ON strategy_positions_proj ((state->>'status'));

COMMENT ON TABLE strategy_positions_proj IS
    '策略级持仓投影，按 strategy_id:symbol 隔离，支持 FIFO/Lot 追踪';

-- ------------------------------------------------------------------
-- 账户级持仓投影表（包含历史持仓）
-- aggregate_id = {venue}:{symbol}
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_positions_proj (
    aggregate_id      TEXT PRIMARY KEY,
    state             JSONB NOT NULL DEFAULT '{}',
    version           INT NOT NULL DEFAULT 1,
    last_event_seq    INT NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ap_symbol
    ON account_positions_proj ((state->>'symbol'));
CREATE INDEX IF NOT EXISTS idx_ap_historical
    ON account_positions_proj ((state->>'symbol'), (state->>'historical_flag'))
    WHERE state->>'historical_flag' = 'true';

COMMENT ON TABLE account_positions_proj IS
    '账户级持仓投影（Broker API 真相源），包含历史持仓标记';

-- ------------------------------------------------------------------
-- Lot 追踪表
-- 一笔买入 = 一条记录，支持部分成交、部分平仓、费用追踪
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS position_lots (
    lot_id          TEXT PRIMARY KEY,
    position_id     TEXT NOT NULL,           -- {strategy_id}:{symbol}
    strategy_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    original_qty    NUMERIC(36,18) NOT NULL,
    remaining_qty   NUMERIC(36,18) NOT NULL,
    fill_price      NUMERIC(36,18) NOT NULL,
    fee_qty         NUMERIC(36,18) NOT NULL DEFAULT 0,
    fee_asset       TEXT,
    realized_pnl    NUMERIC(36,18) NOT NULL DEFAULT 0,
    filled_at       TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    is_closed       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lots_position
    ON position_lots (position_id);
CREATE INDEX IF NOT EXISTS idx_lots_strategy_symbol
    ON position_lots (strategy_id, symbol);
-- 仅查询未关闭 lot（高频）
CREATE INDEX IF NOT EXISTS idx_lots_open
    ON position_lots (strategy_id, symbol)
    WHERE NOT is_closed;

COMMENT ON TABLE position_lots IS
    '批次追踪表，每笔买入一条记录，支持 FIFO 和费用计算';

-- ------------------------------------------------------------------
-- 对账日志表
-- 记录每次对账的结果，差异超阈值时触发告警/KillSwitch
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reconciliation_log (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    broker_qty      NUMERIC(36,18) NOT NULL,
    oms_total_qty   NUMERIC(36,18) NOT NULL,
    historical_qty  NUMERIC(36,18) NOT NULL DEFAULT 0,
    difference      NUMERIC(36,18) NOT NULL,
    tolerance       NUMERIC(36,18) NOT NULL,
    status          TEXT NOT NULL,           -- CONSISTENT / DISCREPANCY / HISTORICAL_GAP
    resolution      TEXT,                     -- NONE / AUTO_ALIGNED / ALERTED / KILLSWITCH_L1
    details         JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recon_status_created
    ON reconciliation_log (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recon_symbol_created
    ON reconciliation_log (symbol, created_at DESC);

COMMENT ON TABLE reconciliation_log IS
    '持仓对账日志，记录 broker vs OMS 差异及处理结果';

-- ------------------------------------------------------------------
-- 快照表（策略持仓快照，用于快速恢复）
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_position_snapshots (
    snapshot_id     BIGSERIAL PRIMARY KEY,
    aggregate_id    TEXT NOT NULL,           -- {strategy_id}:{symbol}
    state           JSONB NOT NULL,
    version         INT NOT NULL,
    ts_ms           BIGINT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sps_aggregate
    ON strategy_position_snapshots (aggregate_id, created_at DESC);
