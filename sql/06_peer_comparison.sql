-- ============================================================================
-- 文件：06_peer_comparison.sql
-- 业务问题：单个公司在同行里处于什么水平？（找出显著优于同行的公司）
-- 技术要点：CTE 预聚合 + INNER JOIN 明细表 —— 实现"组内比较"
--
-- 【重要技术说明：为什么不用窗口函数算行业中位数】
-- 本查询最初写成：
--     MEDIAN(roe_w) OVER (PARTITION BY industry) AS 行业ROE中位数
-- 结果报错。原因是 SQLite 的一个真实限制：
--     **自定义聚合函数（我们自己注册的 MEDIAN）不能用作窗口函数。**
-- SQLite 只在 3.25+ 支持窗口函数，且仅内置聚合（SUM/AVG/COUNT 等）
-- 可作为窗口聚合；用 create_aggregate 注册的函数只能用于 GROUP BY 场景。
--
-- 解决方案：把"行业统计量"拆成一个 CTE 先算好，再用 JOIN 关联到明细行。
-- 这与窗口函数效果完全一致，而且兼容性更好（几乎所有数据库都支持）。
-- 这个取舍值得记住：**遇到数据库特性限制时，用标准 SQL 改写而不是换数据库。**
-- ============================================================================

WITH peer_stats AS (
    -- 第 1 步：按行业算出基准统计量（用 GROUP BY，自定义聚合函数在这里可用）
    SELECT
        industry                  AS industry,
        COUNT(DISTINCT stock_code) AS 行业公司数,
        MEDIAN(roe_w)             AS 行业ROE中位数,
        MEDIAN(net_margin_w)      AS 行业净利率中位数
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
      AND is_st = 0
      AND roe_w IS NOT NULL
    GROUP BY industry
    HAVING COUNT(DISTINCT stock_code) >= 8   -- 剔除小样本行业
),
company_detail AS (
    -- 第 2 步：取出每家公司自己的指标
    SELECT
        stock_code,
        stock_name,
        industry,
        market,
        roe_w,
        net_margin_w,
        revenue_yi,
        net_profit_yi,
        dividend_yield_w
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
      AND is_st = 0
      AND roe_w IS NOT NULL
)
SELECT
    d.industry        AS 行业,
    d.stock_code      AS 股票代码,
    d.stock_name      AS 股票简称,
    d.market          AS 板块,
    ROUND(d.roe_w, 2)                AS ROE,
    ROUND(p.行业ROE中位数, 2)         AS 行业ROE中位数,
    ROUND(d.roe_w - p.行业ROE中位数, 2) AS ROE超额,
    -- NULLIF 防除零：行业中位数为 0 时返回 NULL，而不是抛"除以零"
    ROUND(100.0 * (d.roe_w - p.行业ROE中位数)
          / NULLIF(ABS(p.行业ROE中位数), 0), 2)  AS ROE超额百分比,
    ROUND(d.net_margin_w, 2)         AS 净利率,
    ROUND(d.revenue_yi, 2)           AS 营收亿,
    ROUND(d.net_profit_yi, 2)        AS 净利润亿,
    ROUND(d.dividend_yield_w, 2)     AS 股息率,
    p.行业公司数
FROM company_detail d
INNER JOIN peer_stats p ON d.industry = p.industry
WHERE d.roe_w > p.行业ROE中位数      -- 只保留优于行业中位数的公司
  AND d.net_profit_yi > 0            -- 且必须是真正赚钱的公司
ORDER BY ROE超额 DESC
LIMIT 50;
