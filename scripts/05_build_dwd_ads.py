"""W2 数仓分层：ods → dwd（清洗明细层）+ ads（应用聚合层）。

数仓分层为什么要这么分？（面试高频问题）
—————————————————————————————————————————————
   ods (Operational Data Store)  原始层：接口返回的数据原样落地，不做任何加工
   dwd (Data Warehouse Detail)   明细层：清洗、标准化、加标记，一行 = 一家公司一个报告期
   ads (Application Data Store)  应用层：面向具体分析场景的聚合结果

分层的价值：
   1. **可追溯**：ads 的数字不对，能顺着 dwd → ods 一路查回来
   2. **可复用**：dwd 只做一次清洗，所有分析场景共用，避免每个脚本各写一套清洗逻辑
   3. **可维护**：口径要改，只改 dwd 一层，ads 重新跑一遍即可

本脚本产出：
    dwd_finance_performance   明细层（含特征、标记、分档）
    ads_industry_overview     行业维度聚合
    ads_market_overview       板块维度聚合
    ads_dividend_analysis     股息率专题
    ads_data_summary          全市场汇总
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import features, storage  # noqa: E402
from jmintel.logging_setup import get_logger, setup_logging  # noqa: E402

logger = get_logger("build_dwd_ads")

ODS_TABLE = "ods_finance_performance"
DWD_TABLE = "dwd_finance_performance"

# 行业聚合时要求的最少公司数。
# 为什么设这个门槛？一个行业只有 2 家公司时，"行业中位数"没有统计意义，
# 容易被极端值带偏，得出来的结论不可信。
MIN_COMPANIES_PER_GROUP = 8


def build_dwd() -> pd.DataFrame:
    """构建明细层：读取 ods → 特征工程 → 落库。"""
    logger.info("=" * 68)
    logger.info("第 1 步：构建 dwd 明细层")
    logger.info("=" * 68)

    ods = storage.read_table(ODS_TABLE)
    logger.info("读取 ods 层：%d 行 × %d 列", len(ods), ods.shape[1])

    dwd = features.build_features(ods)

    # 去重保险：同一公司同一报告期只保留一条（新数据优先）
    dwd["report_date"] = dwd["report_date"].astype(str).str[:10]
    before = len(dwd)
    dwd = dwd.drop_duplicates(subset=["stock_code", "report_date"], keep="last")
    if before != len(dwd):
        logger.info("去重：%d → %d 行", before, len(dwd))

    storage.save_dataframe(dwd, DWD_TABLE)
    return dwd


def build_ads(dwd: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """构建应用层聚合表。"""
    logger.info("=" * 68)
    logger.info("第 2 步：构建 ads 应用层聚合表")
    logger.info("=" * 68)

    ad: dict[str, pd.DataFrame] = {}
    base = dwd[dwd["is_analyzable"]].copy()
    logger.info("聚合口径：is_analyzable = True，共 %d 条记录", len(base))
    logger.info("    含 %d 个报告期，涉及 %d 家公司",
                base["report_date"].nunique(), base["stock_code"].nunique())

    # ---------- 口径说明（重要，避免误读）----------
    # 聚合的每一条 = 「一家公司在一个报告期」的表现，所以：
    #   record_count  = 记录数（含多个报告期）
    #   company_count = 去重后的公司数
    # 两者不等，是因为同一家公司在 2024 年报和 2025 半年报里各出现一次。
    # 做同比/趋势分析时必须按 report_date 分开看，不能直接混在一起求平均。

    # ---------- ads 1：行业维度 ----------
    ind = base.groupby("industry", dropna=False).agg(
        record_count=("stock_code", "size"),
        company_count=("stock_code", "nunique"),
        report_period_count=("report_date", "nunique"),
        avg_roe=("roe_w", "mean"),
        median_roe=("roe_w", "median"),
        median_net_margin=("net_margin_w", "median"),
        median_gross_margin=("gross_margin_w", "median"),
        median_revenue_yi=("revenue_yi", "median"),
        median_growth=("growth_score", "median"),
        loss_record_count=("flag_loss", "sum"),
        total_revenue_yi=("revenue_yi", "sum"),
        total_profit_yi=("net_profit_yi", "sum"),
        median_dividend_yield=("dividend_yield_w", "median"),
    ).reset_index()

    # 派生：亏损记录占比——比"亏损家数"更能横向比较（大行业家数天然多）
    ind["loss_ratio"] = (ind["loss_record_count"] /
                         ind["record_count"] * 100).round(2)
    # 净利率的行业离散度：越高说明行业内部分化越严重
    dispersion = base.groupby("industry")["net_margin_w"].std().round(2)
    ind["net_margin_std"] = ind["industry"].map(dispersion)
    # 保留数值精度
    for c in ind.columns:
        if ind[c].dtype.kind == "f":
            ind[c] = ind[c].round(2)

    ind = ind.sort_values("median_roe", ascending=False)
    # 标记样本量是否足够（供使用者自行过滤）
    ind["is_reliable"] = ind["company_count"] >= MIN_COMPANIES_PER_GROUP
    ad["ads_industry_overview"] = ind

    # ---------- ads 2：板块维度 ----------
    mkt = base.groupby("market").agg(
        record_count=("stock_code", "size"),
        company_count=("stock_code", "nunique"),
        median_roe=("roe_w", "median"),
        median_net_margin=("net_margin_w", "median"),
        median_revenue_yi=("revenue_yi", "median"),
        median_growth=("growth_score", "median"),
        median_eps=("eps", "median"),
        loss_ratio=("flag_loss", "mean"),
        st_record_count=("is_st", "sum"),
    ).reset_index()
    mkt["loss_ratio"] = (mkt["loss_ratio"] * 100).round(2)
    for c in mkt.columns:
        if mkt[c].dtype.kind == "f":
            mkt[c] = mkt[c].round(2)
    ad["ads_market_overview"] = mkt.sort_values("median_roe", ascending=False)

    # ---------- ads 3：股息率专题 ----------
    div = base[base["dividend_yield"].notna() & base["dividend_yield"].gt(0)].copy()
    if not div.empty:
        div_summary = div.groupby("industry", dropna=False).agg(
            dividend_company_count=("stock_code", "nunique"),
            median_dividend_yield=("dividend_yield_w", "median"),
            max_dividend_yield=("dividend_yield_w", "max"),
            median_roe=("roe_w", "median"),
            median_net_margin=("net_margin_w", "median"),
            median_revenue_yi=("revenue_yi", "median"),
        ).reset_index()
        for c in div_summary.columns:
            if div_summary[c].dtype.kind == "f":
                div_summary[c] = div_summary[c].round(2)
        ad["ads_dividend_analysis"] = div_summary.sort_values(
            "median_dividend_yield", ascending=False)
        logger.info("股息率专题：%d 家有分红记录，覆盖 %d 个行业",
                    div["stock_code"].nunique(), div_summary["industry"].nunique())

    # ---------- ads 4：报告期汇总（按报告期分开，不与行业混合）----------
    summary = base.groupby(["report_date"]).agg(
        record_count=("stock_code", "size"),
        company_count=("stock_code", "nunique"),
        median_roe=("roe_w", "median"),
        median_net_margin=("net_margin_w", "median"),
        median_revenue_yi=("revenue_yi", "median"),
        median_growth=("growth_score", "median"),
        total_revenue_yi=("revenue_yi", "sum"),
        total_profit_yi=("net_profit_yi", "sum"),
        loss_ratio=("flag_loss", "mean"),
    ).reset_index()
    summary["loss_ratio"] = (summary["loss_ratio"] * 100).round(2)
    for c in summary.columns:
        if summary[c].dtype.kind == "f":
            summary[c] = summary[c].round(2)
    ad["ads_data_summary"] = summary

    # 落库
    for name, frame in ad.items():
        storage.save_dataframe(frame, name)

    return ad


def main() -> int:
    setup_logging(log_file=PROJECT_ROOT / "logs" / "etl.log")
    logger.info("开始 ETL：ods → dwd → ads")

    dwd = build_dwd()
    ad = build_ads(dwd)

    # ---------- 结果概览 ----------
    logger.info("=" * 68)
    logger.info("ETL 完成，结果概览")
    logger.info("=" * 68)
    logger.info("dwd 明细层：%d 行 × %d 列", len(dwd), dwd.shape[1])
    for name, frame in ad.items():
        logger.info("%-24s %d 行 × %d 列", name, len(frame), frame.shape[1])

    logger.info("-" * 68)
    logger.info("行业 ROE 中位数 TOP8（仅统计样本量 >= %d 的行业）：",
                MIN_COMPANIES_PER_GROUP)
    ind = ad["ads_industry_overview"]
    top = ind[ind["is_reliable"]].nlargest(8, "median_roe")[
        ["industry", "company_count", "median_roe", "median_net_margin",
         "loss_ratio", "median_revenue_yi"]]
    print(top.to_string(index=False))

    logger.info("-" * 68)
    logger.info("行业 ROE 中位数 BOTTOM5：")
    bottom = ind[ind["is_reliable"]].nsmallest(5, "median_roe")[
        ["industry", "company_count", "median_roe", "median_net_margin", "loss_ratio"]]
    print(bottom.to_string(index=False))

    logger.info("-" * 68)
    logger.info("ROE 分档分布（全市场）：")
    tier = dwd[dwd["is_analyzable"]]["roe_tier"].value_counts()
    for name, cnt in tier.items():
        ratio = cnt / tier.sum() * 100
        logger.info("    %-6s %5d 家 (%.1f%%)", str(name), cnt, ratio)

    logger.info("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
