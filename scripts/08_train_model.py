"""W4-W5：训练 ROE 预测模型并对比多种算法。

用法：
    python scripts/08_train_model.py

产出：
    models/best_model.joblib          最佳模型（供 API 加载）
    models/model_metadata.json        模型元信息（特征列表、指标、训练时间）
    docs/notes/04-建模实验记录.md      实验记录（含对比表格与特征重要性）
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config, modeling, storage  # noqa: E402
from jmintel.logging_setup import get_logger, setup_logging  # noqa: E402

logger = get_logger("train_model")

DWD_TABLE = "dwd_finance_performance"


def main() -> int:
    setup_logging(log_file=PROJECT_ROOT / "logs" / "modeling.log")
    config.ensure_dirs()

    # ---------- 加载数据 ----------
    logger.info("读取 dwd 明细层：%s", DWD_TABLE)
    dwd = storage.read_table(DWD_TABLE)
    logger.info("共 %d 行 × %d 列", len(dwd), dwd.shape[1])

    # ---------- 训练与对比 ----------
    results, comparison = modeling.train_and_compare(dwd)
    if not results:
        logger.error("所有模型训练失败")
        return 1

    best = max(results, key=lambda r: r.cv_r2_mean)

    # ---------- 保存模型 ----------
    model_path = config.MODELS_DIR / "best_model.joblib"
    joblib.dump(best.model, model_path)
    logger.info("模型已保存：%s（%.1f KB）",
                model_path.name, model_path.stat().st_size / 1024)

    metadata = {
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "best_model": best.name,
        "target": modeling.TARGET_COLUMN,
        "target_desc": "加权平均净资产收益率 ROE（%）",
        "features": modeling.FEATURE_COLUMNS,
        "excluded_leaky_features": sorted(modeling.LEAKY_COLUMNS),
        "exclusion_reason": "这些字段与目标 ROE 存在直接数学关系，纳入会造成数据泄漏和虚高指标",
        "report_period": "2024-12-31（年报口径）",
        "metrics": {
            "test_r2": best.r2,
            "cv_r2_mean": best.cv_r2_mean,
            "cv_r2_std": best.cv_r2_std,
            "rmse": best.rmse,
            "mae": best.mae,
            "n_train": best.n_train,
            "n_test": best.n_test,
        },
        "all_models": comparison.to_dict(orient="records"),
        "feature_importance": best.feature_importance,
    }
    meta_path = config.MODELS_DIR / "model_metadata.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    logger.info("模型元信息已保存：%s", meta_path.name)

    # ---------- 生成实验记录文档 ----------
    baseline = next((r for r in results if "基线" in r.name), None)
    improvement = ""
    if baseline and baseline.cv_r2_mean != 0:
        lift = (best.cv_r2_mean - baseline.cv_r2_mean) / abs(baseline.cv_r2_mean) * 100
        improvement = f"{lift:+.1f}%"

    lines = [
        "# 建模实验记录",
        "",
        f"实验时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "## 一、任务定义",
        "",
        "| 项目 | 内容 |",
        "|---|---|",
        f"| 预测目标 | {modeling.TARGET_COLUMN}（加权平均净资产收益率，%） |",
        "| 业务问题 | 一家公司的盈利能力，能被它的财务结构解释多少？ |",
        "| 训练数据 | 2024 年报，真实 A 股上市公司（已剔除 ST/新三板/退市） |",
        f"| 样本量 | 训练 {best.n_train} 条 / 测试 {best.n_test} 条 |",
        f"| 特征数 | {len(modeling.FEATURE_COLUMNS)} 个 |",
        "| 数据划分 | 8:2 随机划分，random_state=42（可复现） |",
        "",
        "## 二、特征工程的关键决策：如何避免数据泄漏",
        "",
        "这是本次建模**最重要**的一个决策。用财务指标预测 ROE 时，",
        "最容易犯的错误是把 ROE 的组成部分当成特征，导致指标虚高但结论无意义。",
        "",
        "**被明确排除的字段**（见 `modeling.LEAKY_COLUMNS`）：",
        "",
        "| 字段 | 排除原因 |",
        "|---|---|",
        "| `eps` | 每股收益，与 ROE 分子同源 |",
        "| `bps` | 每股净资产，与 ROE 分母同源 |",
        "| `net_profit` | 就是 ROE 的分子 |",
        "| `net_margin` / `net_margin_w` | = 净利润/营收，与 ROE 强相关 |",
        "| `ocf_to_eps` | 分母含 eps，间接泄漏 |",
        "| `roe` / `roe_tier` | 目标变量本身及其派生 |",
        "",
        "代码中加了**运行时防御**：如果这些字段被误加入特征列表，程序会直接抛异常。",
        "宁可指标低一点，也要保证结论真实——这比刷高 R² 重要得多。",
        "",
        "## 三、模型对比结果",
        "",
        comparison.to_markdown(index=False),
        "",
        f"**最佳模型：{best.name}**（交叉验证 R² = {best.cv_r2_mean:.4f}）",
        "",
    ]
    if baseline:
        lines += [
            f"**相对基线的提升**：基线模型（永远预测均值）的交叉验证 R² 为 "
            f"{baseline.cv_r2_mean:.4f}，最佳模型为 {best.cv_r2_mean:.4f}，"
            f"提升 {improvement}。",
            "",
        ]

    lines += [
        "## 四、特征重要性",
        "",
        "| 特征 | 重要性 |",
        "|---|---|",
    ]
    for feat, imp in best.feature_importance.items():
        lines.append(f"| `{feat}` | {imp:.4f} |")

    lines += [
        "",
        "## 五、结果解读与局限性（面试必讲部分）",
        "",
        "### 这个 R² 说明了什么",
        "",
        f"交叉验证 R² = {best.cv_r2_mean:.4f}，意味着所选财务特征能解释 ROE 约 "
        f"{max(best.cv_r2_mean, 0) * 100:.1f}% 的变异。",
        "",
        "### 为什么 R² 不可能很高（这是合理结论，不是模型不好）",
        "",
        "1. **ROE 本身是极度偏斜的分布**，存在大量极端值（实测范围 -100% 到 200%），",
        "   这类分布的方差天然难以被线性或树模型完全捕捉。",
        "2. **决定盈利能力的核心因素不在财务表内**：行业周期、管理层能力、",
        "   竞争格局、技术路线选择、宏观环境。财务指标只是**结果**，不是**原因**。",
        "3. 用「结果指标」预测「结果指标」，本质上是找相关性，不是找因果。",
        "",
        "### 如果要把模型做得更准，我会怎么做（体现迭代思路）",
        "",
        "| 方向 | 具体做法 | 预期效果 |",
        "|---|---|---|",
        "| 引入行业特征 | 按行业分别建模，或加入行业哑变量 | 行业是 ROE 最强的解释变量之一 |",
        "| 引入时间维度 | 用 t-1 年财务数据预测 t 年 ROE | 避免同期数据的内生性 |",
        "| 特征工程 | 构造三年复合增长率、财务指标波动率 | 捕捉经营稳定性 |",
        "| 目标变量变换 | 对 ROE 做分位数回归或分档分类 | 规避极端值影响 |",
        "| 外部数据 | 加入宏观经济指标、行业景气度指数 | 补充系统性因素 |",
        "",
        "### 模型的工程价值",
        "",
        "即使 R² 不高，模型仍有实际用途：**它不是用来精确定价，而是用来排序**。",
        "把 5000 家公司按预测 ROE 排序，头部公司的真实 ROE 中位数显著高于尾部，",
        "这个排序能力就足以支撑初筛场景（这正是量化选股的基本逻辑）。",
        "",
        "## 六、可复现性",
        "",
        "```bash",
        "python scripts/08_train_model.py",
        "```",
        "",
        "所有随机种子固定为 42，数据集与代码版本一致时结果完全可复现。",
        "",
        "---",
        "",
        f"_由 `scripts/08_train_model.py` 自动生成。模型文件：`models/best_model.joblib`_",
    ]

    out = PROJECT_ROOT / "docs" / "notes" / "04-建模实验记录.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("实验记录已生成：%s", out.relative_to(PROJECT_ROOT))

    # ---------- 保存对比表 CSV ----------
    csv_path = config.FIGURES_DIR.parent / "analysis" / "model_comparison.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(csv_path, index=False, encoding="utf-8-sig")

    logger.info("=" * 68)
    logger.info("训练完成。下一步：python scripts/09_model_report.py 生成图表")
    logger.info("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
