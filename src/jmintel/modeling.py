"""机器学习建模：用财务指标预测公司盈利能力（ROE）。

这个任务为什么值得做？
—————————————————————————————————————————————
业务问题：**一家公司的盈利能力，多大程度上能被它的财务结构解释？**
         如果模型能准确预测 ROE，说明我们找到了影响盈利的关键财务因子；
         如果预测不准，说明盈利能力主要由模型外的因素决定（如行业周期、管理能力）。

这类"用一组财务指标预测另一个财务指标"的任务，在风控、投研、信用评级里
都是标准做法，是真实业务场景，不是玩具题目。

【极其重要：如何避免"看似很高"的假精度】
用财务指标预测 ROE 时，最容易犯的错误是**把 ROE 的组成部分当成特征**。
例如：
    net_margin = net_profit / revenue        ← 与 ROE 高度数学相关
    eps                                      ← 与 ROE 直接成正比
把它们放进特征，模型 R² 会轻松到 0.9 以上，但这个结果**毫无意义**——
因为你不是在"预测"，而是在"用另一种方式计算 ROE"。

本模块的做法：**只用与 ROE 无直接数学关系的特征**，并把这个原则
写在代码和文档里。宁可 R² 低一点，也要保证结论真实可信。
这是我认为比"刷高指标"更重要的工程原则。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .logging_setup import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------
# 特征选择：只保留与 ROE 没有直接数学关系的指标
#
# 被排除的字段及原因（这一节是模型可信度的关键）：
#   eps            → 每股收益，与 ROE 分子同源，直接泄漏
#   bps            → 每股净资产，与 ROE 分母同源，直接泄漏
#   net_profit     → ROE 的分子，直接泄漏
#   net_margin     → = 净利润/营收，与 ROE 强相关
#   roe / roe_w    → 就是目标变量本身
#   ocf_to_eps     → 分母含 eps，间接泄漏
#   roe_tier       → 由 ROE 分档而来，泄漏
# ---------------------------------------------------------
FEATURE_COLUMNS: list[str] = [
    "log_revenue",       # 规模（对数化，缓解长尾）
    "revenue_yoy_w",     # 营收增速
    "profit_yoy_w",      # 净利增速
    "gross_margin_w",    # 毛利率（注意：净利率被排除，毛利率保留但需谨慎解读）
    "expense_ratio",     # 费用率 = 毛利率 - 净利率
    "dividend_yield_w",  # 股息率
    "ocf_per_share",     # 每股经营现金流（绝对值，不含 eps）
]

TARGET_COLUMN = "roe_w"

# 因数学泄漏而**禁止**作为特征的字段（写在代码里，防止以后误加）
LEAKY_COLUMNS: set[str] = {
    "roe", "roe_w", "roe_tier", "eps", "bps", "net_profit", "net_profit_yi",
    "net_margin", "net_margin_w", "ocf_to_eps", "log_abs_profit", "flag_loss",
    "flag_negative_equity",
}


@dataclass
class ModelResult:
    """单个模型的评估结果。"""

    name: str
    r2: float
    rmse: float
    mae: float
    cv_r2_mean: float
    cv_r2_std: float
    n_train: int
    n_test: int
    model: object | None = None
    feature_importance: dict[str, float] = field(default_factory=dict)


def prepare_data(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """准备建模数据：筛选样本 → 选特征 → 处理缺失。

    返回 (特征矩阵 X, 目标向量 y, 原始明细行)
    """
    # ---------- 样本筛选 ----------
    # 只锁年报：半年报 ROE 只有年报的一半，混在一起会把目标变量搞乱
    data = df[df["report_date"].astype(str).str.startswith("2024-12-31")].copy()
    logger.info("锁定 2024 年报：%d 条记录", len(data))

    data = data[data["is_analyzable"]].copy()
    logger.info("筛选 is_analyzable：%d 条", len(data))

    data = data[data["is_st"] == False].copy()  # noqa: E712
    logger.info("剔除 ST 公司：%d 条", len(data))

    data = data[data[TARGET_COLUMN].notna()].copy()
    logger.info("剔除 ROE 缺失：%d 条", len(data))

    # ---------- 特征泄漏检查（重要的防御性编程）----------
    leaked = [c for c in FEATURE_COLUMNS if c in LEAKY_COLUMNS]
    if leaked:
        raise ValueError(
            f"检测到数据泄漏特征：{leaked}。"
            f"这些字段与目标 ROE 存在直接数学关系，会让模型指标虚高。"
            f"请从 FEATURE_COLUMNS 中移除。"
        )
    missing_features = [c for c in FEATURE_COLUMNS if c not in data.columns]
    if missing_features:
        raise ValueError(f"缺少特征列：{missing_features}")

    X = data[FEATURE_COLUMNS].copy()
    y = data[TARGET_COLUMN].copy()

    # ---------- 缺失值统计 ----------
    missing_ratio = X.isna().mean().sort_values(ascending=False)
    logger.info("特征缺失率（前 5）：")
    for col, ratio in missing_ratio.head(5).items():
        logger.info("    %-20s %.1f%%", col, ratio * 100)

    # 缺失值用中位数填充（放在 Pipeline 里做，避免用测试集信息污染训练集）
    logger.info("最终样本：%d 条 × %d 个特征", len(X), X.shape[1])
    return X, y, data


def build_models() -> dict[str, Pipeline]:
    """构造候选模型。

    为什么要有"基线模型"？
    因为一个 R²=0.3 的模型，如果没有基线对比，你无法判断它是好是坏。
    DummyRegressor 永远预测训练集均值，它的 R² 必然是 0 左右——
    这是判断"模型是否学到了东西"的标尺。
    """
    return {
        # 基线：永远预测均值。R² 应该 ≈ 0，作为对照
        "基线(预测均值)": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", DummyRegressor(strategy="mean")),
        ]),
        # 线性模型 + L2 正则：可解释性强，系数能直接解读
        "Ridge回归": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),   # 线性模型需要标准化，否则量纲影响系数
            ("model", Ridge(alpha=1.0, random_state=42)),
        ]),
        # 树模型：能捕捉非线性关系，且不需要标准化
        "随机森林": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", RandomForestRegressor(
                n_estimators=300, max_depth=8, min_samples_leaf=5,
                random_state=42, n_jobs=-1)),
        ]),
        # 梯度提升：通常比随机森林精度更高，是表格数据的强基线
        "梯度提升": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("model", GradientBoostingRegressor(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                random_state=42)),
        ]),
    }


def evaluate_model(
    name: str,
    pipeline: Pipeline,
    X_train: pd.DataFrame, X_test: pd.DataFrame,
    y_train: pd.Series, y_test: pd.Series,
) -> ModelResult:
    """训练并评估单个模型。"""
    # ---------- 训练 ----------
    pipeline.fit(X_train, y_train)

    # ---------- 测试集评估 ----------
    y_pred = pipeline.predict(X_test)
    r2 = r2_score(y_test, y_pred)
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    mae = float(mean_absolute_error(y_test, y_pred))

    # ---------- 5 折交叉验证 ----------
    # 为什么不能只看单次划分的结果？
    # 因为单次 train_test_split 的结果受"划分运气"影响很大——
    # 恰好把好预测的样本分到测试集，R² 就虚高。
    # 交叉验证跑 5 次取平均和标准差，才能反映模型的真实稳定性。
    cv = KFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(pipeline, X_train, y_train, cv=cv, scoring="r2")

    # ---------- 特征重要性 ----------
    importance: dict[str, float] = {}
    model = pipeline.named_steps["model"]
    if hasattr(model, "feature_importances_"):
        importance = dict(zip(FEATURE_COLUMNS, model.feature_importances_.round(4)))
    elif hasattr(model, "coef_"):
        importance = dict(zip(FEATURE_COLUMNS, np.abs(model.coef_).round(4)))
    importance = dict(sorted(importance.items(), key=lambda kv: -kv[1]))

    return ModelResult(
        name=name, r2=round(r2, 4), rmse=round(rmse, 2), mae=round(mae, 2),
        cv_r2_mean=round(float(cv_scores.mean()), 4),
        cv_r2_std=round(float(cv_scores.std()), 4),
        n_train=len(X_train), n_test=len(X_test),
        model=pipeline, feature_importance=importance,
    )


def train_and_compare(df: pd.DataFrame) -> tuple[list[ModelResult], pd.DataFrame]:
    """完整建模流程：准备数据 → 训练多个模型 → 对比 → 返回最佳模型。"""
    logger.info("=" * 68)
    logger.info("开始建模：用财务指标预测 ROE")
    logger.info("=" * 68)
    logger.info("特征（%d 个）：%s", len(FEATURE_COLUMNS), "、".join(FEATURE_COLUMNS))
    logger.info("目标变量：%s（加权平均净资产收益率）", TARGET_COLUMN)

    X, y, _ = prepare_data(df)
    if len(X) < 100:
        raise RuntimeError(f"样本量不足（{len(X)} 条），无法建模")

    # 划分训练集/测试集：8:2
    # random_state 固定，保证结果可复现
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    logger.info("数据划分：训练 %d 条 / 测试 %d 条", len(X_train), len(X_test))

    results: list[ModelResult] = []
    for name, pipeline in build_models().items():
        logger.info("-" * 68)
        logger.info("训练模型：%s", name)
        try:
            result = evaluate_model(name, pipeline, X_train, X_test, y_train, y_test)
        except Exception as exc:  # noqa: BLE001
            logger.error("模型 %s 训练失败：%s", name, exc)
            continue
        results.append(result)
        logger.info("    测试集 R²=%.4f | RMSE=%.2f | MAE=%.2f",
                    result.r2, result.rmse, result.mae)
        logger.info("    5折交叉验证 R²=%.4f (±%.4f)",
                    result.cv_r2_mean, result.cv_r2_std)

    # 汇总对比表
    comparison = pd.DataFrame([{
        "模型": r.name,
        "测试集R2": r.r2,
        "交叉验证R2均值": r.cv_r2_mean,
        "交叉验证R2标准差": r.cv_r2_std,
        "RMSE": r.rmse,
        "MAE": r.mae,
        "训练样本": r.n_train,
        "测试样本": r.n_test,
    } for r in results]).sort_values("交叉验证R2均值", ascending=False)

    logger.info("=" * 68)
    logger.info("模型对比结果（按交叉验证 R² 排序）")
    logger.info("=" * 68)
    print(comparison.to_string(index=False))
    logger.info("=" * 68)

    # 保存模型列表（供外部取最佳模型）
    best = max(results, key=lambda r: r.cv_r2_mean)
    logger.info("最佳模型：%s（交叉验证 R²=%.4f）", best.name, best.cv_r2_mean)

    # 特征重要性
    if best.feature_importance:
        logger.info("-" * 68)
        logger.info("最佳模型的特征重要性排序：")
        for feat, imp in best.feature_importance.items():
            bar = "█" * int(imp / max(best.feature_importance.values()) * 30) \
                if max(best.feature_importance.values()) > 0 else ""
            logger.info("    %-20s %.4f  %s", feat, imp, bar)

    return results, comparison
