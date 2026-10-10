-- ============================================================================
-- 文件：05_dividend_quality.sql
-- 业务问题：高股息率的公司，财务质量真的更好吗？
-- 技术要点：分组对比 + 相关性分析，检验一个投资界常见假设
--
-- 投资界常说"高股息 = 高质量"，但这句话需要用数据验证：
--   如果高股息组的 ROE / 净利率确实更高 → 假设成立
--   如果不相上下甚至更低           → 说明高股息更多来自"股价低"而非"公司好"
-- 这类"检验常识"的分析最能体现分析思维，面试时讲这个比讲语法加分得多。
-- ============================================================================

WITH dividend_groups AS (
    SELECT
        stock_code,
        industry,
        dividend_yield_w,
        roe_w,
        net_margin_w,
        revenue_yi,
        growth_score,
        CASE
            -- 股息率分档：参照银行理财与国债收益率的实际水平
            WHEN dividend_yield_w IS NULL OR dividend_yield_w <= 0 THEN '0-不分红'
            WHEN dividend_yield_w < 1                              THEN '1-低(<1%)'
            WHEN dividend_yield_w < 3                              THEN '2-中(1%-3%)'
            WHEN dividend_yield_w < 5                              THEN '3-高(3%-5%)'
            ELSE                                                        '4-很高(>5%)'
        END AS 股息档
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
      AND is_st = 0
)
SELECT
    股息档,
    COUNT(*)                                                   AS 公司数,
    ROUND(MEDIAN(roe_w), 2)                                    AS ROE中位数,
    ROUND(MEDIAN(net_margin_w), 2)                             AS 净利率中位数,
    ROUND(MEDIAN(revenue_yi), 2)                               AS 营收中位数亿,
    ROUND(MEDIAN(growth_score), 2)                             AS 成长性中位数,
    ROUND(100.0 * SUM(CASE WHEN roe_w < 0 THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                       AS 亏损占比,
    ROUND(100.0 * SUM(CASE WHEN roe_w >= 15 THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                       AS 优秀公司占比
FROM dividend_groups
GROUP BY 股息档
ORDER BY 股息档;
