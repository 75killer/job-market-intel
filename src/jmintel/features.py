"""特征工程：把"原始字段"变成"可分析和可建模的特征"。

为什么需要这一层？
—————————————————————————————————————————————
ods 层的字段是"财报原始数值"，人和模型都不好直接用：
  - 单位不统一（元 / 亿元 / 百分比混在一起）
  - 数值分布极度偏斜（ROE 从 -2303% 到 +2684%），模型会被极端值带跑
  - 缺少业务语义（"ROE=15" 到底算好还是差？要看分位数，不是看绝对值）

本模块做三件事：
  1. **构造比率类特征**——比率比绝对值更可比（不同规模的公司能放在一起比）
  2. **分位数分箱**——把连续值转成有业务含义的等级（优秀/良好/一般/亏损）
  3. **稳健化处理**——对极端值做截尾（winsorize），让模型不被少数异常值主导

关于截尾的重要说明：
  我们不删除极端值，也不修改原始数据，而是**新增一列**截尾后的值。
  这样既保留原始数据的可追溯性，又能让建模更稳健。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .logging_setup import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------
# 截尾边界：依据财报常识设定，而不是随便拍的
#   ROE      : -100% ~ 200%（低于 -100% 意味着净资产已近乎亏光，高于 200% 属于极小净资产导致）
#   净利率   : -200% ~ 100%（净利率超过 100% 通常是投资收益等非经营性因素）
#   增长率   : -200% ~ 500%（增长率没有上限，但超过 500% 多是基数极低导致）
# ---------------------------------------------------------
WINSOR_BOUNDS: dict[str, tuple[float, float]] = {
    "roe": (-100.0, 200.0),
    "net_margin": (-200.0, 100.0),
    "revenue_yoy": (-200.0, 500.0),
    "profit_yoy": (-500.0, 1000.0),
    "gross_margin": (-100.0, 100.0),
    "dividend_yield": (0.0, 30.0),
}


def build_ratio_features(df: pd.DataFrame) -> pd.DataFrame:
    """构造比率类与规模类特征。

    比率特征的价值：把"规模不同"的公司变得可比。
        净利润 10 亿听起来很多，但如果营收 1000 亿，净利率只有 1%，其实是薄利。
        所以"净利率"比"净利润"更能反映盈利能力。
    """
    out = df.copy()

    # ---------- 规模类（对数化，缓解长尾）----------
    # 营收跨度从 0.001 亿到 3000 亿，直接放进模型会被巨头主导，
    # 取对数后分布接近正态，这是处理"长尾分布"的标准做法。
    if "revenue_yi" in out.columns:
        safe_rev = out["revenue_yi"].where(out["revenue_yi"] > 0)
        out["log_revenue"] = np.log10(safe_rev).round(4)

    if "net_profit_yi" in out.columns:
        # 净利润可能为负，取对数前先取绝对值再还原符号
        abs_profit = out["net_profit_yi"].abs().where(out["net_profit_yi"].abs() > 0)
        out["log_abs_profit"] = (np.log10(abs_profit) *
                                 np.sign(out["net_profit_yi"])).round(4)

    # ---------- 比率类 ----------
    # 人均指标缺失（没采员工数），这里用"资产周转"类比率代替
    # 资产负债相关比率由资产负债表提供，本表暂不重复计算

    # 毛利率与净利率的差 = 费用率（销售/管理/财务/税），衡量"费用控制能力"
    if {"gross_margin", "net_margin"}.issubset(out.columns):
        out["expense_ratio"] = (out["gross_margin"] - out["net_margin"]).round(4)

    # 现金流质量：每股经营现金流 / 每股收益
    # 大于 1 说明"赚到的是真金白银"，小于 1 说明利润可能停留在应收账款上
    if {"ocf_per_share", "eps"}.issubset(out.columns):
        safe_eps = out["eps"].where(out["eps"] > 0)
        out["ocf_to_eps"] = (out["ocf_per_share"] / safe_eps).round(4)

    # ---------- 成长性综合分 ----------
    # 营收增长和利润增长各占一半权重，都缺失时为 NaN
    yoy_cols = [c for c in ("revenue_yoy", "profit_yoy") if c in out.columns]
    if yoy_cols:
        out["growth_score"] = out[yoy_cols].mean(axis=1).round(4)

    return out


def winsorize(df: pd.DataFrame,
              bounds: dict[str, tuple[float, float]] | None = None) -> pd.DataFrame:
    """对极端值做截尾，生成 `<列名>_w` 新列（w = winsorized）。

    关键：**不覆盖原列**。原数据永远保留，方便追溯和对照。
    """
    bounds = bounds or WINSOR_BOUNDS
    out = df.copy()
    stats: list[str] = []
    for col, (lo, hi) in bounds.items():
        if col not in out.columns:
            continue
        out[f"{col}_w"] = out[col].clip(lower=lo, upper=hi).round(4)
        # 注意：必须区分"非空值数"和"被改动值数"，两者完全不同。
        # 早期版本这里写的是 (out[col] != out[f"{col}_w"]).sum()，
        # 结果在 NaN 参与比较时行为反直觉，日志里显示成
        # "revenue_yoy(23135)"，让人误以为 99% 的数据都被截尾了。
        # 实测真实越界比例只有 0.3%~0.6%。日志必须让人看懂，否则会误导判断。
        valid = out[col].notna()
        clipped = ((out[col] < lo) | (out[col] > hi)) & valid
        stats.append(f"{col}: 非空{int(valid.sum())}条/越界{int(clipped.sum())}条")
    logger.info("截尾处理（越界值被压到边界，原列保留不变）：")
    for s in stats:
        logger.info("    %s", s)
    return out


def add_quality_flags(df: pd.DataFrame) -> pd.DataFrame:
    """添加数据质量与风险标记。

    设计原则：**标记而不删除**。
    原因有两个：
      1. "哪些公司异常"本身就是分析素材（比如 ST 公司的财务特征）
      2. 删除会让样本量与财报披露数量对不上，无法向别人解释口径
    """
    out = df.copy()

    # ---------- 市场口径 ----------
    market = out["market"].astype(str)
    out["is_a_share"] = market.str.contains("上交所|深交所|北京证券交易所", na=False)
    out["is_neeq"] = market.str.contains("新三板|老三板", na=False)
    # ST / 风险警示板：退市风险警示，财务通常很差，分析时要能单独拎出来
    out["is_st"] = (market.str.contains("风险警示", na=False) |
                    out["stock_name"].astype(str).str.contains("ST|退", na=False))
    out["is_delisted"] = out["stock_name"].astype(str).str.contains("摘牌|退市", na=False)

    # ---------- 数据可用性 ----------
    out["has_industry"] = (out["industry"].notna() &
                           (out["industry"].astype(str).str.strip() != ""))
    key_cols = [c for c in ("revenue", "net_profit", "roe") if c in out.columns]
    out["has_key_metrics"] = out[key_cols].notna().all(axis=1) if key_cols else True

    # 可分析口径：A股 + 有行业 + 有关键指标 + 未退市
    out["is_analyzable"] = (out["is_a_share"] & out["has_industry"] &
                            out["has_key_metrics"] & ~out["is_delisted"])

    # 建模口径：可分析 + ROE 非极端（截尾后与原值一致）
    if "roe_w" in out.columns:
        out["is_model_ready"] = (out["is_analyzable"] & out["roe"].notna() &
                                 out["revenue_yi"].gt(0))
    else:
        out["is_model_ready"] = out["is_analyzable"]

    logger.info("口径标记：可分析 %d 条 / 建模可用 %d 条（总 %d 条）",
                int(out["is_analyzable"].sum()),
                int(out["is_model_ready"].sum()), len(out))

    # ---------- 统一布尔类型（一个真实踩坑的修复）----------
    # 背景：ods 表来自 CSV，布尔列存成 "True"/"False" 字符串。
    # 用字符串列做 bool.sum() 时，pandas 会把它们**拼接**而不是计数，
    # 于是算出了"亏损占比 111%"这种不可能的结果。
    # 这类 bug 不报错、只是数字悄悄错掉，所以必须显式归一化类型。
    bool_cols = ["is_a_share", "is_neeq", "is_st", "is_delisted",
                 "has_industry", "has_key_metrics", "is_analyzable",
                 "is_model_ready", "flag_loss", "flag_missing_key",
                 "flag_negative_equity"]
    for col in bool_cols:
        if col in out.columns:
            out[col] = _to_bool(out[col])

    return out


def _to_bool(series: pd.Series) -> pd.Series:
    """把字符串/数字表示的布尔值统一成真正的 bool 类型。

    兼容 CSV 往返后出现的多种表示：True/False、true/false、1/0、是/否。
    """
    if series.dtype == bool:
        return series
    if series.dtype == object or str(series.dtype).startswith("string"):
        return (series.astype(str).str.strip().str.lower()
                .isin(["true", "1", "yes", "是", "1.0"]))
    return series.fillna(False).astype(bool)


def add_business_segments(df: pd.DataFrame) -> pd.DataFrame:
    """添加业务分档字段（把连续值变成有业务含义的类别）。

    为什么要分档？
    因为面试官和业务方听得懂"优秀/良好/一般/亏损"，
    但听不懂"ROE 分位数 0.83"。分档是把数据翻译成业务语言。
    """
    out = df.copy()

    # ---------- ROE 分档（依据 A 股实际分布，用分位数定边界）----------
    if "roe_w" in out.columns:
        # 业务标准：ROE > 15% 算优秀（长期看这是巴菲特的选股门槛）
        #           8%~15% 良好 / 0~8% 一般 / <0 亏损
        bins = [-np.inf, 0, 8, 15, np.inf]
        labels = ["亏损", "一般", "良好", "优秀"]
        out["roe_tier"] = pd.cut(out["roe_w"], bins=bins, labels=labels,
                                 right=False).astype("string")

    # ---------- 营收规模分档 ----------
    if "revenue_yi" in out.columns:
        bins = [-np.inf, 10, 50, 200, 1000, np.inf]
        labels = ["微型(<10亿)", "小型(10-50亿)", "中型(50-200亿)",
                  "大型(200-1000亿)", "超大型(>1000亿)"]
        out["revenue_tier"] = pd.cut(out["revenue_yi"], bins=bins, labels=labels,
                                     right=False).astype("string")

    # ---------- 成长性分档 ----------
    if "growth_score" in out.columns:
        bins = [-np.inf, -20, 0, 20, 50, np.inf]
        labels = ["大幅下滑", "小幅下滑", "小幅增长", "较快增长", "高速增长"]
        out["growth_tier"] = pd.cut(out["growth_score"], bins=bins, labels=labels,
                                    right=False).astype("string")

    return out


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """特征工程主流程：比率 → 截尾 → 标记 → 分档。

    顺序不能乱：
      必须先算比率再截尾（截尾要有对象），
      必须先生成 roe_w 再分档（分档用的是截尾后的值）。
    """
    logger.info("开始特征工程：输入 %d 行 × %d 列", len(df), df.shape[1])
    out = build_ratio_features(df)
    out = winsorize(out)
    out = add_quality_flags(out)
    out = add_business_segments(out)
    logger.info("特征工程完成：输出 %d 行 × %d 列（新增 %d 列）",
                len(out), out.shape[1], out.shape[1] - df.shape[1])
    return out
