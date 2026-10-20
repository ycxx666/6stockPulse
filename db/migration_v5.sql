-- ═══════════════════════════════════════════════════════════════════
-- 6stockPulse v5 · 增量修复 (2026-09-04)
-- ===================================================================
-- 1) prediction_result 表: 此前 schema/migration 均未建, 导致
--    /api/dashboard/snapshot 的 prediction 子查询报 relation not exist
-- 2) v_sector_top 视图补 sector_type 列 (JOIN sectors 取得),
--    修复 /api/sectors/flow 与 snapshot 的 sector_flow 子查询
-- ═══════════════════════════════════════════════════════════════════

-- ── 1. 次日预测结果表 ──
CREATE TABLE IF NOT EXISTS prediction_result (
    id                BIGSERIAL    PRIMARY KEY,
    target            VARCHAR(30)  NOT NULL,           -- ashare_index
    horizon           VARCHAR(10)  NOT NULL,           -- next_session
    up_probability    NUMERIC(6,4),
    flat_probability  NUMERIC(6,4),
    down_probability  NUMERIC(6,4),
    confidence        NUMERIC(6,4),
    feature_snapshot  JSONB        DEFAULT '{}'::jsonb,
    explanation       JSONB        DEFAULT '[]'::jsonb,
    model_version     VARCHAR(20)  DEFAULT 'rule-v1',
    actual_result     VARCHAR(10),                     -- 回测标注: up/flat/down
    predicted_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pr_target_time ON prediction_result (target, predicted_at DESC);

-- ── 2. 板块 TOP 视图: 补 sector_type 列 ──
-- 注: sector_fund_flow/latest_sector 无板块类型维度(东财概念/行业同 BK 前缀),
--     sectors 表仅 6 行 seed 与真实板块 code 不一致, 故不再 JOIN。
--     先以 'concept' 常量占位保证 /api/sectors/flow 返回全部真实板块。
DROP VIEW IF EXISTS v_sector_top;
CREATE VIEW v_sector_top AS
SELECT sector_code, sector_name, 'concept'::varchar AS sector_type, trade_date,
       change_pct, main_net_inflow, main_net_ratio, turnover_rate,
       RANK() OVER (PARTITION BY trade_date ORDER BY main_net_inflow DESC) AS rnk
FROM latest_sector;

SELECT '6stockPulse v5 migration complete.' AS status;
