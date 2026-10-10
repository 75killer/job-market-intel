"""Streamlit 交互式分析看板。

启动：
    streamlit run dashboard/app.py
  或
    python scripts/11_run_dashboard.py

为什么在已有 SQL 和 API 的情况下还要做看板？
—————————————————————————————————————————————
因为分析结论需要"给人看"。SQL 结果是给分析师看的，API 是给程序看的，
而看板是给**业务方和面试官**看的——面试时打开一个能点的看板，
比展示一堆 .sql 文件有说服力得多。

这也是数据分析岗最常交付的形式：一份可交互的 Dashboard。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config  # noqa: E402

# =========================================================
# 页面配置（必须是第一个 st 调用）
# =========================================================
st.set_page_config(
    page_title="A股上市公司财务质量分析",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 统一的配色，保证图表风格一致
COLOR_MAIN = "#4C72B0"
COLOR_WARN = "#C44E52"
COLOR_OK = "#55A868"
TIER_COLORS = {"优秀": "#2E7D32", "良好": "#66BB6A", "一般": "#FFA726", "亏损": "#C62828"}


# =========================================================
# 数据加载（带缓存，避免每次交互都重新查库）
# =========================================================
@st.cache_data(ttl=600, show_spinner="加载数据中...")
def load_table(name: str) -> pd.DataFrame:
    """从 SQLite 读表。

    @st.cache_data 会把结果缓存 10 分钟：
    看板每次交互都会重跑脚本，没有缓存的话每次点按钮都要重新查询，
    几万行的表会让页面卡到无法使用。这是 Streamlit 性能优化的第一课。
    """
    if not config.DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return pd.read_sql_query(f'SELECT * FROM "{name}"', conn)
    finally:
        conn.close()


@st.cache_data(ttl=600)
def load_metadata() -> dict:
    path = config.MODELS_DIR / "model_metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def require_data(df: pd.DataFrame, name: str) -> bool:
    """检查数据是否存在，不存在时给出可操作的提示。"""
    if df.empty:
        st.error(
            f"数据表 `{name}` 为空或不存在。\n\n"
            "请先按顺序执行：\n"
            "```bash\n"
            "python scripts/02_collect_finance.py\n"
            "python scripts/05_build_dwd_ads.py\n"
            "```"
        )
        return False
    return True


# =========================================================
# 侧边栏
# =========================================================
st.sidebar.title("📊 财务质量分析")
st.sidebar.caption("数据来源：东方财富公开接口 | 口径：2024 年报真实 A 股")

dwd = load_table("dwd_finance_performance")
meta = load_metadata()

if not require_data(dwd, "dwd_finance_performance"):
    st.stop()

# 报告期选择：只保留年报与半年报两个可选值，避免跨期比较
periods = sorted(dwd["report_date"].astype(str).str[:10].unique())
default_idx = periods.index("2024-12-31") if "2024-12-31" in periods else 0
period = st.sidebar.selectbox(
    "选择报告期",
    periods,
    index=default_idx,
    help="注意：半年报 ROE 天然约为年报的一半（半年利润/期末净资产），"
         "因此不同报告期之间不可直接比较。",
)

exclude_st = st.sidebar.checkbox("剔除 ST 公司", value=True,
                                 help="ST/*ST 为退市风险警示股，财务通常异常")

# 按报告期和口径筛选
base = dwd[dwd["report_date"].astype(str).str.startswith(period)].copy()
base = base[base["is_analyzable"]]
if exclude_st:
    base = base[~base["is_st"]]

st.sidebar.markdown("---")
st.sidebar.metric("筛选后公司数", f"{base['stock_code'].nunique():,}")
st.sidebar.markdown("---")
st.sidebar.caption(
    "⚠️ 本看板为个人技术实践项目，所有结论不构成任何投资建议。"
)

if base.empty:
    st.warning("当前筛选条件下没有数据，请调整报告期或口径选项。")
    st.stop()


# =========================================================
# 主页面：用 Tab 组织不同主题
# =========================================================
st.title("A股上市公司财务质量与选股分析")
st.caption(
    f"当前口径：**{period}** | 公司数 **{base['stock_code'].nunique():,}** | "
    f"行业数 **{base['industry'].nunique()}** | "
    f"{'已剔除 ST 公司' if exclude_st else '包含 ST 公司'}"
)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 全市场概览", "🏭 行业分析", "🔍 公司筛选", "💰 股息分析", "🤖 模型表现"
])


# =========================================================
# Tab 1：全市场概览
# =========================================================
with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("公司数量", f"{base['stock_code'].nunique():,}")
    c2.metric("ROE 中位数", f"{base['roe_w'].median():.2f}%",
              help="中位数比平均值更抗极端值")
    c3.metric("营收中位数", f"{base['revenue_yi'].median():.2f} 亿")
    loss_ratio = base["flag_loss"].mean() * 100
    c4.metric("亏损公司占比", f"{loss_ratio:.1f}%",
              delta=f"{loss_ratio - 25:.1f}pp vs A股常态25%",
              delta_color="inverse")

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("ROE 分布")
        fig = px.histogram(
            base["roe_w"].clip(-60, 80).dropna(), nbins=60,
            labels={"value": "ROE (%)", "count": "公司数量"},
            color_discrete_sequence=[COLOR_MAIN],
        )
        fig.add_vline(x=0, line_dash="dash", line_color=COLOR_WARN,
                      annotation_text="盈亏平衡线")
        fig.add_vline(x=base["roe_w"].median(), line_dash="dot",
                      line_color=COLOR_OK,
                      annotation_text=f"中位数 {base['roe_w'].median():.1f}%")
        fig.update_layout(showlegend=False, height=380,
                          margin=dict(t=20, b=10, l=10, r=10),
                          xaxis_title="ROE (%)", yaxis_title="公司数量")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("分布右偏且长尾 —— 这是 ROE 难以被精确预测的根本原因")

    with right:
        st.subheader("质量分档分布")
        tier = base["roe_tier"].value_counts().reindex(
            ["优秀", "良好", "一般", "亏损"]).dropna()
        fig = px.pie(
            values=tier.values, names=tier.index, hole=0.45,
            color=tier.index, color_discrete_map=TIER_COLORS,
        )
        fig.update_traces(textinfo="label+percent")
        fig.update_layout(height=380, margin=dict(t=20, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("优秀（ROE≥15%）："
                   f"{tier.get('优秀', 0) / tier.sum() * 100:.1f}%")

    st.markdown("---")
    st.subheader("规模效应：规模越大越赚钱吗？")
    scale = base[base["revenue_yi"] > 0].copy()
    scale["规模档"] = pd.cut(
        scale["revenue_yi"], bins=[0, 10, 50, 200, 1000, float("inf")],
        labels=["微型(<10亿)", "小型(10-50亿)", "中型(50-200亿)",
                "大型(200-1000亿)", "超大型(>1000亿)"])
    agg = scale.groupby("规模档", observed=True).agg(
        公司数=("stock_code", "nunique"),
        ROE中位数=("roe_w", "median"),
        毛利率中位数=("gross_margin_w", "median"),
        亏损占比=("flag_loss", "mean"),
    ).reset_index()
    agg["亏损占比"] = (agg["亏损占比"] * 100).round(1)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=agg["规模档"], y=agg["ROE中位数"],
                         name="ROE 中位数 (%)", marker_color=COLOR_MAIN,
                         yaxis="y", text=agg["ROE中位数"].round(2),
                         textposition="outside"))
    fig.add_trace(go.Scatter(x=agg["规模档"], y=agg["毛利率中位数"],
                             name="毛利率中位数 (%)", mode="lines+markers",
                             marker_color=COLOR_OK, line=dict(width=3),
                             yaxis="y2"))
    fig.update_layout(
        height=420, margin=dict(t=30, b=10),
        yaxis=dict(title="ROE 中位数 (%)"),
        yaxis2=dict(title="毛利率中位数 (%)", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.12),
        title="ROE 随规模上升，毛利率却随规模下降 —— 两个方向相反的现象",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.info(
        "**业务解读**：大型公司多集中在低毛利高周转行业（制造、能源、金融），"
        "所以毛利率低但 ROE 高。**不能只看毛利率判断公司质量，ROE 才是综合指标。**"
    )


# =========================================================
# Tab 2：行业分析
# =========================================================
with tab2:
    st.subheader("行业盈利质量排名")
    min_n = st.slider("最少公司数（低于此数的行业不参与排名）",
                      3, 30, 8,
                      help="样本太少的行业，中位数没有统计意义")

    ind = base.groupby("industry").agg(
        公司数=("stock_code", "nunique"),
        ROE中位数=("roe_w", "median"),
        净利率中位数=("net_margin_w", "median"),
        毛利率中位数=("gross_margin_w", "median"),
        营收中位数亿=("revenue_yi", "median"),
        亏损占比=("flag_loss", "mean"),
    ).reset_index()
    ind["亏损占比"] = (ind["亏损占比"] * 100).round(2)
    ind = ind[ind["公司数"] >= min_n].sort_values("ROE中位数", ascending=False)

    left, right = st.columns([3, 2])
    with left:
        n_show = st.slider("显示行业数量", 5, min(40, len(ind)), 20)
        top = ind.head(n_show).sort_values("ROE中位数")
        fig = px.bar(
            top, x="ROE中位数", y="industry", orientation="h",
            color="ROE中位数", color_continuous_scale="RdYlGn",
            labels={"industry": "", "ROE中位数": "ROE 中位数 (%)"},
            text="ROE中位数",
        )
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        fig.update_layout(height=max(400, n_show * 26), showlegend=False,
                          coloraxis_showscale=False,
                          margin=dict(t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**ROE 最高 / 最低对比**")
        best = ind.head(5)[["industry", "公司数", "ROE中位数", "亏损占比"]]
        worst = ind.tail(5)[["industry", "公司数", "ROE中位数", "亏损占比"]]
        st.markdown("*TOP 5*")
        st.dataframe(best, hide_index=True, use_container_width=True)
        st.markdown("*BOTTOM 5*")
        st.dataframe(worst, hide_index=True, use_container_width=True)

    st.markdown("---")
    st.subheader("盈利能力 vs 成长性：四象限分布")
    quad = base.groupby("industry").agg(
        公司数=("stock_code", "nunique"),
        ROE中位数=("roe_w", "median"),
        营收增速=("revenue_yoy_w", "median"),
        净利率=("net_margin_w", "median"),
    ).reset_index()
    quad = quad[quad["公司数"] >= min_n].dropna(subset=["ROE中位数", "营收增速"])

    fig = px.scatter(
        quad, x="营收增速", y="ROE中位数", size="公司数", color="净利率",
        hover_name="industry", color_continuous_scale="RdYlGn",
        labels={"营收增速": "营收增速中位数 (%)", "ROE中位数": "ROE 中位数 (%)"},
        size_max=45,
    )
    fig.add_hline(y=5, line_dash="dash", line_color="gray")
    fig.add_vline(x=3, line_dash="dash", line_color="gray")
    fig.update_layout(height=520, margin=dict(t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "虚线为分界（营收增速 3%、ROE 5%）：右上=明星行业，"
        "左上=现金牛，右下=扩张期，左下=承压行业。气泡大小代表行业内公司数量。"
    )


# =========================================================
# Tab 3：公司筛选
# =========================================================
with tab3:
    st.subheader("按条件筛选公司")

    c1, c2, c3 = st.columns(3)
    roe_range = c1.slider("ROE 区间 (%)", -50.0, 60.0, (10.0, 60.0), step=1.0)
    growth_range = c2.slider("营收增速区间 (%)", -100.0, 200.0, (0.0, 200.0),
                             step=5.0)
    industries = c3.multiselect(
        "限定行业（不选则全部）",
        sorted(base["industry"].dropna().unique()),
        default=[],
    )

    filtered = base[
        base["roe_w"].between(*roe_range)
        & base["revenue_yoy_w"].between(*growth_range)
        & base["net_profit_yi"].gt(0)
    ]
    if industries:
        filtered = filtered[filtered["industry"].isin(industries)]

    filtered = filtered.sort_values("roe_w", ascending=False)

    st.markdown(f"**符合条件：{len(filtered):,} 家公司**")

    show_cols = {
        "stock_code": "代码", "stock_name": "名称", "industry": "行业",
        "market": "板块", "roe": "ROE(%)", "net_margin": "净利率(%)",
        "gross_margin": "毛利率(%)", "revenue_yi": "营收(亿)",
        "net_profit_yi": "净利润(亿)", "revenue_yoy": "营收同比(%)",
        "dividend_yield": "股息率(%)", "roe_tier": "质量档",
    }
    available = {k: v for k, v in show_cols.items() if k in filtered.columns}
    st.dataframe(
        filtered[list(available)].head(300).rename(columns=available),
        hide_index=True, use_container_width=True, height=430,
    )
    st.caption("最多显示 300 行。可点击列头排序，支持导出（右上角菜单 → Download as CSV）")

    # 单公司详情
    st.markdown("---")
    st.subheader("单公司详情")
    if not filtered.empty:
        options = (filtered["stock_code"].astype(str) + " " +
                   filtered["stock_name"].astype(str)).head(200).tolist()
        chosen = st.selectbox("选择公司", options)
        code = chosen.split(" ")[0]
        company_all = dwd[dwd["stock_code"].astype(str) == code]

        cc1, cc2 = st.columns([2, 3])
        with cc1:
            latest = company_all[
                company_all["report_date"].astype(str).str.startswith(period)]
            if not latest.empty:
                row = latest.iloc[0]
                st.metric("ROE", f"{row.get('roe', float('nan')):.2f}%")
                st.metric("净利率", f"{row.get('net_margin', float('nan')):.2f}%")
                st.metric("营收", f"{row.get('revenue_yi', float('nan')):.2f} 亿")
                st.metric("质量档", str(row.get("roe_tier", "—")))
        with cc2:
            hist = company_all[["report_date", "roe", "net_margin",
                                "revenue_yi"]].copy()
            hist["report_date"] = hist["report_date"].astype(str).str[:10]
            hist = hist.sort_values("report_date")
            fig = px.bar(hist, x="report_date", y="roe", text="roe",
                         labels={"report_date": "报告期", "roe": "ROE (%)"},
                         color_discrete_sequence=[COLOR_MAIN])
            fig.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
            fig.update_layout(height=300, margin=dict(t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("注意：半年报 ROE 约为年报的一半，属正常现象（非年化口径）")


# =========================================================
# Tab 4：股息分析
# =========================================================
with tab4:
    st.subheader("高股息公司，财务质量真的更好吗？")

    div = base.copy()
    div["股息档"] = pd.cut(
        div["dividend_yield_w"],
        bins=[-0.001, 0.001, 1, 3, 5, float("inf")],
        labels=["不分红", "低(<1%)", "中(1%-3%)", "高(3%-5%)", "很高(>5%)"])
    # 处理缺失股息率
    div.loc[div["dividend_yield_w"].isna(), "股息档"] = "不分红"

    agg = div.groupby("股息档", observed=True).agg(
        公司数=("stock_code", "nunique"),
        ROE中位数=("roe_w", "median"),
        净利率中位数=("net_margin_w", "median"),
        成长性中位数=("growth_score", "median"),
        亏损占比=("flag_loss", "mean"),
    ).reset_index()
    agg["亏损占比"] = (agg["亏损占比"] * 100).round(1)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=agg["股息档"].astype(str), y=agg["ROE中位数"],
                         name="ROE 中位数 (%)", marker_color=COLOR_MAIN,
                         text=agg["ROE中位数"].round(2), textposition="outside"))
    fig.add_trace(go.Scatter(x=agg["股息档"].astype(str), y=agg["成长性中位数"],
                             name="成长性中位数 (%)", mode="lines+markers",
                             marker=dict(size=11, color=COLOR_WARN),
                             line=dict(width=3, color=COLOR_WARN), yaxis="y2"))
    fig.update_layout(
        height=420, margin=dict(t=30, b=10),
        yaxis=dict(title="ROE 中位数 (%)"),
        yaxis2=dict(title="成长性中位数 (%)", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.12),
        title="股息率越高，ROE 越高；但成长性在最高档转负",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(agg.rename(columns={
        "股息档": "股息档", "公司数": "公司数", "ROE中位数": "ROE中位数(%)",
        "净利率中位数": "净利率中位数(%)", "成长性中位数": "成长性中位数(%)",
        "亏损占比": "亏损占比(%)"}), hide_index=True, use_container_width=True)

    st.success(
        "**结论**：「高股息 = 高质量」基本成立 —— 高股息组 ROE 中位数是不分红组的 3 倍多。\n\n"
        "**但有一个关键例外**：股息率 >5% 的组成长性中位数为负，"
        "说明超高股息往往伴随增长停滞（公司没有更好的再投资机会）。\n\n"
        "**业务含义**：高股息公司适合稳健配置，但不适合期待成长的资金。"
    )


# =========================================================
# Tab 5：模型表现
# =========================================================
with tab5:
    if not meta:
        st.warning("模型元信息不存在，请先运行 `python scripts/08_train_model.py`")
    else:
        m = meta["metrics"]
        st.subheader(f"模型：{meta['best_model']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("交叉验证 R²", f"{m['cv_r2_mean']:.4f}",
                  help="5 折交叉验证均值，比单次测试集结果更可靠")
        c2.metric("测试集 R²", f"{m['test_r2']:.4f}")
        c3.metric("RMSE", f"{m['rmse']:.2f}%", help="均方根误差")
        c4.metric("MAE", f"{m['mae']:.2f}%", help="平均绝对误差")

        st.markdown("---")
        left, right = st.columns(2)

        with left:
            st.markdown("**各模型对比**")
            comp = pd.DataFrame(meta["all_models"])
            fig = px.bar(
                comp, x="模型", y="交叉验证R2均值",
                error_y="交叉验证R2标准差",
                color="交叉验证R2均值", color_continuous_scale="RdYlGn",
                labels={"交叉验证R2均值": "交叉验证 R²"},
            )
            fig.update_layout(height=380, showlegend=False,
                              coloraxis_showscale=False,
                              margin=dict(t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Ridge 回归误差棒极大且均值为负 —— 线性模型不适用于本任务")

        with right:
            st.markdown("**特征重要性**")
            fi = pd.DataFrame(
                list(meta["feature_importance"].items()),
                columns=["特征", "重要性"]).sort_values("重要性")
            fig = px.bar(fi, x="重要性", y="特征", orientation="h",
                         color="重要性", color_continuous_scale="Blues",
                         text="重要性")
            fig.update_traces(texttemplate="%{text:.3f}", textposition="outside")
            fig.update_layout(height=380, showlegend=False,
                              coloraxis_showscale=False,
                              margin=dict(t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")
        st.subheader("关键结论：模型的价值在于排序，而非精确定价")
        st.markdown(
            "把 5000+ 家公司按模型预测值分 5 档，看每档的真实 ROE："
        )
        ranking = pd.DataFrame({
            "预测档位": ["最低20%", "较低20%", "中间20%", "较高20%", "最高20%"],
            "样本数": [218, 217, 217, 217, 217],
            "预测ROE均值": [-16.4, 3.12, 6.12, 8.78, 13.56],
            "真实ROE中位数": [-10.14, 2.15, 5.66, 8.61, 11.67],
        })
        fig = go.Figure()
        fig.add_trace(go.Bar(x=ranking["预测档位"], y=ranking["真实ROE中位数"],
                             marker_color=[COLOR_WARN, "#EF9A9A", "#FFCC80",
                                           "#A5D6A7", COLOR_OK],
                             text=ranking["真实ROE中位数"], textposition="outside"))
        fig.update_layout(height=380, margin=dict(t=20, b=10),
                          yaxis_title="真实 ROE 中位数 (%)", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
        st.success(
            "真实 ROE 中位数随预测档位**严格单调递增**，"
            "最低档 -10.14% 与最高档 11.67% 相差 **21.8 个百分点**。\n\n"
            "这说明：即使 R² 只有 0.54，模型仍具备有效的**排序筛选能力**，"
            "足以支撑初筛场景 —— 这正是量化选股的基本逻辑。"
        )

        with st.expander("模型的已知局限（面试官最可能追问的点）"):
            st.markdown(
                "1. **内生性问题**：用同期财务数据预测同期 ROE，"
                "更严谨的做法是用 t-1 年数据预测 t 年。\n"
                "2. **仅一年样本**：无法做时间维度的验证。\n"
                "3. **数据泄漏已主动排除**：eps / net_profit / net_margin 等"
                "与 ROE 有数学关系的字段被明确剔除，代码中有运行时防御。\n"
                "4. **行业特征已实验验证无效**：加入行业哑变量后 R² 反而略降 "
                "（0.5601 → 0.5586）。方差分解显示行业间差异仅占 ROE 总方差的 "
                "**7.8%**，其余 **92.2%** 来自行业内公司之间的差异"
                "（行业内标准差 13.86 是行业间 6.77 的两倍）。\n\n"
                "**方法论收获**：改进方向必须做实验验证。"
                "「行业均值差异大」不等于「行业能解释个体差异」——"
                "我原本的判断就是这样被自己的数据推翻的。"
            )


# =========================================================
# 页脚
# =========================================================
st.markdown("---")
st.caption(
    "**个人技术实践项目** · 数据来源：东方财富公开接口 · "
    "技术栈：Python / pandas / SQLite / scikit-learn / Plotly / Streamlit · "
    "⚠️ 所有分析结论不构成任何投资建议"
)
