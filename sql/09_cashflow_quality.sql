-- ============================================================================
-- 文件：09_cashflow_quality.sql
-- 业务问题：公司赚到的利润，是真金白银还是账面数字？
-- 技术要点：比率分析 + 分档统计 + 用现金流质量识别"纸面利润"
--
-- 【核心概念：利润含金量】
--   净利润是会计口径，可能包含大量应收账款（卖出去了但钱没收到）。
--   经营现金流才是真金白银。
--
--   每股经营现金流 / 每股收益（ocf_to_eps）：
--       > 1.2  → 利润含金量高，赚到的是现金
--       0.8~1.2→ 基本匹配，属于正常
--       < 0.8  → 利润质量存疑，可能有大量应收款
--       < 0    → 经营现金流为负，即使账面盈利也要警惕
--
-- 这是财务分析里非常经典的一个指标，能讲清楚说明你懂业务而不只是会写 SQL。
-- ============================================================================

WITH cashflow_data AS (
    SELECT
        stock_code,
        stock_name,
        industry,
        roe_w,
        eps,
        ocf_per_share,
        net_profit_yi,
        net_margin_w,
        -- NULLIF 防除零：EPS 为 0 时不计算比率，返回 NULL
        ROUND(ocf_per_share / NULLIF(eps, 0), 3)  AS 现金利润比,
        CASE
            WHEN ocf_per_share IS NULL OR eps IS NULL OR eps <= 0 THEN '5-不适用'
            WHEN ocf_per_share < 0                                THEN '4-现金流为负'
            WHEN ocf_per_share / NULLIF(eps, 0) < 0.8             THEN '3-含金量低'
            WHEN ocf_per_share / NULLIF(eps, 0) < 1.2             THEN '2-含金量中'
            ELSE                                                       '1-含金量高'
        END AS 现金流档
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
      AND is_st = 0
      AND net_profit_yi > 0        -- 只看盈利公司：亏损公司的 EPS 为负，比率无意义
)
SELECT
    现金流档,
    COUNT(*)                                              AS 公司数,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM cashflow_data), 2)
                                                          AS 占比,
    ROUND(MEDIAN(现金利润比), 3)                          AS 现金利润比中位数,
    ROUND(MEDIAN(roe_w), 2)                               AS ROE中位数,
    ROUND(MEDIAN(net_margin_w), 2)                        AS 净利率中位数,
    ROUND(MEDIAN(net_profit_yi), 2)                       AS 净利润中位数亿,
    COUNT(DISTINCT industry)                              AS 覆盖行业数
FROM cashflow_data
GROUP BY 现金流档
ORDER BY 现金流档;
