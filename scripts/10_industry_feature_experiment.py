"""实验：行业特征能否提升 ROE 预测能力？

这是一个**负结果实验**：假设"加入行业特征能显著提升 R²"，
实测后发现**不成立**，并通过方差分解找到了根本原因。

为什么要保留一个"失败的实验"并写成脚本？
—————————————————————————————————————————————
因为负结果同样需要证据支撑。如果只把结论写在文档里，
别人无法验证；做成可复现的脚本，任何人都能跑一遍确认。

更重要的是：**这个实验暴露了我推理中的一个逻辑错误**——
把"行业均值差异大"当成了"行业能解释个体差异"。
把犯错过程记录下来，比只记录正确结论更有价值。

用法：
    python scripts/10_industry_feature_experiment.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.model_selection import KFold, cross_val_score
from sklearn.pipeline import Pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config, storage  # noqa: E402
from jmintel.logging_setup import get_logger, setup_logging  # noqa: E402

logger = get_logger("industry_experiment")

BASE_FEATURES = [
    "log_revenue", "revenue_yoy_w", "profit_yoy_w", "gross_margin_w",
    "expense_ratio", "dividend_yield_w", "ocf_per_share",
]
CV = KFold(n_splits=5, shuffle=True, random_state=42)

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 140)


def make_rf() -> RandomForestRegressor:
    return RandomForestRegressor(n_estimators=300, max_depth=8,
                                 min_samples_leaf=5, random_state=42, n_jobs=-1)


def make_gb() -> GradientBoostingRegressor:
    return GradientBoostingRegressor(n_estimators=200, max_depth=3,
                                     learning_rate=0.05, random_state=42)


def evaluate(X: pd.DataFrame, y: pd.Series, estimator) -> tuple[float, float]:
    """跑一次 5 折交叉验证，返回 (R² 均值, R² 标准差)。

    注意：这里每次都新建完整的 Pipeline，不复用变量、不用工厂函数传参，
    避免出现"以为换了模型其实没换"这类隐蔽错误。
    （本实验的第一版就因为复用了变量，导致两个模型印出相同数字，
      差点得出错误结论。实验代码必须先保证工具本身是对的。）
    """
    pipe = Pipeline([("imputer", SimpleImputer(strategy="median")),
                     ("model", estimator)])
    scores = cross_val_score(pipe, X, y, cv=CV, scoring="r2")
    return float(scores.mean()), float(scores.std())


def variance_decomposition(df: pd.DataFrame, target: str) -> dict:
    """对目标变量做方差分解：多少来自行业之间，多少来自行业内部。

    这是本实验最有说服力的证据。用到的概念是单因素方差分析（One-way ANOVA）：
        总平方和 SST = 组间平方和 SSB + 组内平方和 SSW
    其中"组"就是行业。
        组间占比大 → 行业是重要解释变量
        组内占比大 → 行业解释不了个体差异（本项目属于这种情况）
    """
    y = df[target]
    grand_mean = y.mean()
    ss_total = float(((y - grand_mean) ** 2).sum())
    group_means = df.groupby("industry")[target].transform("mean")
    ss_between = float(((group_means - grand_mean) ** 2).sum())
    ss_within = ss_total - ss_between

    grouped = df.groupby("industry")[target]
    return {
        "sst": ss_total,
        "ssb": ss_between,
        "ssw": ss_within,
        "ssb_pct": ss_between / ss_total * 100,
        "ssw_pct": ss_within / ss_total * 100,
        "between_std": float(grouped.mean().std()),
        "within_std": float(grouped.std().mean()),
    }


def main() -> int:
    setup_logging(log_file=PROJECT_ROOT / "logs" / "experiments.log")
    config.ensure_dirs()

    logger.info("=" * 76)
    logger.info("实验：行业特征能否提升 ROE 预测能力")
    logger.info("=" * 76)

    # ---------- 准备数据 ----------
    df = storage.read_table("dwd_finance_performance")
    d = df[
        df["report_date"].astype(str).str.startswith("2024-12-31")
        & df["is_analyzable"]
        & ~df["is_st"]
    ].copy()
    d = d[d["roe_w"].notna()].reset_index(drop=True)
    y = d["roe_w"]

    logger.info("样本：%d 条，行业数：%d", len(d), d["industry"].nunique())
    logger.info("基线特征（%d 个）：%s", len(BASE_FEATURES), "、".join(BASE_FEATURES))

    X_base = d[BASE_FEATURES].reset_index(drop=True)
    industry_dummies = pd.get_dummies(d["industry"], prefix="ind").astype(int)
    X_full = pd.concat([X_base, industry_dummies], axis=1)

    # ---------- 实验 1：三种哑变量规模 ----------
    logger.info("-" * 76)
    logger.info("实验 1：逐步增加行业哑变量数量")
    logger.info("-" * 76)
    sizes = d.groupby("industry")["stock_code"].size()
    exp1_rows = []
    base_r2, base_std = evaluate(X_base, y, make_rf())
    exp1_rows.append({"特征集": "仅7个财务特征（基线）", "特征数": len(BASE_FEATURES),
                      "R2均值": round(base_r2, 4), "R2标准差": round(base_std, 4)})
    logger.info("  %-26s R² = %.4f (±%.4f)", "仅7个财务特征（基线）", base_r2, base_std)

    for k in (10, 20, 30):
        top_industries = sizes.nlargest(k).index
        Xk = X_base.copy()
        for ind in top_industries:
            Xk[f"ind_{ind}"] = (d["industry"] == ind).astype(int)
        r2, std = evaluate(Xk, y, make_rf())
        label = f"+ Top{k} 行业哑变量"
        exp1_rows.append({"特征集": label, "特征数": Xk.shape[1],
                          "R2均值": round(r2, 4), "R2标准差": round(std, 4)})
        logger.info("  %-26s R² = %.4f (±%.4f)  变化 %+.4f",
                    label, r2, std, r2 - base_r2)

    # ---------- 实验 2：两种模型交叉验证 ----------
    logger.info("-" * 76)
    logger.info("实验 2：两种模型分别验证（避免单一模型的偶然性）")
    logger.info("-" * 76)
    exp2_rows = []
    for feat_label, X in (("财务特征(7个)", X_base), ("财务特征+行业哑变量", X_full)):
        for model_label, estimator in (("随机森林", make_rf()), ("梯度提升", make_gb())):
            r2, std = evaluate(X, y, estimator)
            exp2_rows.append({"特征集": feat_label, "特征数": X.shape[1],
                              "模型": model_label, "R2均值": round(r2, 4),
                              "R2标准差": round(std, 4)})
            logger.info("  %-22s %-8s R² = %.4f (±%.4f)",
                        feat_label, model_label, r2, std)

    exp2 = pd.DataFrame(exp2_rows)
    logger.info("-" * 76)
    logger.info("结论：行业特征的净效果")
    for model_label in ("随机森林", "梯度提升"):
        a = exp2[(exp2["特征集"] == "财务特征(7个)")
                 & (exp2["模型"] == model_label)]["R2均值"].values[0]
        b = exp2[(exp2["特征集"] == "财务特征+行业哑变量")
                 & (exp2["模型"] == model_label)]["R2均值"].values[0]
        delta = b - a
        verdict = "无提升" if abs(delta) < 0.01 else ("有提升" if delta > 0 else "反而下降")
        logger.info("    %s：%.4f → %.4f（%+.4f）=> %s",
                    model_label, a, b, delta, verdict)

    # ---------- 实验 3：只用行业信息 ----------
    logger.info("-" * 76)
    logger.info("实验 3：只给模型行业信息（不给任何财务特征）")
    logger.info("-" * 76)
    exp3_rows = []
    for model_label, estimator in (("随机森林", make_rf()), ("梯度提升", make_gb())):
        r2, std = evaluate(industry_dummies, y, estimator)
        exp3_rows.append({"特征集": "仅128个行业哑变量", "模型": model_label,
                          "R2均值": round(r2, 4), "R2标准差": round(std, 4)})
        logger.info("  仅行业哑变量  %-8s R² = %.4f (±%.4f)", model_label, r2, std)

    # ---------- 实验 4：Out-of-Fold 目标编码 ----------
    logger.info("-" * 76)
    logger.info("实验 4：Out-of-Fold 行业目标编码（对比'用行业均值当特征'的泄漏做法）")
    logger.info("-" * 76)
    oof = np.full(len(d), np.nan)
    for train_idx, test_idx in CV.split(d):
        # 只用训练折的数据算行业均值，再映射到测试折——这样才没有泄漏
        median_map = d.iloc[train_idx].groupby("industry")["roe_w"].median()
        oof[test_idx] = (d.iloc[test_idx]["industry"].map(median_map)
                         .fillna(y.median()).values)
    X_oof = X_base.copy()
    X_oof["industry_oof_median"] = oof
    r2_oof, std_oof = evaluate(X_oof, y, make_rf())
    logger.info("  财务特征 + OOF行业ROE中位数  R² = %.4f (±%.4f)", r2_oof, std_oof)
    logger.info("  说明：如果用行业 ROE 均值直接做特征而不做 OOF，R² 会虚高，")
    logger.info("        但那是目标泄漏（行业均值本身就由这些公司的 ROE 算出）。")

    # ---------- 实验 5：方差分解（核心证据）----------
    logger.info("=" * 76)
    logger.info("实验 5：ROE 方差分解 —— 行业到底能解释多少")
    logger.info("=" * 76)
    vd = variance_decomposition(d, "roe_w")
    # 注意：% 风格格式化不支持千位分隔符（%14,.0f 会原样打印出来），
    # 所以这里统一用 f-string —— 它支持 {:,} 这种写法。
    logger.info("  总平方和 SST      : %s", f"{vd['sst']:>14,.0f}")
    logger.info("  组间平方和 SSB    : %s   占比 %s",
                f"{vd['ssb']:>14,.0f}", f"{vd['ssb_pct']:>5.1f}%")
    logger.info("  组内平方和 SSW    : %s   占比 %s",
                f"{vd['ssw']:>14,.0f}", f"{vd['ssw_pct']:>5.1f}%")
    logger.info("  行业间 ROE 标准差 : %.2f", vd["between_std"])
    logger.info("  行业内 ROE 标准差 : %.2f（平均）", vd["within_std"])
    logger.info("  组内/组间 比值    : %.1f 倍",
                vd["within_std"] / vd["between_std"] if vd["between_std"] else 0)
    logger.info("-" * 76)
    logger.info("  根本原因：行业只能解释 ROE 方差的 %.1f%%，"
                "剩余 %.1f%% 来自行业内公司差异。",
                vd["ssb_pct"], vd["ssw_pct"])
    logger.info("  同一个行业里既有 ROE 50%% 的公司也有亏损公司，")
    logger.info("  行业只是'平均水平的标签'，无法说明具体某家公司好不好。")

    # ---------- 落盘 ----------
    out_dir = PROJECT_ROOT / "docs" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(exp1_rows).to_csv(
        out_dir / "exp1_industry_dummy_counts.csv", index=False, encoding="utf-8-sig")
    exp2.to_csv(out_dir / "exp2_two_models.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(exp3_rows + [{"特征集": "财务特征+OOF行业编码",
                               "模型": "随机森林", "R2均值": round(r2_oof, 4),
                               "R2标准差": round(std_oof, 4)}]).to_csv(
        out_dir / "exp3_industry_only.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([vd]).to_csv(
        out_dir / "exp4_variance_decomposition.csv", index=False, encoding="utf-8-sig")

    logger.info("=" * 76)
    logger.info("实验结果已保存到 docs/analysis/ 下的 4 个 CSV")
    logger.info("完整分析见 docs/notes/07-实验记录-行业特征负结果.md")
    logger.info("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
