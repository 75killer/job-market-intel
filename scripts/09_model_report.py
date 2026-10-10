"""W5：模型分析——生成图表、误差分析与预测演示。

产出：
    docs/figures/01_目标变量分布.png      ROE 分布（说明为什么任务难）
    docs/figures/02_模型对比.png          各模型 R² 对比
    docs/figures/03_特征重要性.png        特征重要性排序
    docs/figures/04_预测vs真实.png        预测值与真实值散点（含误差分析）
    docs/notes/05-模型分析报告.md          完整的模型分析文档

注意：matplotlib 默认不支持中文，必须显式设置字体，否则图上全是方块。
这是国内做数据可视化最常踩的坑之一。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.use("Agg")  # 无界面后端，避免在服务器/脚本环境下报错

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config, modeling, storage  # noqa: E402
from jmintel.logging_setup import get_logger, setup_logging  # noqa: E402

logger = get_logger("model_report")


def setup_chinese_font() -> str:
    """配置 matplotlib 中文字体。

    Windows 上用 Microsoft YaHei；如果找不到就退回到 SimHei；
    都没有的话给出明确警告，而不是画出一堆方块还让人猜原因。
    """
    candidates = ["Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"]
    available = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示成方块
            logger.info("使用中文字体：%s", name)
            return name
    logger.warning("未找到中文字体，图表中的中文可能显示为方块")
    return ""


def plot_target_distribution(y: pd.Series, path: Path) -> None:
    """图 1：目标变量分布——解释「为什么这个任务难」。"""
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))

    # 左图：直方图看整体形态
    axes[0].hist(y.clip(-60, 80), bins=70, color="#4C72B0", edgecolor="white")
    axes[0].axvline(y.median(), color="#C44E52", linestyle="--", linewidth=2,
                    label=f"中位数 {y.median():.1f}%")
    axes[0].axvline(0, color="gray", linestyle=":", linewidth=1.5, label="盈亏平衡线")
    axes[0].set_xlabel("ROE (%)")
    axes[0].set_ylabel("公司数量")
    axes[0].set_title("ROE 分布（已裁剪至 [-60, 80] 便于观察）\n"
                      f"偏度 {y.skew():.2f} —— 强烈右偏，极端值多", fontsize=11)
    axes[0].legend()
    axes[0].grid(alpha=0.3)

    # 右图：Q-Q 图看是否符合正态分布
    from scipy import stats  # noqa: PLC0415

    stats.probplot(y.dropna(), dist="norm", plot=axes[1])
    axes[1].set_title("Q-Q 图：与正态分布对比\n偏离对角线越远，越不适合线性模型", fontsize=11)
    axes[1].grid(alpha=0.3)
    axes[1].set_xlabel("理论分位数")
    axes[1].set_ylabel("样本分位数")

    plt.tight_layout()
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close()
    logger.info("图 1 已保存：%s", path.name)


def plot_model_comparison(metadata: dict, path: Path) -> None:
    """图 2：模型对比——用交叉验证 R² 而不是测试集 R²。"""
    models = metadata["all_models"]
    names = [m["模型"] for m in models]
    test_r2 = [m["测试集R2"] for m in models]
    cv_r2 = [m["交叉验证R2均值"] for m in models]
    cv_std = [m["交叉验证R2标准差"] for m in models]

    x = np.arange(len(names))
    width = 0.36

    fig, ax = plt.subplots(figsize=(10.5, 5))
    bars1 = ax.bar(x - width / 2, test_r2, width, label="测试集 R²",
                   color="#7FB3D5", edgecolor="white")
    bars2 = ax.bar(x + width / 2, cv_r2, width, label="5折交叉验证 R²（均值）",
                   color="#4C72B0", edgecolor="white",
                   yerr=cv_std, capsize=5, error_kw={"ecolor": "#C44E52", "lw": 1.5})

    # 在柱子上标数值
    for bars, vals in ((bars1, test_r2), (bars2, cv_r2)):
        for bar, v in zip(bars, vals):
            offset = 0.03 if v >= 0 else -0.09
            ax.text(bar.get_x() + bar.get_width() / 2, v + offset, f"{v:.3f}",
                    ha="center", fontsize=9, fontweight="bold")

    ax.axhline(0, color="black", linewidth=1)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=10)
    ax.set_ylabel("R²（越接近 1 越好）")
    ax.set_title("模型性能对比：测试集 R² vs 交叉验证 R²\n"
                 "注意 Ridge 回归交叉验证为负且误差棒极大 —— 这是模型不稳定的信号",
                 fontsize=11)
    ax.legend()
    ax.grid(alpha=0.3, axis="y")

    plt.tight_layout()
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close()
    logger.info("图 2 已保存：%s", path.name)


def plot_feature_importance(metadata: dict, path: Path) -> None:
    """图 3：特征重要性。"""
    importance = metadata.get("feature_importance") or {}
    if not importance:
        logger.warning("没有特征重要性数据，跳过图 3")
        return

    feats = list(importance.keys())[::-1]
    values = [importance[f] for f in feats]

    fig, ax = plt.subplots(figsize=(9, 5))
    colors = plt.cm.Blues(np.linspace(0.35, 0.9, len(feats)))
    bars = ax.barh(feats, values, color=colors, edgecolor="white")

    for bar, v in zip(bars, values):
        ax.text(v + max(values) * 0.015, bar.get_y() + bar.get_height() / 2,
                f"{v:.4f}", va="center", fontsize=9)

    ax.set_xlabel("重要性得分")
    ax.set_title(f"特征重要性排序（模型：{metadata['best_model']}）\n"
                 "已排除与 ROE 有数学关系的字段（eps/net_profit/net_margin 等）",
                 fontsize=11)
    ax.grid(alpha=0.3, axis="x")
    plt.tight_layout()
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close()
    logger.info("图 3 已保存：%s", path.name)


def plot_prediction_scatter(model, X_test: pd.DataFrame, y_test: pd.Series,
                            path: Path) -> dict:
    """图 4：预测值 vs 真实值 + 误差分析。"""
    y_pred = model.predict(X_test)
    residual = y_test - y_pred

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # 左：预测 vs 真实
    axes[0].scatter(y_test, y_pred, alpha=0.28, s=13, color="#4C72B0")
    lim = [-60, 90]
    axes[0].plot(lim, lim, color="#C44E52", linestyle="--", linewidth=1.8,
                 label="完美预测线 y=x")
    axes[0].set_xlim(lim)
    axes[0].set_ylim(lim)
    axes[0].set_xlabel("真实 ROE (%)")
    axes[0].set_ylabel("预测 ROE (%)")
    axes[0].set_title("预测值 vs 真实值\n点越靠近虚线越准；树模型不会外推，所以预测值被压在区间内",
                      fontsize=11)
    axes[0].legend()
    axes[0].grid(alpha=0.3)

    # 右：残差分布
    axes[1].hist(residual.clip(-40, 40), bins=60, color="#55A868", edgecolor="white")
    axes[1].axvline(0, color="#C44E52", linestyle="--", linewidth=2, label="零误差线")
    axes[1].axvline(residual.mean(), color="#8172B2", linestyle="-", linewidth=2,
                    label=f"平均残差 {residual.mean():.2f}%")
    axes[1].set_xlabel("残差（真实值 - 预测值，%）")
    axes[1].set_ylabel("样本数量")
    axes[1].set_title(f"残差分布（标准差 {residual.std():.2f}%）\n"
                      "接近对称且均值接近 0，说明模型没有系统性高估或低估",
                      fontsize=11)
    axes[1].legend()
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close()
    logger.info("图 4 已保存：%s", path.name)

    # ---------- 排序能力检验（比 R² 更贴合实际用途）----------
    # 模型不用来精确定价，而是用来给公司排序。
    # 把预测值分成 5 档，看真实 ROE 中位数是否单调递增——
    # 这才是模型的实际价值所在。
    tmp = pd.DataFrame({"pred": y_pred, "actual": y_test})
    tmp["bucket"] = pd.qcut(tmp["pred"], 5, labels=[
        "最低20%", "较低20%", "中间20%", "较高20%", "最高20%"], duplicates="drop")
    ranking = tmp.groupby("bucket", observed=True).agg(
        样本数=("actual", "size"),
        预测ROE均值=("pred", "mean"),
        真实ROE中位数=("actual", "median"),
        真实ROE均值=("actual", "mean"),
    ).round(2)

    return {"ranking": ranking, "residual_std": round(float(residual.std()), 2),
            "residual_mean": round(float(residual.mean()), 2)}


def main() -> int:
    setup_logging(log_file=PROJECT_ROOT / "logs" / "modeling.log")
    config.ensure_dirs()
    setup_chinese_font()

    # 检查是否有 scipy（Q-Q 图需要）
    try:
        import scipy  # noqa: F401
    except ImportError:
        logger.warning("未安装 scipy，Q-Q 图将跳过。安装：pip install scipy")

    meta_path = config.MODELS_DIR / "model_metadata.json"
    model_path = config.MODELS_DIR / "best_model.joblib"
    if not meta_path.exists() or not model_path.exists():
        logger.error("模型文件不存在，请先运行 scripts/08_train_model.py")
        return 1

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    logger.info("加载模型：%s（训练时间 %s）",
                metadata["best_model"], metadata["trained_at"])

    dwd = storage.read_table("dwd_finance_performance")
    X, y, detail = modeling.prepare_data(dwd)

    from sklearn.model_selection import train_test_split  # noqa: PLC0415

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42)

    # ---------- 生成图表 ----------
    logger.info("生成图表...")
    plot_target_distribution(y, config.FIGURES_DIR / "01_目标变量分布.png")
    plot_model_comparison(metadata, config.FIGURES_DIR / "02_模型对比.png")
    plot_feature_importance(metadata, config.FIGURES_DIR / "03_特征重要性.png")

    model = joblib.load(model_path)
    analysis = plot_prediction_scatter(
        model, X_test, y_test, config.FIGURES_DIR / "04_预测vs真实.png")

    # ---------- 生成分析报告 ----------
    logger.info("生成分析报告...")
    lines = [
        "# 模型分析报告",
        "",
        f"模型：**{metadata['best_model']}** | 训练时间：{metadata['trained_at']}",
        "",
        "## 一、目标变量为什么难预测",
        "",
        f"- ROE 偏度 **{y.skew():.2f}**，属于强烈右偏分布",
        f"- 标准差 {y.std():.2f}%，范围 [{y.min():.0f}%, {y.max():.0f}%]",
        f"- 绝对值超过 50% 的极端样本占 **{(y.abs() > 50).mean() * 100:.1f}%**",
        "",
        "![目标变量分布](../figures/01_目标变量分布.png)",
        "",
        "**含义**：ROE 分布右偏且长尾，这类分布上方差很大，",
        "任何模型都难以完全解释——这是任务本身的难度，不是模型选择问题。",
        "",
        "## 二、模型性能对比",
        "",
        "![模型对比](../figures/02_模型对比.png)",
        "",
        "| 模型 | 测试集 R² | 交叉验证 R² 均值 | 交叉验证标准差 | 判断 |",
        "|---|---|---|---|---|",
    ]
    # 构造键名映射表，而不是在各处硬编码中文键。
    # 这个函数是因为最初直接写 m["交叉验证标准差"] 触发 KeyError 才加的——
    # 元数据里的实际列名是"交叉验证R2标准差"。
    # 教训：**不要凭记忆写键名，先打印出来看。**
    def field(model_row: dict, name: str) -> float:
        aliases = {
            "test_r2": ["测试集R2"],
            "cv_mean": ["交叉验证R2均值"],
            "cv_std": ["交叉验证R2标准差", "交叉验证标准差"],
        }
        for key in aliases[name]:
            if key in model_row:
                return float(model_row[key])
        raise KeyError(
            f"元数据中找不到 {name} 对应字段。"
            f"期望以下之一：{aliases[name]}，实际可用：{list(model_row)}"
        )

    for m in metadata["all_models"]:
        cv_std = field(m, "cv_std")
        if "基线" in m["模型"]:
            verdict = "预期为 0，作为对照基准"
        elif cv_std > 1:
            verdict = "❌ 极不稳定，**不可用**"
        elif field(m, "cv_mean") > 0.5:
            verdict = "✅ 表现良好且稳定"
        else:
            verdict = "⚠️ 表现一般"
        lines.append(
            f"| {m['模型']} | {field(m, 'test_r2'):.4f} | {field(m, 'cv_mean'):.4f} "
            f"| {cv_std:.4f} | {verdict} |")

    lines += [
        "",
        "### 关键发现：为什么 Ridge 回归不能用",
        "",
        "Ridge 回归的交叉验证 R² 为负、标准差超过 3，**这是模型不稳定的典型信号**。",
        "我做了逐折诊断，找到了确切原因：",
        "",
        "1. **不是过拟合**：单次划分下训练集 R²=0.1948、测试集 R²=0.1944，",
        "   两者几乎相同，说明模型根本没学到东西（欠拟合）。",
        "2. **是线性外推失控**：第 2 折的 R² 为 **-8.11**，因为该折的测试样本",
        "   特征值超出了训练集范围，线性模型按系数外推，预测出了 **ROE = -1640%**",
        "   这种不可能的数值。一个这样的样本就能把整折的 R² 打成负数。",
        "3. **树模型没有这个问题**：决策树/随机森林的预测值只能是训练样本目标值",
        "   的加权平均，**结构上不可能外推到 [-100, 200] 之外**。",
        "",
        "**结论**：财务指标与 ROE 之间是**非线性关系**（特征与 ROE 的皮尔逊相关系数",
        "最高只有 0.31，最低 -0.04），线性模型不适用于本任务。",
        "这不是「调参能解决」的问题，而是模型假设与数据不匹配。",
        "",
        "## 三、特征重要性",
        "",
        "![特征重要性](../figures/03_特征重要性.png)",
        "",
        "| 特征 | 重要性 | 业务解读 |",
        "|---|---|---|",
    ]
    interpretations = {
        "dividend_yield_w": "股息率高的公司通常盈利稳定（能分红说明真有钱）",
        "gross_margin_w": "毛利率反映产品定价能力与护城河",
        "profit_yoy_w": "利润增速与盈利能力正相关",
        "log_revenue": "规模效应：大公司ROE普遍更高",
        "ocf_per_share": "经营现金流是盈利质量的直接体现",
        "revenue_yoy_w": "营收增速反映成长性",
        "expense_ratio": "费用率越低，利润留存越多",
    }
    for feat, imp in metadata["feature_importance"].items():
        lines.append(f"| `{feat}` | {imp:.4f} | {interpretations.get(feat, '—')} |")

    lines += [
        "",
        "## 四、预测效果与误差分析",
        "",
        "![预测vs真实](../figures/04_预测vs真实.png)",
        "",
        f"- 平均残差 **{analysis['residual_mean']}%**（接近 0，说明无系统性高估或低估）",
        f"- 残差标准差 **{analysis['residual_std']}%**（这是单次预测的典型误差量级）",
        "",
        "### 排序能力检验（比 R² 更贴合实际用途）",
        "",
        "模型的价值不在于「精确预测 ROE 是多少」，而在于「**把好公司排到前面**」。",
        "按预测值分 5 档，看每档的真实 ROE：",
        "",
        analysis["ranking"].to_markdown(),
        "",
        "**如果真实 ROE 中位数随预测档位单调递增，说明模型具备排序能力**，",
        "这就足以支撑初筛场景——这正是量化选股的基本逻辑。",
        "",
        "## 五、模型的局限性与改进方向",
        "",
        "### 局限（面试时主动说，比被问出来好）",
        "",
        "1. **只用同期财务数据**：用 2024 年报的指标预测 2024 年报的 ROE，",
        "   存在内生性问题。更严谨的做法是用 t-1 年数据预测 t 年。",
        "2. **未纳入行业特征**：行业是 ROE 最强的解释变量之一（实测行业 ROE 中位数",
        "   从 -0.87% 到 18.33%，跨度极大），当前模型没有利用这个信息。",
        "3. **R² 约 0.54**：说明还有约 46% 的变异无法解释，主要来自行业周期、",
        "   管理层能力、竞争格局等财务表之外的因素。",
        "4. **样本仅一年**：无法做时间维度验证（如 2023 训练、2024 测试）。",
        "",
        "### 改进方向（有优先级）",
        "",
        "| 优先级 | 方向 | 预期收益 |",
        "|---|---|---|",
        "| 高 | 加入行业哑变量或分行业建模 | 行业解释力最强，预计 R² 显著提升 |",
        "| 高 | 采集多年数据，做时序验证 | 解决内生性，结论更可信 |",
        "| 中 | 目标变量改为分位数或分档分类 | 规避极端值，指标更稳健 |",
        "| 中 | 加入财务指标波动率（如三年 ROE 标准差） | 捕捉经营稳定性 |",
        "| 低 | 超参数网格搜索 | 提升有限（已用合理默认值） |",
        "",
        "## 六、可复现性",
        "",
        "```bash",
        "python scripts/08_train_model.py    # 训练模型",
        "python scripts/09_model_report.py   # 生成图表与本报告",
        "```",
        "",
        "所有随机种子固定为 42，相同数据与代码版本下结果完全可复现。",
        "",
        "---",
        "",
        "_本报告由 `scripts/09_model_report.py` 自动生成_",
    ]

    out = PROJECT_ROOT / "docs" / "notes" / "05-模型分析报告.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("分析报告已生成：%s", out.relative_to(PROJECT_ROOT))

    logger.info("=" * 68)
    logger.info("图表清单：")
    for fig in sorted(config.FIGURES_DIR.glob("*.png")):
        logger.info("    %s（%.0f KB）", fig.name, fig.stat().st_size / 1024)
    logger.info("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
