-- ═══════════════════════════════════════════════════════════════
-- 6stockPulse · TimescaleDB Hypertable Migration
-- ═══════════════════════════════════════════════════════════════
-- 将时间序列表转换为 TimescaleDB hypertable，获得自动分区、压缩、查询加速

-- 1. 启用 TimescaleDB 扩展
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 2. 行情表 → hypertable
SELECT create_hypertable('stock_prices', 'trade_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 3. 资金流 → hypertable
SELECT create_hypertable('fund_flow', 'trade_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 4. 板块资金流 → hypertable
SELECT create_hypertable('sector_fund_flow', 'trade_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 5. 全球指数 → hypertable
SELECT create_hypertable('global_indices', 'trade_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 6. 自动压缩策略（7 天后压缩历史数据）
SELECT add_compression_policy('stock_prices', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_compression_policy('fund_flow', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_compression_policy('sector_fund_flow', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_compression_policy('global_indices', INTERVAL '7 days', if_not_exists => TRUE);

-- 7. 自动清理策略（保留 90 天原始数据，之后只保留压缩块）
SELECT add_retention_policy('stock_prices', INTERVAL '90 days', if_not_exists => TRUE);
SELECT add_retention_policy('fund_flow', INTERVAL '90 days', if_not_exists => TRUE);
