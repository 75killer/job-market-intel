-- ============================================================================
-- 文件：04_top_bottom_companies.sql
-- 业务问题：各行业里最赚钱的公司是谁？（每个行业取前 3 名）
-- 技术要点：ROW_NUMBER() 分组排名 + CTE
--
-- 【为什么用 ROW_NUMBER 而不是 LIMIT】
-- LIMIT 是全局限制，只能取"全市场前 10"。
-- 要取"每个行业的前 3 名"，必须用窗口函数按行业 partition 后编号。
-- 这是「组内取前 N」的标准解法：
--     PARTITION BY industry  →  在每个行业内部独立编号
--     ORDER BY roe_w DESC    →  按 ROE 从高到低
-- ============================================================================

WITH ranked AS (
    SELECT
        industry,
        stock_code,
        stock_name,
        market,
        roe_w,
        net_margin_w,
        revenue_yi,
        net_profit_yi,
        dividend_yield_w,
        ROW_NUMBER() OVER (
            PARTITION BY industry
            ORDER BY roe_w DESC
        ) AS 行业内排名
    FROM dwd_finance_performance
    WHERE is_analyzable = 1
      AND report_date = '{report_date}'
      AND is_st = 0
      AND roe_w IS NOT NULL
      AND revenue_yi > 0
),
-- 只保留公司数 >= 8 的行业，避免"3 家公司里取前 3 名"这种无意义结果
valid_industries AS (
    SELECT industry
    FROM dwd_finance_performance
    WHERE is_analyzable = 1 AND report_date = '{report_date}' AND is_st = 0
    GROUP BY industry
    HAVING COUNT(DISTINCT stock_code) >= 8
)
SELECT
    r.industry      AS 行业,
    r.行业内排名,
    r.stock_code    AS 股票代码,
    r.stock_name    AS 股票简称,
    r.market        AS 上市板块,
    ROUND(r.roe_w, 2)             AS ROE,
    ROUND(r.net_margin_w, 2)      AS 净利率,
    ROUND(r.revenue_yi, 2)        AS 营收亿,
    ROUND(r.net_profit_yi, 2)     AS 净利润亿,
    ROUND(r.dividend_yield_w, 2)  AS 股息率
FROM ranked r
INNER JOIN valid_industries v ON r.industry = v.industry
WHERE r.行业内排名 <= 3
ORDER BY r.industry, r.行业内排名;
