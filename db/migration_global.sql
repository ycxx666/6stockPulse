-- ═══════════════════════════════════════════════════════════════
-- 6stockPulse · Global Market Intelligence Layer
-- ═══════════════════════════════════════════════════════════════
-- 新增 4 张表：全球行情 / 宏观指标 / 情绪 / 事件

-- 1. 全球市场行情（统一股票/指数/ETF/商品/加密/外汇）
CREATE TABLE IF NOT EXISTS global_market_quote (
    id            BIGSERIAL    PRIMARY KEY,
    symbol        VARCHAR(32)  NOT NULL,
    name          VARCHAR(100),
    market        VARCHAR(32)  NOT NULL,            -- NASDAQ/NYSE/SSE/HKEX/CRYPTO/COMM
    asset_type    VARCHAR(20)  NOT NULL,            -- stock/index/etf/commodity/crypto/forex
    price         NUMERIC(18,6),
    change_pct    NUMERIC(10,4),
    change_amt    NUMERIC(18,6),
    volume        BIGINT,
    prev_close    NUMERIC(18,6),
    high          NUMERIC(18,6),
    low           NUMERIC(18,6),
    open          NUMERIC(18,6),
    currency      VARCHAR(10)  DEFAULT 'USD',
    sector        VARCHAR(50),
    source        VARCHAR(20)  DEFAULT 'yahoo',     -- yahoo/finnhub/coingecko
    quote_time    TIMESTAMPTZ  NOT NULL,
    is_stale      BOOLEAN      DEFAULT FALSE,
    created_at    TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (symbol, quote_time)
);
CREATE INDEX IF NOT EXISTS idx_gmq_symbol ON global_market_quote (symbol, quote_time DESC);
CREATE INDEX IF NOT EXISTS idx_gmq_type   ON global_market_quote (asset_type, quote_time DESC);
CREATE INDEX IF NOT EXISTS idx_gmq_market ON global_market_quote (market, quote_time DESC);

-- 物化视图：全球最新行情快照
CREATE MATERIALIZED VIEW IF NOT EXISTS latest_global_market AS
SELECT DISTINCT ON (symbol)
    symbol, name, market, asset_type, price, change_pct, change_amt,
    volume, prev_close, currency, sector, source, quote_time, is_stale
FROM global_market_quote
ORDER BY symbol, quote_time DESC;
CREATE UNIQUE INDEX IF NOT EXISTS idx_lgm_symbol ON latest_global_market (symbol);


-- 2. 宏观经济指标（FRED/World Bank 等）
CREATE TABLE IF NOT EXISTS global_macro_indicator (
    id            BIGSERIAL    PRIMARY KEY,
    indicator_id  VARCHAR(32)  NOT NULL,            -- DFF / CPIAUCSL / GDP ...
    name          VARCHAR(200) NOT NULL,
    value         NUMERIC(18,6),
    unit          VARCHAR(50),
    frequency     VARCHAR(20),                       -- daily/monthly/quarterly
    source        VARCHAR(20)  DEFAULT 'fred',
    ref_date      DATE         NOT NULL,             -- 数据对应的日期
    created_at    TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (indicator_id, ref_date)
);
CREATE INDEX IF NOT EXISTS idx_gmi_indicator ON global_macro_indicator (indicator_id, ref_date DESC);


-- 3. 全球情绪（恐惧贪婪 / 社交情绪 / 风险评分）
CREATE TABLE IF NOT EXISTS global_sentiment (
    id            BIGSERIAL    PRIMARY KEY,
    source        VARCHAR(32)  NOT NULL,             -- fear_greed / reddit / twitter / risk_engine
    score         NUMERIC(8,4) NOT NULL,             -- 0-100 or -1 to +1
    label         VARCHAR(20),                        -- extreme_fear / fear / neutral / greed / extreme_greed
    factors       JSONB        DEFAULT '{}'::jsonb,  -- 细分因子
    snapshot_time TIMESTAMPTZ  NOT NULL,
    created_at    TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gs_source ON global_sentiment (source, snapshot_time DESC);


-- 4. 全球事件（地缘 / 宏观日历 / 财报 / 冲突）
CREATE TABLE IF NOT EXISTS global_event (
    id            BIGSERIAL    PRIMARY KEY,
    event_type    VARCHAR(32)  NOT NULL,             -- geopolitical / macro_calendar / earnings / conflict
    title         TEXT         NOT NULL,
    description   TEXT,
    country       VARCHAR(10),                        -- ISO-3166 alpha-2
    severity      SMALLINT     DEFAULT 0,             -- 0-100
    affected_assets TEXT[],                           -- ['oil','gold','shipping']
    risk_impact   SMALLINT     DEFAULT 0,             -- ± impact on risk score
    event_time    TIMESTAMPTZ  NOT NULL,
    source        VARCHAR(32)  DEFAULT 'acled',
    created_at    TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ge_type    ON global_event (event_type, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_ge_country ON global_event (country, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_ge_assets  ON global_event USING gin (affected_assets);
