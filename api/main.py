"""FastAPI 模型服务：把训练好的 ROE 预测模型封装成 REST 接口。

为什么要把模型做成 API？（面试要能讲清价值）
—————————————————————————————————————————————
模型训练完存在 .joblib 文件里，只有写 Python 的人能用。
做成 API 之后：
  - 前端、Excel、其他系统都能调用
  - 模型更新时调用方无感知
  - 可以统一做限流、日志、鉴权
这是"模型从实验走向生产"的标准一步，也是数据分析岗与算法岗的加分项。

启动方式：
    uvicorn api.main:app --reload --port 8000
  或
    python scripts/10_run_api.py

交互式文档（FastAPI 自动生成，这是它的核心优势之一）：
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# 把 src 加入模块搜索路径，保证 uvicorn 从任意目录启动都能找到 jmintel
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config, storage  # noqa: E402
from jmintel.logging_setup import get_logger  # noqa: E402

logger = get_logger("api")

# ---------------------------------------------------------
# 全局状态：模型与元信息在启动时加载一次，而不是每次请求都读文件。
# 这是 API 服务的基本性能常识——磁盘 IO 比内存慢几个数量级。
# ---------------------------------------------------------
STATE: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时加载模型，关闭时释放资源。"""
    model_path = config.MODELS_DIR / "best_model.joblib"
    meta_path = config.MODELS_DIR / "model_metadata.json"

    if not model_path.exists() or not meta_path.exists():
        logger.error("模型文件不存在，请先运行 scripts/08_train_model.py")
        STATE["model"] = None
        STATE["metadata"] = {}
    else:
        STATE["model"] = joblib.load(model_path)
        STATE["metadata"] = json.loads(meta_path.read_text(encoding="utf-8"))
        logger.info("模型已加载：%s（特征 %d 个）",
                    STATE["metadata"]["best_model"],
                    len(STATE["metadata"]["features"]))
    STATE["started_at"] = time.time()
    yield
    STATE.clear()
    logger.info("服务已关闭")


