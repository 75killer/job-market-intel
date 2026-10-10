-- ============================================================================
-- 文件：02_quality_distribution.sql
-- 业务问题：全市场公司的财务质量呈现什么分布？优秀公司有多少？
-- 技术要点：CASE WHEN 分箱、条件聚合一次性算出多档占比、累计分布
--
-- 分档标准（依据实测分位数设定，不是拍脑袋）：
--   2024 年报实测：90% 分位 = 15.55%，75% 分位 = 9.79%，25% 分位 = 0.40%
--   所以用 15% / 8% / 0% 作为优秀 / 良好 / 一般 / 亏损的边界，
--   既贴合 A 股实际分布，也符合"ROE 长期 15% 是优质公司门槛"的业务常识。
-- ============================================================================

WITH tiered AS (
    SELECT
        stock_code,
        industry,
        roe_w,
        net_margin_w,
        CASE
            WHEN roe_w IS NULL      THEN '未披露'
            WHEN roe_w < 0          THEN '亏损'
            WHEN roe_w < 8          THEN '一般'
            WHEN roe_w < 15         THEN '良好'
            ELSE                         '优秀'
        END AS 质量档
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
      AND is_st = 0
)
SELECT
    质量档,
    COUNT(*)                                                    AS 公司数,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM tiered), 2)   AS 占比,
    ROUND(MEDIAN(roe_w), 2)                                     AS 档内ROE中位数,
    ROUND(MIN(roe_w), 2)                                        AS 档内ROE最小,
    ROUND(MAX(roe_w), 2)                                        AS 档内ROE最大,
    ROUND(MEDIAN(net_margin_w), 2)                              AS 档内净利率中位数,
    COUNT(DISTINCT industry)                                    AS 覆盖行业数
FROM tiered
GROUP BY 质量档
ORDER BY
    -- 手工指定排序：不能用字母序，否则"优秀"会排在"一般"前面
    CASE 质量档
        WHEN '优秀' THEN 1 WHEN '良好' THEN 2
        WHEN '一般' THEN 3 WHEN '亏损' THEN 4 ELSE 5
    END;
