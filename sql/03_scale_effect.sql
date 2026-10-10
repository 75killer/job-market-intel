-- ============================================================================
-- 文件：03_scale_effect.sql
-- 业务问题：公司规模越大，盈利能力就越强吗？（"规模效应"到底存不存在）
-- 技术要点：CASE WHEN 区间分箱 + 聚合对比，用一个查询验证一个业务假设
--
-- 这是本项目最有"分析味"的一条 SQL：
-- 它不是在描述数据，而是在**检验一个假设**，并能得出可反驳的结论。
-- ============================================================================

WITH sized AS (
    SELECT
        stock_code,
        revenue_yi,
        roe_w,
        net_margin_w,
        gross_margin_w,
        CASE
            WHEN revenue_yi <  10   THEN '1-微型(<10亿)'
            WHEN revenue_yi <  50   THEN '2-小型(10-50亿)'
            WHEN revenue_yi < 200   THEN '3-中型(50-200亿)'
            WHEN revenue_yi < 1000  THEN '4-大型(200-1000亿)'
            ELSE                         '5-超大型(>1000亿)'
        END AS 规模档
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
      AND is_st = 0
      AND revenue_yi > 0           -- 排除营收为 0 或缺失的记录
)
SELECT
    规模档,
    COUNT(*)                                              AS 公司数,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM sized), 2) AS 公司占比,
    ROUND(MEDIAN(roe_w), 2)                               AS ROE中位数,
    ROUND(AVG(roe_w), 2)                                  AS ROE平均值,
    ROUND(MEDIAN(net_margin_w), 2)                        AS 净利率中位数,
    ROUND(MEDIAN(gross_margin_w), 2)                      AS 毛利率中位数,
    ROUND(100.0 * SUM(CASE WHEN roe_w < 0 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                          AS 亏损占比
FROM sized
GROUP BY 规模档
ORDER BY 规模档;
