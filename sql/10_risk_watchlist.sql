-- ============================================================================
-- 文件：10_risk_watchlist.sql
-- 业务问题：哪些公司需要重点关注风险？（异常财务信号筛查）
-- 技术要点：多条件 OR 组合筛选 + 风险评分 + 排序
--
-- 【业务价值】这是"规则引擎"式的风险筛查，是风控场景最常见的做法：
--   把多个风险信号加权打分，超过阈值就进观察名单。
--   比单一指标更能避免误判（一家公司 ROE 低，可能只是行业周期问题；
--   但如果同时满足"亏损 + 现金流为负 + 高负债"，风险就明显更高）。
--
-- 注意：本查询只做技术演示，不构成任何投资建议。
-- ============================================================================

WITH risk_signals AS (
    SELECT
        stock_code,
        stock_name,
        industry,
        market,
        roe_w,
        net_margin_w,
        revenue_yi,
        net_profit_yi,
        revenue_yoy,
        ocf_per_share,
        eps,

        -- ---------- 逐项风险信号（每个信号记 1 分）----------
        CASE WHEN net_profit_yi < 0            THEN 1 ELSE 0 END AS 信号_亏损,
        CASE WHEN ocf_per_share < 0            THEN 1 ELSE 0 END AS 信号_现金流为负,
        CASE WHEN revenue_yoy < -30            THEN 1 ELSE 0 END AS 信号_营收大幅下滑,
        CASE WHEN net_margin_w < -20           THEN 1 ELSE 0 END AS 信号_严重亏损,
        CASE WHEN roe_w < -20                  THEN 1 ELSE 0 END AS 信号_ROE严重为负,
        CASE WHEN revenue_yi < 1               THEN 1 ELSE 0 END AS 信号_营收极小,
        CASE WHEN dividend_yield IS NULL OR dividend_yield <= 0
                                               THEN 1 ELSE 0 END AS 信号_不分红
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
),
scored AS (
    SELECT
        *,
        (信号_亏损 + 信号_现金流为负 + 信号_营收大幅下滑 + 信号_严重亏损
         + 信号_ROE严重为负 + 信号_营收极小 + 信号_不分红) AS 风险分
    FROM risk_signals
)
SELECT
    stock_code      AS 股票代码,
    stock_name      AS 股票简称,
    industry        AS 行业,
    market          AS 板块,
    风险分,
    ROUND(roe_w, 2)          AS ROE,
    ROUND(net_margin_w, 2)   AS 净利率,
    ROUND(revenue_yi, 2)     AS 营收亿,
    ROUND(net_profit_yi, 2)  AS 净利润亿,
    ROUND(revenue_yoy, 2)    AS 营收同比,
    ROUND(ocf_per_share, 2)  AS 每股经营现金流,
    -- 把命中的风险信号拼成可读文本，方便人工复核
    TRIM(
        CASE WHEN 信号_亏损 = 1         THEN '亏损 ' ELSE '' END ||
        CASE WHEN 信号_现金流为负 = 1   THEN '现金流为负 ' ELSE '' END ||
        CASE WHEN 信号_营收大幅下滑 = 1 THEN '营收大幅下滑 ' ELSE '' END ||
        CASE WHEN 信号_严重亏损 = 1     THEN '严重亏损 ' ELSE '' END ||
        CASE WHEN 信号_ROE严重为负 = 1  THEN 'ROE严重为负 ' ELSE '' END ||
        CASE WHEN 信号_营收极小 = 1     THEN '营收极小 ' ELSE '' END ||
        CASE WHEN 信号_不分红 = 1       THEN '不分红' ELSE '' END
    )                       AS 命中风险信号
FROM scored
WHERE 风险分 >= 4          -- 命中 4 项及以上才进观察名单，避免名单过长
ORDER BY 风险分 DESC, net_profit_yi ASC
LIMIT 100;
