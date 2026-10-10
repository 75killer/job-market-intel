"""数据核实：确认几个可疑的数字是否合理。

为什么要做这一步？
因为"程序跑通"不等于"结果正确"。ETL 日志里有三个数字需要解释：
  1. revenue_yoy 有 23135 条被截尾（占总记录 99.5%）—— 这个比例高得反常
  2. 部分行业亏损占比接近 50% —— 需要确认是真实情况还是口径问题
  3. ROE 分档里"一般"占 55.7% —— 需要确认分档边界是否合理

核实方法：直接看原始分布，而不是看聚合结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import storage  # noqa: E402

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 130)

dwd = storage.read_table("dwd_finance_performance")
print("=" * 78)
print(f"dwd 明细层：{len(dwd)} 行 × {dwd.shape[1]} 列")
print("=" * 78)

# ---------- 疑点 1：revenue_yoy 为什么几乎全被截尾 ----------
print("\n【疑点 1】revenue_yoy（营收同比）的真实分布")
print("-" * 78)
for period in sorted(dwd["report_date"].unique()):
    sub = dwd[dwd["report_date"] == period]
    s = sub["revenue_yoy"]
    print(f"\n  报告期 {period}（{len(sub)} 条）")
    print(f"    非空: {s.notna().sum()} 条 ({s.notna().mean()*100:.1f}%)"
          f"  空值: {s.isna().sum()} 条")
    if s.notna().any():
        d = s.dropna()
        print(f"    最小值 {d.min():.2f} | 1%分位 {d.quantile(.01):.2f} "
              f"| 中位数 {d.median():.2f} | 99%分位 {d.quantile(.99):.2f} "
              f"| 最大值 {d.max():.2f}")
        out_of_bounds = ((d < -200) | (d > 500)).sum()
        print(f"    超出 [-200, 500] 边界: {out_of_bounds} 条 "
              f"({out_of_bounds/len(d)*100:.1f}%)")

# ---------- 疑点 2：亏损占比 ----------
print("\n【疑点 2】亏损情况（按报告期分开看）")
print("-" * 78)
for period in sorted(dwd["report_date"].unique()):
    sub = dwd[(dwd["report_date"] == period) & dwd["is_analyzable"]]
    loss = sub["flag_loss"]
    print(f"  {period}: {len(sub)} 条中亏损 {int(loss.sum())} 条 "
          f"({loss.mean()*100:.1f}%)")
print("\n  说明：A 股长期约 25-30% 公司亏损属正常范围。")
print("        半年报口径亏损率通常会高于年报（半年度盈利难度更大）。")

# ---------- 疑点 3：ROE 分档边界 ----------
print("\n【疑点 3】roe_w 的实际分位数（用于判断分档边界是否合理）")
print("-" * 78)
for period in sorted(dwd["report_date"].unique()):
    s = dwd[(dwd["report_date"] == period) & dwd["is_analyzable"]]["roe_w"].dropna()
    print(f"\n  {period}: {len(s)} 条")
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        print(f"    {int(q*100):>2}% 分位 = {s.quantile(q):>8.2f}%")
    print(f"    ROE > 15%（优秀门槛）: {(s > 15).sum()} 条 ({(s>15).mean()*100:.1f}%)")
    print(f"    ROE < 0 （亏损）      : {(s < 0).sum()} 条 ({(s<0).mean()*100:.1f}%)")

# ---------- 半年报 ROE 可比性问题 ----------
print("\n【重要发现】报告期之间的可比性问题")
print("-" * 78)
annual = dwd[(dwd["report_date"] == "2024-12-31") & dwd["is_analyzable"]]["roe_w"].median()
semi = dwd[(dwd["report_date"] == "2025-06-30") & dwd["is_analyzable"]]["roe_w"].median()
print(f"  2024 年报 ROE 中位数    : {annual:.2f}%")
print(f"  2025 半年报 ROE 中位数  : {semi:.2f}%")
print(f"  差异                    : {annual - semi:.2f} 个百分点")
print("\n  原因：ROE = 净利润 / 净资产。半年报只累计了半年利润，")
print("        分母却是期末净资产，所以半年报 ROE 天然约为年报的一半。")
print("  → 结论：**跨报告期直接比较 ROE 是无意义的**，")
print("     分析行业时必须锁定同一个报告期。这是本项目必须写进口径说明的要点。")

print("\n" + "=" * 78)
