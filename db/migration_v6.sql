-- ═══════════════════════════════════════════════════════════════════
-- 6stockPulse v6 · 板块资金流增强 (概念/行业 + 龙头股)
-- ===================================================================
-- 1) sector_fund_flow 加 sector_type(concept/industry) / leader_stock / leader_name
-- 2) 重建 latest_sector 物化视图(含新列) 与 v_sector_top 视图(用真实列,去 'concept' 常量)
-- ═══════════════════════════════════════════════════════════════════
ALTER TABLE sector_fund_flow ADD COLUMN IF NOT EXISTS sector_type   VARCHAR(20) DEFAULT 'concept';
ALTER TABLE sector_fund_flow ADD COLUMN IF NOT EXISTS leader_stock  VARCHAR(10);
ALTER TABLE sector_fund_flow ADD COLUMN IF NOT EXISTS leader_name   VARCHAR(50);

DROP VIEW IF EXISTS v_sector_top;
DROP MATERIALIZED VIEW IF EXISTS latest_sector;
CREATE MATERIALIZED VIEW IF NOT EXISTS latest_sector AS
SELECT DISTINCT ON (sector_code)
    sector_code, sector_name, sector_type, trade_date, trade_time,
    change_pct, main_net_inflow, main_net_ratio, turnover_rate,
    leader_stock, leader_name
FROM sector_fund_flow
ORDER BY sector_code, trade_time DESC;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ls_code ON latest_sector (sector_code);

DROP VIEW IF EXISTS v_sector_top;
CREATE VIEW v_sector_top AS
SELECT sector_code, sector_name, sector_type, trade_date,
       change_pct, main_net_inflow, main_net_ratio, turnover_rate,
       leader_stock, leader_name,
       RANK() OVER (PARTITION BY trade_date ORDER BY main_net_inflow DESC) AS rnk
FROM latest_sector;

SELECT '6stockPulse v6 migration complete.' AS status;
