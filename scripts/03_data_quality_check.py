"""数据质量体检：搞清楚我们采到的到底是什么范围内的公司。

为什么必须做这一步？
  采集脚本报"11607 家公司"，听起来很好。
  但如果不检查构成，就可能把**新三板、已退市、B股**的公司混进来，
  得出一堆看起来专业、实际错误的结论。

  这是数据分析里最容易被忽视、也最容易翻车的一步：
  **先搞清楚你的样本是什么，再谈分析结论。**

本脚本要回答：
  1. 数据里都有哪些上市板块？各多少家？
  2. 有多少家是已退市/摘牌的？
  3. 有多少家没有行业分类？
  4. ROE 的分布是否合理？极端值有多少？
  5. 如果要研究"A 股上市公司财务质量"，应该保留哪些数据？
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config  # noqa: E402

pd.set_option("display.unicode.east_asian_width", True)

print("=" * 78)
print("数据质量体检")
print("=" * 78)

with sqlite3.connect(config.DB_PATH) as conn:
    df = pd.read_sql_query("SELECT * FROM ods_finance_performance", conn)

print(f"\n总记录数：{len(df)}")
print(f"唯一股票代码：{df['stock_code'].nunique()}")

# ---------- 1. 上市板块分布 ----------
print("\n" + "-" * 78)
print("【1】上市板块分布（这个字段决定了样本的含金量）")
print("-" * 78)
market = df["market"].value_counts(dropna=False)
for name, cnt in market.items():
    pct = cnt / len(df) * 100
    print(f"    {str(name):<16} {cnt:>6} 条  ({pct:>5.1f}%)")

# ---------- 2. 已退市 / 摘牌 ----------
print("\n" + "-" * 78)
print("【2】已退市或摘牌的公司（名字里含 摘牌 / 退 字样）")
print("-" * 78)
delisted_mask = df["stock_name"].astype(str).str.contains("摘牌|退市|退", na=False)
print(f"    匹配到 {int(delisted_mask.sum())} 条")
if delisted_mask.any():
    print(df.loc[delisted_mask, ["stock_code", "stock_name", "market",
                                 "report_date"]].head(10).to_string(index=False))

# ---------- 3. 行业缺失 ----------
print("\n" + "-" * 78)
print("【3】行业分类缺失情况")
print("-" * 78)
na_industry = df["industry"].isna() | (df["industry"].astype(str).str.strip() == "")
print(f"    行业缺失：{int(na_industry.sum())} 条 ({na_industry.mean()*100:.1f}%)")
if na_industry.any():
    print("    缺失样本的板块分布：")
    print(df.loc[na_industry, "market"].value_counts(dropna=False).to_string())
    print("\n    缺失样本的代码前两位分布（8 开头通常是新三板/北交所）：")
    prefix = df.loc[na_industry, "stock_code"].astype(str).str[:2].value_counts()
    print(prefix.head(10).to_string())

# ---------- 4. ROE 分布 ----------
print("\n" + "-" * 78)
print("【4】ROE 分布检查")
print("-" * 78)
roe = df["roe"].dropna()
print(f"    有效值：{len(roe)} 条，缺失 {df['roe'].isna().sum()} 条")
if len(roe):
    print(f"    最小值 {roe.min():.2f} | 25分位 {roe.quantile(.25):.2f} "
          f"| 中位数 {roe.median():.2f} | 75分位 {roe.quantile(.75):.2f} "
          f"| 最大值 {roe.max():.2f}")
    for lo, hi, label in [(-1e9, 0, "负值(亏损)"), (0, 15, "0~15% 正常区间"),
                          (15, 50, "15~50% 偏高"), (50, 100, "50~100% 异常偏高"),
                          (100, 1e9, ">100% 极端值")]:
        n = int(((roe > lo) & (roe <= hi)).sum())
        print(f"    {label:<18} {n:>6} 条 ({n/len(roe)*100:>5.1f}%)")

# ---------- 5. 按板块看 ROE 中位数（关键判断依据）----------
print("\n" + "-" * 78)
print("【5】各板块的 ROE 中位数与行业缺失率（判断是否该剔除）")
print("-" * 78)
grp = df.groupby(df["market"].fillna("(缺失)"), dropna=False).agg(
    记录数=("stock_code", "size"),
    有行业数=("industry", lambda s: s.notna().sum()),
    ROE中位数=("roe", "median"),
    营收中位数亿=("revenue_yi", "median"),
)
grp["行业缺失率%"] = ((grp["记录数"] - grp["有行业数"]) / grp["记录数"] * 100).round(1)
print(grp.to_string())

# ---------- 6. 建议的过滤规则 ----------
print("\n" + "-" * 78)
print("【6】建议的过滤规则（用于后续分析口径）")
print("-" * 78)
a_share = df[~na_industry & ~delisted_mask]
print(f"    规则：剔除行业缺失 + 剔除已退市  →  剩余 {len(a_share)} 条")
print(f"    覆盖公司：{a_share['stock_code'].nunique()} 家，"
      f"行业 {a_share['industry'].nunique()} 个")
print(f"    ROE 中位数：{a_share['roe'].median():.2f}%")
print("\n    验证：过滤后 ROE 最高的 5 家（应该都是正常公司了）")
top = a_share.nlargest(5, "roe")[
    ["stock_code", "stock_name", "market", "industry", "roe", "revenue_yi"]
]
print(top.to_string(index=False))

print("\n" + "=" * 78)