app = FastAPI(
    title="A股上市公司财务质量分析 API",
    description=(
        "基于财务指标预测上市公司 ROE 的模型服务。\n\n"
        "**数据口径**：2024 年报，真实 A 股上市公司（已剔除新三板/ST/退市）。\n\n"
        "**模型**：随机森林回归，5 折交叉验证 R² ≈ 0.54。\n\n"
        "**重要说明**：模型用于**排序筛选**而非精确定价；"
        "结果仅供技术演示，不构成任何投资建议。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# =========================================================
# 请求 / 响应模型（Pydantic 会自动做类型校验和文档生成）
# =========================================================
class CompanyFeatures(BaseModel):
    """单家公司的特征输入。

    注意：字段含义必须写清楚，否则调用方不知道要传什么。
    FastAPI 会把这些说明自动渲染进 /docs 页面。
    """

    log_revenue: float = Field(..., description="营业收入的 log10 值（元→亿元后取对数）",
                               examples=[1.85])
    revenue_yoy_w: float = Field(..., description="营业收入同比增长率（%），已截尾",
                                 examples=[12.5])
    profit_yoy_w: float = Field(..., description="净利润同比增长率（%），已截尾",
                                examples=[8.3])
    gross_margin_w: float = Field(..., description="销售毛利率（%）", examples=[32.4])
    expense_ratio: float = Field(..., description="费用率 = 毛利率 - 净利率（%）",
                                 examples=[22.1])
    dividend_yield_w: float = Field(..., description="股息率（%），无分红填 0",
                                    examples=[2.5])
    ocf_per_share: float = Field(..., description="每股经营现金流（元）", examples=[1.2])

    model_config = {
        "json_schema_extra": {
            "example": {
                "log_revenue": 1.85, "revenue_yoy_w": 12.5, "profit_yoy_w": 8.3,
                "gross_margin_w": 32.4, "expense_ratio": 22.1,
                "dividend_yield_w": 2.5, "ocf_per_share": 1.2,
            }
        }
    }


class PredictionResponse(BaseModel):
    """预测响应。"""

    predicted_roe: float = Field(..., description="预测的 ROE（%）")
    quality_tier: str = Field(..., description="质量分档：优秀/良好/一般/亏损")
    model_name: str = Field(..., description="使用的模型名称")
    disclaimer: str = Field(..., description="免责声明")


class BatchRequest(BaseModel):
    """批量预测请求。"""

    companies: list[CompanyFeatures] = Field(..., description="公司特征列表",
                                             max_length=500)


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    model_loaded: bool
    model_name: str | None
    uptime_seconds: float
    db_tables: list[str]


# =========================================================
# 工具函数
# =========================================================
def _classify(roe: float) -> str:
    """把预测的 ROE 转成业务语言的质量分档。

    分档边界与分析口径保持一致（见 sql/02_quality_distribution.sql），
    这样 API 的返回结果与 SQL 分析报告可以直接对照。
    """
    if roe < 0:
        return "亏损"
    if roe < 8:
        return "一般"
    if roe < 15:
        return "良好"
    return "优秀"


def _require_model() -> None:
    """检查模型是否已加载，未加载则抛出 503。"""
    if STATE.get("model") is None:
        raise HTTPException(
            status_code=503,
            detail="模型未加载。请先运行 python scripts/08_train_model.py 训练模型",
        )


# =========================================================
# 接口定义
# =========================================================
@app.get("/", summary="服务首页")
def root() -> dict[str, Any]:
    """返回服务基本信息和可用接口列表。"""
    return {
        "service": "A股上市公司财务质量分析 API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": [
            "GET  /health          健康检查",
            "GET  /model/info      模型元信息",
            "POST /predict         单公司 ROE 预测",
            "POST /predict/batch   批量预测",
            "GET  /industry/ranking 行业 ROE 排名",
            "GET  /company/{code}   按股票代码查询历史数据",
        ],
        "disclaimer": "本服务仅供技术演示，不构成任何投资建议",
    }


@app.get("/health", response_model=HealthResponse, summary="健康检查")
def health() -> HealthResponse:
    """健康检查接口。

    生产环境的服务必须有健康检查：负载均衡器靠它判断实例是否可用，
    监控系统靠它告警。
    """
    return HealthResponse(
        status="ok",
        model_loaded=STATE.get("model") is not None,
        model_name=STATE.get("metadata", {}).get("best_model"),
        uptime_seconds=round(time.time() - STATE.get("started_at", time.time()), 2),
        db_tables=storage.list_tables(),
    )


@app.get("/model/info", summary="模型元信息")
def model_info() -> dict[str, Any]:
    """返回模型的训练信息、指标和特征列表。

    这个接口很重要：调用方需要知道模型的适用范围和精度，
    才能判断预测结果能不能用。
    """
    metadata = STATE.get("metadata") or {}
    if not metadata:
        raise HTTPException(status_code=503, detail="模型元信息不可用")
    return {
        "best_model": metadata.get("best_model"),
        "trained_at": metadata.get("trained_at"),
        "target": metadata.get("target_desc"),
        "report_period": metadata.get("report_period"),
        "features": metadata.get("features"),
        "excluded_leaky_features": metadata.get("excluded_leaky_features"),
        "exclusion_reason": metadata.get("exclusion_reason"),
        "metrics": metadata.get("metrics"),
        "all_models_comparison": metadata.get("all_models"),
        "known_limitations": [
            "使用同期财务数据预测同期 ROE，存在内生性；"
            "更严谨的做法是用 t-1 年数据预测 t 年",
            "仅一年样本，无法做时间维度验证",
            "加入行业特征已实验验证无效（R² 无提升）："
            "行业间差异仅占 ROE 总方差的 7.8%，"
            "92.2% 来自行业内公司差异",
        ],
    }


@app.post("/predict", response_model=PredictionResponse, summary="预测单家公司 ROE")
def predict(features: CompanyFeatures) -> PredictionResponse:
    """输入一家公司的财务指标，预测其 ROE。"""
    _require_model()

    feature_names = STATE["metadata"]["features"]
    # 按训练时的特征顺序构造输入，顺序错了模型会给出错误结果但不报错——
    # 这是非常隐蔽的 bug，所以必须显式按 names 取值。
    row = pd.DataFrame([[getattr(features, name) for name in feature_names]],
                       columns=feature_names)

    predicted = float(STATE["model"].predict(row)[0])
    return PredictionResponse(
        predicted_roe=round(predicted, 2),
        quality_tier=_classify(predicted),
        model_name=STATE["metadata"]["best_model"],
        disclaimer="预测结果仅供技术演示，不构成任何投资建议",
    )


@app.post("/predict/batch", summary="批量预测")
def predict_batch(request: BatchRequest) -> dict[str, Any]:
    """批量预测多公司的 ROE，按预测值降序返回。"""
    _require_model()

    feature_names = STATE["metadata"]["features"]
    rows = [[getattr(c, name) for name in feature_names] for c in request.companies]
    X = pd.DataFrame(rows, columns=feature_names)

    predictions = STATE["model"].predict(X)

    results = [
        {
            "index": i,
            "predicted_roe": round(float(p), 2),
            "quality_tier": _classify(float(p)),
        }
        for i, p in enumerate(predictions)
    ]
    results.sort(key=lambda r: -r["predicted_roe"])

    return {
        "count": len(results),
        "model_name": STATE["metadata"]["best_model"],
        "results": results,
        "disclaimer": "预测结果仅供技术演示，不构成任何投资建议",
    }


@app.get("/industry/ranking", summary="行业 ROE 排名")
def industry_ranking(limit: int = 20, min_companies: int = 8) -> dict[str, Any]:
    """读取 ads 层聚合结果，返回行业 ROE 排名。

    这个接口直接查数仓的 ads 表，不经过模型——
    因为"行业统计"是确定性的事实，不需要模型预测。
    区分"查事实"和"跑模型"是接口设计的基本判断。
    """
    try:
        df = storage.read_table("ads_industry_overview")
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="ads 层数据不存在，请先运行 scripts/05_build_dwd_ads.py",
        ) from exc

    df = df[(df["is_reliable"] == 1) | (df["company_count"] >= min_companies)]
    df = df.nlargest(limit, "median_roe")

    cols = ["industry", "company_count", "median_roe", "median_net_margin",
            "loss_ratio", "median_revenue_yi"]
    available = [c for c in cols if c in df.columns]

    return {
        "count": len(df),
        "min_companies_filter": min_companies,
        "ranking": df[available].to_dict(orient="records"),
        "note": "数据来自 ads_industry_overview，口径为 2024 年报真实 A 股",
    }


@app.get("/company/{stock_code}", summary="按股票代码查询公司数据")
def get_company(stock_code: str) -> dict[str, Any]:
    """查询指定股票代码的历史财务数据。"""
    try:
        df = storage.read_table("dwd_finance_performance")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="dwd 层数据不存在") from exc

    sub = df[df["stock_code"].astype(str) == stock_code]
    if sub.empty:
        raise HTTPException(status_code=404, detail=f"未找到股票代码 {stock_code}")

    cols = ["stock_code", "stock_name", "industry", "report_date", "roe", "eps",
            "revenue_yi", "net_profit_yi", "net_margin", "gross_margin",
            "dividend_yield", "roe_tier"]
    available = [c for c in cols if c in sub.columns]

    return {
        "stock_code": stock_code,
        "records": len(sub),
        "data": sub[available].astype(str).to_dict(orient="records"),
    }
