-- ============================================================================
-- 文件：01_industry_ranking.sql
-- 业务问题：哪些行业的盈利质量最高？行业之间的差距有多大？
-- 技术要点：窗口函数 RANK()、分位数、条件聚合、NULLIF 防除零
--
-- 【口径说明 · 必读】
-- ROE = 净利润 / 净资产。半年报只累计半年利润，分母却是期末净资产，
-- 所以半年报 ROE 天然约为年报的一半（实测：年报中位数 4.69% vs 半年报 2.52%）。
-- 因此本查询用 {report_date} 参数锁定单一报告期，绝不跨期比较。
-- ============================================================================

WITH industry_stats AS (
    SELECT
        industry                                          AS 行业,
        COUNT(DISTINCT stock_code)                        AS 公司数,
        -- 用中位数而不是平均值：行业里只要有一家公司 ROE 是 2000%，
        -- 平均值就被彻底带偏，中位数对这种极端值免疫。
        ROUND(MEDIAN(roe_w), 2)                           AS ROE中位数,
        ROUND(AVG(roe_w), 2)                              AS ROE平均值,
        ROUND(MEDIAN(net_margin_w), 2)                    AS 净利率中位数,
        ROUND(MEDIAN(gross_margin_w), 2)                  AS 毛利率中位数,
        ROUND(MEDIAN(revenue_yi), 2)                      AS 营收中位数亿,
        -- 条件聚合：把布尔列当成 0/1 求和，就能一次算出占比
        ROUND(100.0 * SUM(CASE WHEN flag_loss THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                          AS 亏损占比,
        -- 行业内部分化程度：净利率标准差越大，说明同行业内公司差异越大
        ROUND(STDDEV(net_margin_w), 2)                    AS 净利率标准差
    FROM dwd_finance_performance
    WHERE is_analyzable = 1                -- 只保留真实 A 股口径
      AND report_date = '{report_date}'    -- 锁定报告期，保证可比
      AND is_st = 0                        -- 剔除 ST（退市风险警示）公司
    GROUP BY industry
    HAVING COUNT(DISTINCT stock_code) >= 8 -- 样本太少时中位数没有统计意义
)
SELECT
    -- 窗口函数：在不破坏明细行的前提下，给出全局排名
    RANK() OVER (ORDER BY ROE中位数 DESC)      AS 盈利排名,
    行业,
    公司数,
    ROE中位数,
    ROE平均值,
    净利率中位数,
    毛利率中位数,
    营收中位数亿,
    亏损占比,
    净利率标准差,
    -- 与全行业中位数的偏离：正数说明该行业优于市场平均
    ROUND(ROE中位数 - (SELECT MEDIAN(roe_w) FROM dwd_finance_performance
                       WHERE is_analyzable = 1 AND report_date = '{report_date}'
                         AND is_st = 0), 2)        AS 高于市场中位数,
    -- 把 ROE 中位数分成 4 档，便于业务方快速理解
    CASE
        WHEN ROE中位数 >= 10 THEN 'A-优秀'
        WHEN ROE中位数 >= 5  THEN 'B-良好'
        WHEN ROE中位数 >= 2  THEN 'C-一般'
        ELSE                      'D-偏弱'
    END                                            AS 行业评级
FROM industry_stats
ORDER BY 盈利排名;
