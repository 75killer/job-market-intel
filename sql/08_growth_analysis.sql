-- ============================================================================
-- 文件：08_growth_analysis.sql
-- 业务问题：哪些行业在增长、哪些在下滑？（成长性 vs 盈利能力的四象限）
-- 技术要点：CTE + 多表 JOIN + 二维分象限
--
-- 【业务价值】把"盈利能力"和"成长性"两个维度交叉，得到经典四象限：
--       高增长 + 高盈利 = 明星行业
--       高增长 + 低盈利 = 烧钱扩张期（风险高但有想象力）
--       低增长 + 高盈利 = 现金牛（稳定但缺增长）
--       低增长 + 低盈利 = 衰退行业（谨慎）
-- 这种"二维分类"是麦肯锡矩阵的简化版，讲出来很显专业度。
-- ============================================================================

WITH growth_data AS (
    SELECT
        industry,
        stock_code,
        revenue_yoy,
        profit_yoy,
        roe_w,
        net_margin_w,
        revenue_yi
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
      AND is_st = 0
      AND revenue_yoy IS NOT NULL
      AND roe_w IS NOT NULL
),
industry_level AS (
    SELECT
        industry                                        AS 行业,
        COUNT(DISTINCT stock_code)                      AS 公司数,
        ROUND(MEDIAN(revenue_yoy), 2)                   AS 营收增速中位数,
        ROUND(MEDIAN(profit_yoy), 2)                    AS 净利增速中位数,
        ROUND(MEDIAN(roe_w), 2)                         AS ROE中位数,
        ROUND(MEDIAN(net_margin_w), 2)                  AS 净利率中位数,
        ROUND(MEDIAN(revenue_yi), 2)                    AS 营收中位数亿,
        -- 增长公司占比：比"增速中位数"更抗极端值
        ROUND(100.0 * SUM(CASE WHEN revenue_yoy > 0 THEN 1 ELSE 0 END)
              / COUNT(*), 2)                            AS 营收正增长占比,
        ROUND(100.0 * SUM(CASE WHEN profit_yoy > 0 THEN 1 ELSE 0 END)
              / COUNT(*), 2)                            AS 净利正增长占比
    FROM growth_data
    GROUP BY industry
    HAVING COUNT(DISTINCT stock_code) >= 8
)
SELECT
    行业,
    公司数,
    营收增速中位数,
    净利增速中位数,
    ROE中位数,
    净利率中位数,
    营收中位数亿,
    营收正增长占比,
    净利正增长占比,
    -- 二维分象限：用全市场中位数作为分界线（此处以 3% 营收增速、5% ROE 为界）
    CASE
        WHEN 营收增速中位数 >= 3 AND ROE中位数 >= 5 THEN '① 明星(高增长+高盈利)'
        WHEN 营收增速中位数 >= 3 AND ROE中位数 <  5 THEN '② 扩张(高增长+低盈利)'
        WHEN 营收增速中位数 <  3 AND ROE中位数 >= 5 THEN '③ 现金牛(低增长+高盈利)'
        ELSE                                             '④ 承压(低增长+低盈利)'
    END                                             AS 象限,
    -- 综合评分：盈利能力与成长性各占 50% 权重（权重可调，这里只做示例）
    ROUND(0.5 * ROE中位数 + 0.5 * 营收增速中位数, 2) AS 综合评分
FROM industry_level
ORDER BY 综合评分 DESC;
