-- ============================================================================
-- 文件：07_market_board_analysis.sql
-- 业务问题：主板、创业板、科创板、北交所的公司质量有什么区别？
-- 技术要点：条件聚合一次性算出多个维度的占比 + 交叉分析
--
-- 业务背景（这决定了分析的价值）：
--   主板     → 成熟大公司为主
--   创业板   → 成长型创新企业（代码 300 开头）
--   科创板   → 硬科技企业，允许未盈利上市（代码 688 开头）
--   北交所   → 专精特新中小企业（代码 8 开头）
--   风险警示 → ST/*ST，退市风险
-- 如果"科创板亏损占比明显更高"，那不是数据错误，
-- 而是科创板允许未盈利企业上市的制度设计结果——能讲清这一点就是业务理解。
-- ============================================================================

SELECT
    market                                              AS 上市板块,
    COUNT(*)                                            AS 公司数,
    ROUND(100.0 * COUNT(*) /
          (SELECT COUNT(*) FROM dwd_finance_performance
           WHERE is_analyzable = 1 AND report_date = '{report_date}'), 2)
                                                        AS 占全市场比例,
    ROUND(MEDIAN(roe_w), 2)                             AS ROE中位数,
    ROUND(MEDIAN(net_margin_w), 2)                      AS 净利率中位数,
    ROUND(MEDIAN(gross_margin_w), 2)                    AS 毛利率中位数,
    ROUND(MEDIAN(revenue_yi), 2)                        AS 营收中位数亿,
    ROUND(MEDIAN(growth_score), 2)                      AS 成长性中位数,

    -- ---------- 质量分布（条件聚合）----------
    ROUND(100.0 * SUM(CASE WHEN roe_w >= 15 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                        AS 优秀占比,
    ROUND(100.0 * SUM(CASE WHEN roe_w >= 8 AND roe_w < 15 THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                AS 良好占比,
    ROUND(100.0 * SUM(CASE WHEN roe_w >= 0 AND roe_w < 8 THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                AS 一般占比,
    ROUND(100.0 * SUM(CASE WHEN roe_w < 0 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                        AS 亏损占比,

    -- ---------- 分红与现金流 ----------
    ROUND(100.0 * SUM(CASE WHEN dividend_yield > 0 THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                AS 分红公司占比,
    ROUND(MEDIAN(dividend_yield_w), 2)                  AS 股息率中位数

FROM dwd_finance_performance
WHERE is_analyzable = 1
  AND report_date = '{report_date}'
GROUP BY market
ORDER BY ROE中位数 DESC;
