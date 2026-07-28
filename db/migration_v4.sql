-- ═══════════════════════════════════════════════════════════════════
-- 6stockPulse v4 · 数据库迁移 (Phase 1)
-- ===================================================================
-- 新增: market_quote_snapshot, market_breadth, anomaly_event,
--       sentiment_snapshot, signal_snapshot
-- 运行: psql -U davi60 -h /tmp -d stockpulse -f db/migration_v4.sql
-- ═══════════════════════════════════════════════════════════════════

-- ────────────────────────────────────────────────────────────
-- 表 1: 跨市场最新行情快照
--   覆盖: 指数 (A股/美股/港股/日股/欧股)
--         商品 (黄金/白银/原油/天然气/铜)
--         外汇 (EURUSD/USDJPY/USDCNH)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_quote_snapshot (
    symbol        VARCHAR(30)  PRIMARY KEY,               -- 统一代码: 000001.SH / .DJI / GC.F / EURUSD.F
    asset_type    VARCHAR(20)  NOT NULL DEFAULT 'index',  -- index / commodity / forex
    market        VARCHAR(10)  NOT NULL,                  -- SH / SZ / US / HK / JP / EU / COMM / FX
    name          VARCHAR(50)  NOT NULL,
    price         NUMERIC(14,4),
    prev_close    NUMERIC(14,4),
    change        NUMERIC(14,4),
    change_pct    NUMERIC(8,4),
    volume        BIGINT,
    amount        NUMERIC(18,2),
    quote_time    TIMESTAMPTZ,
    source        VARCHAR(20)  DEFAULT 'eastmoney',
    is_stale      BOOLEAN      DEFAULT FALSE,
    quality_score NUMERIC(5,4) DEFAULT 1.0,
    updated_at    TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mqs_type   ON market_quote_snapshot (asset_type, market);
CREATE INDEX IF NOT EXISTS idx_mqs_stale  ON market_quote_snapshot (is_stale, updated_at);

-- ────────────────────────────────────────────────────────────
-- 表 2: 市场宽度快照
--   每分钟记录全市场 up/down/flat + 涨跌停 + 情绪分
--   snapshot_time 作为业务唯一键去重
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_breadth (
    id              BIGSERIAL    PRIMARY KEY,
    snapshot_time   TIMESTAMPTZ  NOT NULL,
    up_count        INTEGER      DEFAULT 0,
    down_count      INTEGER      DEFAULT 0,
    flat_count      INTEGER      DEFAULT 0,
    limit_up        INTEGER      DEFAULT 0,
    limit_down      INTEGER      DEFAULT 0,
    big_up          INTEGER      DEFAULT 0,   -- 涨幅 > 5%
    big_down        INTEGER      DEFAULT 0,   -- 跌幅 > 5%
    total           INTEGER      DEFAULT 0,
    sentiment_score INTEGER      DEFAULT 50,
    source          VARCHAR(20)  DEFAULT 'computed',
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mb_time ON market_breadth (snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_mb_created ON market_breadth (created_at DESC);

-- ────────────────────────────────────────────────────────────
-- 表 3: 异动事件
--   检测急涨/急跌/放量/缩量/突破/涨停打开/板块共振
--   状态: active → expired (过期的由定时任务标记)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS anomaly_event (
    id              BIGSERIAL     PRIMARY KEY,
    symbol          VARCHAR(20)   NOT NULL,
    stock_name      VARCHAR(50),
    event_type      VARCHAR(30)   NOT NULL,   -- surge / drop / volume_break / limit_up_open / sector_resonance
    direction       VARCHAR(10),              -- up / down
    window_seconds  INTEGER       DEFAULT 60, -- 检测窗口 (60/180/300 秒)
    change_pct      NUMERIC(8,4),
    volume_ratio    NUMERIC(10,4),
    z_score         NUMERIC(8,4),
    anomaly_score   NUMERIC(5,4),             -- 综合异常分 0-100
    mapped_sectors  TEXT[],                   -- 关联板块
    confidence      NUMERIC(5,4)  DEFAULT 0.5,
    status          VARCHAR(10)   DEFAULT 'active',  -- active / expired
    detected_at     TIMESTAMPTZ   DEFAULT NOW(),
    expired_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ae_status   ON anomaly_event (status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_ae_symbol   ON anomaly_event (symbol, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_ae_type     ON anomaly_event (event_type, detected_at DESC);

-- ────────────────────────────────────────────────────────────
-- 表 4: 情绪快照
--   scope = global / ashare
--   factors 用 JSONB 存每个因子的原始值 + 标准化值 + 权重
--   便于 AI 摘要引用证据
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sentiment_snapshot (
    id              BIGSERIAL    PRIMARY KEY,
    scope           VARCHAR(20)  NOT NULL,           -- global / ashare
    score           INTEGER      NOT NULL,           -- 0-100
    label           VARCHAR(30),                     -- 极弱 / 偏弱 / 中性 / 偏强 / 过热
    long_count      INTEGER      DEFAULT 0,          -- 多头数 (全球=上涨市场数)
    neutral_count   INTEGER      DEFAULT 0,
    short_count     INTEGER      DEFAULT 0,
    factors         JSONB        DEFAULT '{}'::jsonb, -- 因子明细: {factor_name: {raw, normalized, weight}}
    model_version   VARCHAR(20)  DEFAULT 'rule-v1',
    snapshot_time   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ss_scope_time ON sentiment_snapshot (scope, snapshot_time DESC);

-- ────────────────────────────────────────────────────────────
-- 表 5: 信号快照
--   结构化投资信号 (机会/风险)
--   evidence 是 JSONB 数组: [{type, value, source, ts}]
--   每条信号最多 5 条证据
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signal_snapshot (
    id              BIGSERIAL     PRIMARY KEY,
    signal_type     VARCHAR(30)   NOT NULL,          -- opportunity / risk
    scope           VARCHAR(20)   DEFAULT 'ashare',  -- ashare / global / sector
    title           TEXT          NOT NULL,
    summary         TEXT,                            -- 一句话摘要
    evidence        JSONB         DEFAULT '[]'::jsonb,
    direction       VARCHAR(10),                     -- bullish / bearish / neutral
    confidence      NUMERIC(5,4)  DEFAULT 0.5,
    severity        VARCHAR(10)   DEFAULT 'info',    -- info / warning / critical
    model_version   VARCHAR(20)   DEFAULT 'rule-v1',
    generated_at    TIMESTAMPTZ   DEFAULT NOW(),
    expires_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sis_type ON signal_snapshot (signal_type, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sis_scope ON signal_snapshot (scope, generated_at DESC);

-- ────────────────────────────────────────────────────────────
-- 完成
-- ────────────────────────────────────────────────────────────
SELECT '6stockPulse v4 migration complete.' AS status;
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN ('market_quote_snapshot','market_breadth','anomaly_event','sentiment_snapshot','signal_snapshot')
ORDER BY table_name;
