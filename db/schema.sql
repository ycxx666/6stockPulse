-- ═══════════════════════════════════════════════════════════════════
-- 6stockPulse · PostgreSQL Schema (实时行情 + 多维资讯)
-- ═══════════════════════════════════════════════════════════════════
-- 设计目标:
--   1. 支持 A 股全市场 (沪深北) 个股 + 板块
--   2. 时间序列: stock_prices / fund_flow 用 BRIN + 分区预留
--   3. 资讯: 个股新闻 (stock_news) + 个股公告 (stock_announcements)
--   4. 审计: 每次爬虫写入均记录到 crawl_log
-- ═══════════════════════════════════════════════════════════════════

-- 扩展 (TimescaleDB 可选, 没装也能跑)
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- 模糊搜索
CREATE EXTENSION IF NOT EXISTS btree_gin; -- GIN 复合索引

-- ────────────────────────────────────────────────────────────
-- 0. ENUM 类型
-- ────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE market_type AS ENUM ('SH', 'SZ', 'BJ');
    CREATE TYPE flow_type   AS ENUM ('main', 'super_large', 'large', 'mid', 'small');
    CREATE TYPE sentiment_t AS ENUM ('bullish', 'bearish', 'neutral');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ────────────────────────────────────────────────────────────
-- 1. 股票基础信息 (stocks) — 静态维度表
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stocks (
    stock_code      VARCHAR(10)  PRIMARY KEY,          -- 600519 / 000001 / 830799
    stock_name      VARCHAR(50)  NOT NULL,
    market          market_type  NOT NULL,             -- SH/SZ/BJ
    industry        VARCHAR(50),                       -- 申万行业
    sector_code     VARCHAR(20),                       -- 板块代码
    list_date       DATE,                              -- 上市日期
    total_shares    BIGINT,                            -- 总股本
    circ_shares     BIGINT,                            -- 流通股本
    is_active       BOOLEAN     DEFAULT TRUE,          -- 是否活跃
    extra           JSONB       DEFAULT '{}'::jsonb,   -- 扩展字段
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stocks_market   ON stocks(market) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_stocks_industry ON stocks(industry) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_stocks_name_trgm ON stocks USING gin (stock_name gin_trgm_ops);

-- ────────────────────────────────────────────────────────────
-- 2. 板块信息 (sectors) — 行业 + 概念双维度
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sectors (
    sector_code     VARCHAR(20)  PRIMARY KEY,
    sector_name     VARCHAR(50)  NOT NULL,
    sector_type     VARCHAR(20)  NOT NULL,            -- 'industry' / 'concept'
    parent_code     VARCHAR(20),                       -- 上级板块
    is_active       BOOLEAN     DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sectors_type ON sectors(sector_type) WHERE is_active;

-- ────────────────────────────────────────────────────────────
-- 3. 实时行情 (stock_prices) — 时间序列
--    写入频率: 1-3 秒/次 (实时模式) 或 每日收盘后 (历史模式)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_prices (
    id              BIGSERIAL,
    stock_code      VARCHAR(10)  NOT NULL,
    trade_date      DATE         NOT NULL,
    trade_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    open            NUMERIC(10,3),
    high            NUMERIC(10,3),
    low             NUMERIC(10,3),
    close           NUMERIC(10,3),
    volume          BIGINT,                            -- 成交量 (手)
    amount          NUMERIC(18,2),                     -- 成交额 (元)
    change_pct      NUMERIC(8,4),                      -- 涨跌幅 %
    change_amt      NUMERIC(10,3),                     -- 涨跌额
    turnover_rate   NUMERIC(8,4),                      -- 换手率 %
    pe_ratio        NUMERIC(10,3),                     -- 动态市盈率
    pb_ratio        NUMERIC(10,3),                     -- 市净率
    market_cap      NUMERIC(18,2),                     -- 总市值 (亿)
    circ_market_cap NUMERIC(18,2),                     -- 流通市值 (亿)
    PRIMARY KEY (trade_date, stock_code, trade_time)
) PARTITION BY RANGE (trade_date);

-- 当日分区 + 未来 7 天预创建 (爬虫启动时确保)
CREATE TABLE IF NOT EXISTS stock_prices_default PARTITION OF stock_prices DEFAULT;

-- 高频查询索引
CREATE INDEX IF NOT EXISTS idx_sp_code_time ON stock_prices (stock_code, trade_time DESC);
CREATE INDEX IF NOT EXISTS idx_sp_time       ON stock_prices (trade_time DESC);

-- ────────────────────────────────────────────────────────────
-- 4. 资金流向 (fund_flow) — 时间序列
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fund_flow (
    id              BIGSERIAL,
    stock_code      VARCHAR(10)  NOT NULL,
    trade_date      DATE         NOT NULL,
    trade_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    main_net_inflow   NUMERIC(18,2),    -- 主力 (超大+大)
    main_net_ratio    NUMERIC(8,4),
    super_large_net   NUMERIC(18,2),    -- 超大单
    super_large_ratio NUMERIC(8,4),
    large_net_inflow  NUMERIC(18,2),    -- 大单
    large_ratio       NUMERIC(8,4),
    mid_net_inflow    NUMERIC(18,2),    -- 中单
    mid_ratio         NUMERIC(8,4),
    small_net_inflow  NUMERIC(18,2),    -- 小单
    small_ratio       NUMERIC(8,4),
    sentiment         sentiment_t,      -- 情绪标签
    PRIMARY KEY (trade_date, stock_code, trade_time)
) PARTITION BY RANGE (trade_date);
CREATE TABLE IF NOT EXISTS fund_flow_default PARTITION OF fund_flow DEFAULT;

CREATE INDEX IF NOT EXISTS idx_ff_code_time ON fund_flow (stock_code, trade_time DESC);
-- PG16: 分区表部分索引谓词必须 IMMUTABLE，CURRENT_DATE(stable) 不允许 → 去掉谓词建全量索引
CREATE INDEX IF NOT EXISTS idx_ff_main       ON fund_flow (main_net_inflow DESC);

-- ────────────────────────────────────────────────────────────
-- 5. 板块资金流向 (sector_fund_flow) — 当日 + 历史
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sector_fund_flow (
    id              BIGSERIAL,
    sector_code     VARCHAR(20)  NOT NULL,
    sector_name     VARCHAR(50)  NOT NULL,
    trade_date      DATE         NOT NULL,
    trade_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    change_pct      NUMERIC(8,4),
    main_net_inflow NUMERIC(18,2),
    main_net_ratio  NUMERIC(8,4),
    turnover_rate   NUMERIC(8,4),
    super_large_net NUMERIC(18,2),
    large_net       NUMERIC(18,2),
    mid_net         NUMERIC(18,2),
    small_net       NUMERIC(18,2),
    PRIMARY KEY (trade_date, sector_code, trade_time)
) PARTITION BY RANGE (trade_date);
CREATE TABLE IF NOT EXISTS sector_fund_flow_default PARTITION OF sector_fund_flow DEFAULT;

CREATE INDEX IF NOT EXISTS idx_sff_code_time ON sector_fund_flow (sector_code, trade_time DESC);

-- ────────────────────────────────────────────────────────────
-- 6. 7×24 财经快讯 (news) — 全市场滚动新闻
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS news (
    news_id         VARCHAR(50)  PRIMARY KEY,         -- 新浪 ID
    title           TEXT         NOT NULL,
    content         TEXT,
    summary         TEXT,                              -- NLP 摘要
    publish_time    TIMESTAMPTZ  NOT NULL,
    source          VARCHAR(50),
    source_url      TEXT,
    category        VARCHAR(30),                       -- macro/policy/company/global
    stocks          TEXT[],                            -- 关联股票 (数组)
    keywords        TEXT[],                            -- 关键词数组
    sentiment       NUMERIC(5,4),                      -- 情感分数 -1~+1
    importance      SMALLINT     DEFAULT 0,            -- 重要度 0-5
    crawl_time      TIMESTAMPTZ  DEFAULT NOW(),
    is_processed    BOOLEAN      DEFAULT FALSE         -- 是否已 NLP 处理
);
CREATE INDEX IF NOT EXISTS idx_news_publish ON news (publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_news_cat      ON news (category, publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_news_stocks   ON news USING gin (stocks);
CREATE INDEX IF NOT EXISTS idx_news_kw       ON news USING gin (keywords);

-- ────────────────────────────────────────────────────────────
-- 7. 个股新闻 (stock_news) — 关联到具体股票
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_news (
    id              BIGSERIAL    PRIMARY KEY,
    stock_code      VARCHAR(10)  NOT NULL,
    news_id         VARCHAR(50)  NOT NULL,            -- 关联 news.news_id
    title           TEXT         NOT NULL,
    content         TEXT,
    publish_time    TIMESTAMPTZ  NOT NULL,
    source          VARCHAR(50),
    source_url      TEXT,
    category        VARCHAR(30),
    sentiment       NUMERIC(5,4),
    read_count      INTEGER      DEFAULT 0,
    crawl_time      TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (stock_code, news_id)
);
CREATE INDEX IF NOT EXISTS idx_sn_code_time ON stock_news (stock_code, publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_sn_publish   ON stock_news (publish_time DESC);

-- ────────────────────────────────────────────────────────────
-- 8. 个股公告 (stock_announcements)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_announcements (
    id              BIGSERIAL    PRIMARY KEY,
    stock_code      VARCHAR(10)  NOT NULL,
    ann_id          VARCHAR(50)  NOT NULL,            -- 公告 ID
    title           TEXT         NOT NULL,
    content         TEXT,
    publish_time    TIMESTAMPTZ  NOT NULL,
    ann_type        VARCHAR(50)  NOT NULL,            -- 临时公告/定期报告/...
    pdf_url         TEXT,
    source_url      TEXT,
    crawl_time      TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (stock_code, ann_id)
);
CREATE INDEX IF NOT EXISTS idx_sa_code_time ON stock_announcements (stock_code, publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_sa_type      ON stock_announcements (ann_type);
CREATE INDEX IF NOT EXISTS idx_sa_publish   ON stock_announcements (publish_time DESC);

-- ────────────────────────────────────────────────────────────
-- 9. 爬虫运行日志 (crawl_log) — 审计
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crawl_log (
    id              BIGSERIAL    PRIMARY KEY,
    crawler_name    VARCHAR(50)  NOT NULL,            -- eastmoney_fund / sina_news / ...
    started_at      TIMESTAMPTZ  NOT NULL,
    finished_at     TIMESTAMPTZ,
    success_count   INTEGER      DEFAULT 0,
    skip_count      INTEGER      DEFAULT 0,
    fail_count      INTEGER      DEFAULT 0,
    error_msg       TEXT,
    meta            JSONB        DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_clog_name_time ON crawl_log (crawler_name, started_at DESC);

-- ────────────────────────────────────────────────────────────
-- 10. 实时行情最新快照 (latest_quotes) — 物化视图加速
--    实时模式下由 WebSocket 推送更新
-- ────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS latest_quotes AS
SELECT DISTINCT ON (stock_code)
    stock_code, trade_date, trade_time, open, high, low, close,
    volume, amount, change_pct, change_amt, turnover_rate
FROM stock_prices
ORDER BY stock_code, trade_time DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_lq_code ON latest_quotes (stock_code);

-- ────────────────────────────────────────────────────────────
-- 11. 板块最新行情 (latest_sector)
-- ────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS latest_sector AS
SELECT DISTINCT ON (sector_code)
    sector_code, sector_name, trade_date, trade_time,
    change_pct, main_net_inflow, main_net_ratio, turnover_rate
FROM sector_fund_flow
ORDER BY sector_code, trade_time DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ls_code ON latest_sector (sector_code);

-- ────────────────────────────────────────────────────────────
-- 11.5. 全球指数 (global_indices) — VIX / 标普 / 纳指 / 道指 …
--    refresh_interval_seconds 控制 trade_time 的分钟桶粒度, 用于去重
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS global_indices (
    id            BIGSERIAL    PRIMARY KEY,
    index_code    VARCHAR(20)  NOT NULL,            -- 100.VIX / .DJI / .INX
    index_name    VARCHAR(50)  NOT NULL,            -- VIX 恐慌指数 / 道琼斯
    market        VARCHAR(10)  NOT NULL,            -- US / HK / EU / JP
    trade_date    DATE         NOT NULL,
    trade_time    TIMESTAMPTZ  NOT NULL,            -- 截断到 refresh_interval 边界
    open          NUMERIC(12,4),
    high          NUMERIC(12,4),
    low           NUMERIC(12,4),
    close         NUMERIC(12,4),
    prev_close    NUMERIC(12,4),
    change_amt    NUMERIC(12,4),
    change_pct    NUMERIC(8,4),
    source        VARCHAR(20)  DEFAULT 'eastmoney',
    extra         JSONB        DEFAULT '{}'::jsonb,
    crawl_time    TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (index_code, trade_date, trade_time)
);
CREATE INDEX IF NOT EXISTS idx_gi_code_time ON global_indices (index_code, trade_time DESC);
CREATE INDEX IF NOT EXISTS idx_gi_market     ON global_indices (market, trade_time DESC);
CREATE INDEX IF NOT EXISTS idx_gi_publish    ON global_indices (trade_time DESC);

-- 物化视图: 各大指数最新值
CREATE MATERIALIZED VIEW IF NOT EXISTS latest_global_indices AS
SELECT DISTINCT ON (index_code)
    index_code, index_name, market, trade_date, trade_time,
    open, high, low, close, prev_close,
    change_amt, change_pct, source, crawl_time
FROM global_indices
ORDER BY index_code, trade_time DESC;
CREATE UNIQUE INDEX IF NOT EXISTS idx_lgi_code ON latest_global_indices (index_code);

-- ────────────────────────────────────────────────────────────
-- 12. 视图: 板块 TOP 排行
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_sector_top AS
SELECT sector_code, sector_name, trade_date, change_pct,
       main_net_inflow, main_net_ratio, turnover_rate,
       RANK() OVER (PARTITION BY trade_date ORDER BY main_net_inflow DESC) AS rnk
FROM latest_sector;

-- ────────────────────────────────────────────────────────────
-- 13. 视图: 个股资金流入 TOP
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_stock_top_inflow AS
SELECT s.stock_code, s.stock_name, s.industry, s.market,
       lq.trade_time, lq.close, lq.change_pct, lq.turnover_rate,
       ff.main_net_inflow, ff.super_large_net, ff.large_net_inflow
FROM latest_quotes lq
JOIN stocks s ON s.stock_code = lq.stock_code
LEFT JOIN fund_flow ff ON ff.stock_code = lq.stock_code
                      AND ff.trade_date = lq.trade_date
                      AND ff.trade_time  = (
                          SELECT MAX(trade_time) FROM fund_flow
                          WHERE stock_code = lq.stock_code AND trade_date = lq.trade_date
                      )
WHERE s.is_active;

-- ────────────────────────────────────────────────────────────
-- 14. 触发器: 自动更新 stocks.updated_at
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION trg_touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_stocks_touch ON stocks;
CREATE TRIGGER trg_stocks_touch BEFORE UPDATE ON stocks
    FOR EACH ROW EXECUTE FUNCTION trg_touch_updated_at();

-- ────────────────────────────────────────────────────────────
-- 15. 初始化数据
-- ────────────────────────────────────────────────────────────
-- 常用股票基础信息 (供测试)
INSERT INTO stocks (stock_code, stock_name, market, industry) VALUES
    ('600519', '贵州茅台',  'SH', '白酒'),
    ('000001', '平安银行',  'SZ', '银行'),
    ('300750', '宁德时代',  'SZ', '电池'),
    ('601318', '中国平安',  'SH', '保险'),
    ('000858', '五粮液',    'SZ', '白酒'),
    ('600036', '招商银行',  'SH', '银行'),
    ('601012', '隆基绿能',  'SH', '光伏'),
    ('002415', '海康威视',  'SZ', '安防'),
    ('600276', '恒瑞医药',  'SH', '医药'),
    ('688981', '中芯国际',  'SH', '半导体')
ON CONFLICT (stock_code) DO NOTHING;

-- 板块示例
INSERT INTO sectors (sector_code, sector_name, sector_type) VALUES
    ('BK0001', '存储芯片',   'concept'),
    ('BK0002', '固态电池',   'concept'),
    ('BK0003', '人形机器人', 'concept'),
    ('BK0004', '先进封装',   'concept'),
    ('BK0005', 'CPO概念',    'concept'),
    ('BK0006', '通信设备',   'industry')
ON CONFLICT (sector_code) DO NOTHING;

-- 重点关注的全球指数
INSERT INTO global_indices (index_code, index_name, market, trade_date, trade_time, close, source) VALUES
    ('100.VIX',  'VIX 恐慌指数',     'US', CURRENT_DATE, NOW(), 0, 'eastmoney'),
    ('100.DJI',  '道琼斯工业指数',   'US', CURRENT_DATE, NOW(), 0, 'eastmoney'),
    ('100.INX',  '标普500指数',      'US', CURRENT_DATE, NOW(), 0, 'eastmoney'),
    ('100.IXIC', '纳斯达克综合指数', 'US', CURRENT_DATE, NOW(), 0, 'eastmoney'),
    ('100.HSI',  '恒生指数',         'HK', CURRENT_DATE, NOW(), 0, 'eastmoney'),
    ('100.N225', '日经225指数',      'JP', CURRENT_DATE, NOW(), 0, 'eastmoney')
ON CONFLICT (index_code, trade_date, trade_time) DO NOTHING;

-- ═══════════════════════════════════════════════════════════
-- 完成
-- ═══════════════════════════════════════════════════════════
SELECT '6stockPulse schema initialized.' AS status;
SELECT COUNT(*) AS stock_count FROM stocks;
SELECT COUNT(*) AS sector_count FROM sectors;
