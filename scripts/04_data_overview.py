"""数据总览：一眼看清我们手上有多少数据、质量如何。

每次采集完跑一遍这个脚本，确认三件事：
  1. 数据量对不对（有没有少采、重复）
  2. 报告期是否齐全
  3. 有效分析口径是多少家（剔除新三板/退市后）

这个脚本是后续所有分析的基础——**先确认数据，再谈结论**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import storage  # noqa: E402

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 120)

TABLE = "ods_finance_performance"

print("=" * 78)
print("数据总览")
print("=" * 78)

df = storage.read_table(TABLE)
print(f"\n总记录数：{len(df):,}")
print(f"唯一股票代码：{df['stock_code'].nunique():,}")
print(f"报告期：{sorted(df['report_date'].dropna().astype(str).str[:10].unique())}")

print("\n按报告期统计：")
summary = df.groupby(df["report_date"].astype(str).str[:10]).agg(
    记录数=("stock_code", "size"),
    公司数=("stock_code", "nunique"),
    行业数=("industry", "nunique"),
    ROE中位数=("roe", "median"),
    平均营收亿=("revenue_yi", "mean"),
)
print(summary.round(2).to_string())

# ---------- 有效分析口径 ----------
print("\n" + "-" * 78)
print("有效分析口径（只保留真正的 A 股交易所上市公司）")
print("-" * 78)
# 口径说明：market 字段的取值实测为
#   上交所主板 / 上交所科创板 / 上交所风险警示板
#   深交所主板 / 深交所创业板 / 深交所风险警示板
#   北京证券交易所  ← A 股
#   新三板 / 老三板  ← 非上市挂牌，且无行业分类，必须剔除
# 注意：ST(风险警示板) 公司**保留**，因为它们是 A 股的一部分，
# 只是后续分析时需要单独标记，不能直接删——删了会让亏损公司的统计失真。
market_str = df["market"].astype(str)
is_listed = market_str.str.contains("上交所|深交所|北京证券交易所", na=False)
has_industry = df["industry"].notna() & (df["industry"].astype(str).str.strip() != "")
not_delisted = ~df["stock_name"].astype(str).str.contains("摘牌|退市", na=False)

valid = df[is_listed & has_industry & not_delisted].copy()
print(f"口径：上交所/深交所/北交所 且 有行业分类 且 未退市")
print(f"    → {len(valid):,} 条")
print(f"    覆盖公司：{valid['stock_code'].nunique():,} 家")
print(f"    覆盖行业：{valid['industry'].nunique()} 个")
print(f"    ROE 中位数：{valid['roe'].median():.2f}%")
print(f"    营收中位数：{valid['revenue_yi'].median():.2f} 亿元")

print("\n按板块分布：")
print(valid["market"].value_counts().to_string())

# ---------- 行业 TOP10（用 ROE 中位数排序，比平均值更抗极端值）----------
print("\n" + "-" * 78)
print("行业 ROE 中位数 TOP10（筛选条件：至少 10 家公司，避免小样本偏差）")
print("-" * 78)
grp = valid.groupby("industry").agg(
    公司数=("stock_code", "nunique"),
    ROE中位数=("roe", "median"),
    净利率中位数=("net_margin", "median"),
    营收中位数亿=("revenue_yi", "median"),
)
grp = grp[grp["公司数"] >= 10].sort_values("ROE中位数", ascending=False)
print(grp.head(10).round(2).to_string())

print("\n" + "-" * 78)
print("行业 ROE 中位数 BOTTOM5")
print("-" * 78)
print(grp.tail(5).round(2).to_string())

print("\n" + "=" * 78)
print("提示：以上为初步数据概览，正式的行业分析将在 W3 用 SQL 完成")
print("=" * 78)
