"""SQL 分析执行器：批量运行 sql/ 目录下的业务分析 SQL。

为什么需要一个执行器，而不是手工复制粘贴到数据库工具里跑？
—————————————————————————————————————————————
  1. **可复现**：任何人 clone 项目后一条命令就能跑出所有分析结果
  2. **可验证**：CI 里可以跑，确认 SQL 没有语法错误
  3. **可归档**：结果自动写入 docs/analysis/，成为项目文档的一部分
  4. **参数化**：报告期通过参数传入，同一套 SQL 能复用于不同年份

技术要点：SQLite 没有内置 MEDIAN / STDDEV 聚合函数，
但可以用 create_aggregate 注册自定义聚合函数。这比改写 SQL 更干净——
SQL 里始终写 MEDIAN(x)，换到 PostgreSQL / DuckDB 也能直接跑。

用法：
    python scripts/07_run_sql.py                  # 跑全部，默认 2024-12-31
    python scripts/07_run_sql.py --report-date 2025-06-30
    python scripts/07_run_sql.py --only 01 03     # 只跑指定编号
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config  # noqa: E402

SQL_DIR = PROJECT_ROOT / "sql"
OUT_DIR = PROJECT_ROOT / "docs" / "analysis"

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


# =========================================================
# 自定义聚合函数：让 SQLite 拥有 MEDIAN / STDDEV
# =========================================================
class Median:
    """中位数聚合函数。

    中位数比平均值更抗极端值：
    某个行业里只要有一家公司 ROE 是 2000%，平均值就被彻底带偏，
    中位数则几乎不受影响。财务分析里中位数是更可靠的中心趋势指标。
    """

    def __init__(self) -> None:
        self.values: list[float] = []

    def step(self, value: float | None) -> None:
        if value is not None:
            self.values.append(value)

    def finalize(self) -> float | None:
        if not self.values:
            return None
        self.values.sort()
        n = len(self.values)
        mid = n // 2
        if n % 2 == 1:
            return float(self.values[mid])
        return (self.values[mid - 1] + self.values[mid]) / 2.0


class StdDev:
    """样本标准差聚合函数（用于衡量行业内部分化程度）。"""

    def __init__(self) -> None:
        self.values: list[float] = []

    def step(self, value: float | None) -> None:
        if value is not None:
            self.values.append(value)

    def finalize(self) -> float | None:
        n = len(self.values)
        if n < 2:
            return None
        mean = sum(self.values) / n
        # 样本标准差：除以 n-1（贝塞尔校正），这是统计学惯例
        var = sum((x - mean) ** 2 for x in self.values) / (n - 1)
        return var ** 0.5


def connect() -> sqlite3.Connection:
    """连接数据库并注册自定义聚合函数。"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.create_aggregate("MEDIAN", 1, Median)
    conn.create_aggregate("STDDEV", 1, StdDev)
    return conn


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="批量执行 sql/ 目录下的分析 SQL")
    p.add_argument("--report-date", default="2024-12-31",
                   help="报告期，默认 2024-12-31（年报口径，ROE 可跨行业比较）")
    p.add_argument("--only", nargs="*", default=None,
                   help="只执行指定编号的 SQL，如 --only 01 05")
    p.add_argument("--no-save", action="store_true",
                   help="不把结果写入 docs/analysis/")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    sql_files = sorted(SQL_DIR.glob("*.sql"))
    if args.only:
        sql_files = [f for f in sql_files if f.stem.split("_")[0] in args.only]

    if not sql_files:
        print("没有找到要执行的 SQL 文件")
        return 1

    if not config.DB_PATH.exists():
        print(f"数据库不存在：{config.DB_PATH}，请先运行 05_build_dwd_ads.py")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()

    print("=" * 100)
    print(f"SQL 分析执行器 | 报告期 = {args.report_date} | 共 {len(sql_files)} 个分析")
    print("=" * 100)

    report_lines = [
        "# SQL 业务分析结果",
        "",
        f"- 报告期：**{args.report_date}**",
        f"- 数据源表：`dwd_finance_performance`",
        f"- 分析口径：`is_analyzable = 1`（真实 A 股 + 有行业分类 + 有关键指标 + 未退市）",
        f"- 执行的 SQL 数量：{len(sql_files)}",
        "",
        "> 本文件由 `scripts/07_run_sql.py` 自动生成，请勿手工编辑。",
        "",
        "---",
        "",
    ]

    ok = fail = 0
    for sql_file in sql_files:
        raw = sql_file.read_text(encoding="utf-8")

        # ---------- 提取文件头的业务问题作为标题 ----------
        title = sql_file.stem
        for line in raw.splitlines():
            if "业务问题：" in line:
                title = line.split("业务问题：", 1)[1].strip()
                break

        print(f"\n{'=' * 100}")
        print(f"【{sql_file.stem}】{title}")
        print(f"{'=' * 100}")

        # ---------- 参数替换 ----------
        # 用 replace 而不是 str.format，因为 SQL 里有 CASE WHEN 等不含花括号，
        # 但为保险起见，只替换明确的占位符，避免误伤其他内容。
        sql = raw.replace("{report_date}", args.report_date)

        try:
            df = pd.read_sql_query(sql, conn)
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print(f"❌ 执行失败：{type(exc).__name__}: {exc}")
            report_lines += [f"## {sql_file.stem} {title}", "",
                             f"❌ 执行失败：`{exc}`", "", "---", ""]
            continue

        ok += 1
        print(f"✅ 返回 {len(df)} 行 × {df.shape[1]} 列\n")
        # 控制台只显示前 15 行，避免刷屏；完整结果写入文档
        with pd.option_context("display.max_rows", 15):
            print(df.head(15).to_string(index=False))
        if len(df) > 15:
            print(f"...（共 {len(df)} 行，完整结果见 docs/analysis/）")

        if not args.no_save:
            # Markdown 表格形式存档
            report_lines += [f"## {sql_file.stem.replace('_', ' ')} — {title}", ""]
            report_lines += [f"返回 **{len(df)}** 行。", ""]
            report_lines += [df.to_markdown(index=False), "", "---", ""]
            # 同时存一份 CSV，方便用 Excel 打开
            csv_path = OUT_DIR / f"{args.report_date}_{sql_file.stem}.csv"
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    conn.close()

    if not args.no_save and ok:
        report_path = OUT_DIR / f"分析结果_{args.report_date}.md"
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        print(f"\n{'=' * 100}")
        print(f"结果已归档：{report_path.relative_to(PROJECT_ROOT)}")

    print(f"\n{'=' * 100}")
    print(f"执行完成：成功 {ok} 个 / 失败 {fail} 个")
    print("=" * 100)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
