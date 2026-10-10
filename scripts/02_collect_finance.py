"""采集上市公司业绩报表数据。

用法（在项目根目录执行）：
    # 先小规模验证（只抓 1 页）
    .venv\\Scripts\\python.exe scripts\\02_collect_finance.py --report-date 2024-12-31 --max-pages 1

    # 全量采集 2024 年报 + 2025 半年报
    .venv\\Scripts\\python.exe scripts\\02_collect_finance.py

设计说明：
  - 第一次运行会走网络并把结果缓存到 data/raw/，之后重复运行直接读缓存
  - 采集失败不会丢失已拿到的数据（模块内部会先落盘再抛错）
  - 同时输出 CSV 和写入 SQLite，方便后续用 Excel / SQL 两种方式检查
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config, storage  # noqa: E402
from jmintel.http import HttpClient  # noqa: E402
from jmintel.logging_setup import get_logger, setup_logging  # noqa: E402
from jmintel.sources import eastmoney_finance  # noqa: E402

logger = get_logger("collect_finance")

# 默认采集的报告期：2024 年报（最完整）+ 2025 半年报（最新）
DEFAULT_REPORT_DATES = ["2024-12-31", "2025-06-30"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 A 股上市公司业绩报表")
    parser.add_argument(
        "--report-date", action="append", dest="report_dates",
        help="报告期，可重复指定，如 --report-date 2024-12-31；不填则用默认两组",
    )
    parser.add_argument("--max-pages", type=int, default=None,
                        help="最多采集页数（调试用，建议先填 1）")
    parser.add_argument("--no-cache", action="store_true",
                        help="忽略本地缓存，强制重新采集")
    parser.add_argument("--table", type=str, default="ods_finance_performance",
                        help="写入数据库的表名")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(log_file=PROJECT_ROOT / "logs" / "collect.log")
    config.ensure_dirs()

    report_dates = args.report_dates or DEFAULT_REPORT_DATES

    logger.info("=" * 64)
    logger.info("开始采集：上市公司业绩报表")
    logger.info("报告期：%s", "、".join(report_dates))
    logger.info("=" * 64)

    all_frames = []
    failures: list[str] = []

    with HttpClient() as client:
        for report_date in report_dates:
            logger.info("-" * 64)
            logger.info("▶ 报告期 %s", report_date)
            try:
                records = eastmoney_finance.fetch_performance(
                    client,
                    report_date=report_date,
                    max_pages=args.max_pages,
                    use_cache=not args.no_cache,
                )
            except Exception as exc:  # noqa: BLE001
                # 关键设计：单个报告期失败不影响其他报告期继续采集。
                # 这叫"故障隔离"——一条流水线不应该因为一个源挂掉就整体崩溃。
                logger.error("报告期 %s 采集失败：%s", report_date, exc)
                failures.append(report_date)
                continue

            if not records:
                logger.warning("报告期 %s 没有采到数据", report_date)
                failures.append(report_date)
                continue

            df = eastmoney_finance.parse_performance(records)
            all_frames.append(df)
            logger.info("报告期 %s 解析完成：%d 行", report_date, len(df))

    if not all_frames:
        logger.error("所有报告期都采集失败，请检查网络或等一段时间再试")
        return 1

    # 合并多个报告期
    import pandas as pd  # noqa: E402

    merged = pd.concat(all_frames, ignore_index=True)

    # 按（股票代码, 报告期）去重，防止多次运行产生重复行
    before = len(merged)
    merged = merged.drop_duplicates(subset=["stock_code", "report_date"], keep="last")
    if before != len(merged):
        logger.info("去重：%d 行 → %d 行", before, len(merged))

    # 落盘：用 upsert 而不是覆盖，避免"这次只采一个报告期"把别的报告期数据冲掉
    merged = storage.upsert_dataframe(
        merged, args.table, key_cols=["stock_code", "report_date"]
    )

    # ---------- 结果概览（最便宜的质检手段）----------
    logger.info("=" * 64)
    logger.info("采集概览")
    logger.info("=" * 64)
    logger.info("总记录数：%d", len(merged))
    logger.info("公司数（去重）：%d", merged["stock_code"].nunique())
    logger.info("行业数：%d", merged["industry"].nunique())
    logger.info("报告期分布：")
    for date, cnt in merged["report_date"].value_counts().sort_index().items():
        logger.info("    %s → %d 条", str(date)[:10], cnt)

    logger.info("-" * 64)
    logger.info("数据预览（ROE 最高的 5 家公司）：")
    cols = [c for c in ("stock_code", "stock_name", "industry", "report_date",
                        "roe", "revenue_yi", "net_profit_yi", "net_margin")
            if c in merged.columns]
    top = merged.nlargest(5, "roe")[cols].copy()
    if "report_date" in top.columns:
        # 【踩坑记录】不能直接用 .dt.strftime()。
        # 原因：upsert_dataframe 合并时会把主键列统一转成字符串以保证去重可靠，
        # 所以第二次运行（数据已存在）时 report_date 已经是 str 而不是 datetime，
        # 调用 .dt 访问器会抛 AttributeError: Can only use .dt accessor with
        # datetimelike values。
        # 这里统一用字符串截取，无论输入是 datetime 还是 str 都能正常工作。
        top["report_date"] = top["report_date"].astype(str).str[:10]
    print(top.to_string(index=False))

    logger.info("-" * 64)
    logger.info("数据质量检查：")
    logger.info("    净利润为负的公司：%d 家（占比 %.1f%%）",
                int(merged["flag_loss"].sum()),
                merged["flag_loss"].mean() * 100)
    logger.info("    营收或净利缺失：%d 条", int(merged["flag_missing_key"].sum()))
    logger.info("    ROE 缺失：%d 条", int(merged["roe"].isna().sum()))

    if failures:
        logger.warning("以下报告期采集失败（可稍后重试）：%s", failures)

    logger.info("=" * 64)
    logger.info("完成。数据文件：data/processed/%s.csv", args.table)
    logger.info("数据库：data/jmintel.db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
