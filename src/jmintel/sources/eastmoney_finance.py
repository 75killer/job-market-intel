"""东方财富财务数据源：上市公司业绩报表（RPT_LICO_FN_CPD）。

为什么用这个接口（而不是行情接口）？
—————————————————————————————————————————————
行情接口（push2 域名）对我们做了 IP 限流，实测"能用一会儿就全挂"。
财务接口（datacenter-web 域名）稳定可用，而且字段更适合做分析：

    报告期报表字段（实测 37 个字段，本模块用到其中 20 个）：
        SECURITY_CODE / SECURITY_NAME_ABBR  股票代码 / 名称
        REPORTDATE                          报告期（2024-12-31 等）
        PUBLISHNAME / BOARD_NAME            所属行业（如"银行Ⅱ"）
        TRADE_MARKET                        上市板块（深交所主板 等）
        BASIC_EPS                           基本每股收益（元）
        TOTAL_OPERATE_INCOME                营业总收入（元）
        PARENT_NETPROFIT                    归母净利润（元）
        WEIGHTAVG_ROE                       加权平均净资产收益率（%）
        YSTZ                                营业收入同比增长（%）
        SJLTZ                               净利润同比增长（%）
        XSMLL                               销售毛利率（%）
        ZXGXL                               最新股息率（%）
        BPS                                 每股净资产（元）
        MGJYXJJE                            每股经营现金流（元）
        ASSIGNDSCRPT                        分红方案说明

接口特性（实测结论，写进代码避免后人踩坑）：
    - 单页最大 500 条，超过会被静默截断成 500 —— 这是个隐蔽的坑
    - 支持 filter=(REPORTDATE='2024-12-31') 按报告期过滤
    - 每个报告期约 11000+ 条记录（含已退市公司）
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from .. import config
from ..cache import merge_cached, save_cached
from ..http import HttpClient
from ..logging_setup import get_logger

logger = get_logger(__name__)

# 接口单页硬上限，实测 500（写 1000 也只会返回 500，且不报错）
MAX_PAGE_SIZE = 500

_HEADERS = {"Referer": "https://data.eastmoney.com/"}

REPORT_NAME = "RPT_LICO_FN_CPD"

# ---------------------------------------------------------
# 接口字段 -> 规范列名
# 命名风格用英文蛇形，因为后续 SQL / 建模都用英文列名更方便
# ---------------------------------------------------------
FIELD_MAP: dict[str, str] = {
    "SECURITY_CODE": "stock_code",        # 股票代码
    "SECURITY_NAME_ABBR": "stock_name",   # 股票简称
    "REPORTDATE": "report_date",          # 报告期
    "PUBLISHNAME": "industry",            # 所属行业
    "TRADE_MARKET": "market",             # 上市板块
    "SECURITY_TYPE": "security_type",     # 证券类型
    "BASIC_EPS": "eps",                   # 基本每股收益（元）
    "TOTAL_OPERATE_INCOME": "revenue",    # 营业总收入（元）
    "PARENT_NETPROFIT": "net_profit",     # 归母净利润（元）
    "WEIGHTAVG_ROE": "roe",               # 加权平均 ROE（%）
    "YSTZ": "revenue_yoy",                # 营收同比（%）
    "SJLTZ": "profit_yoy",                # 净利润同比（%）
    "XSMLL": "gross_margin",              # 销售毛利率（%）
    "ZXGXL": "dividend_yield",            # 最新股息率（%）
    "BPS": "bps",                         # 每股净资产（元）
    "MGJYXJJE": "ocf_per_share",          # 每股经营现金流（元）
    "ASSIGNDSCRPT": "dividend_plan",      # 分红方案
    "DATATYPE": "report_type",            # 报告类型（年报/半年报…）
    "NOTICE_DATE": "notice_date",         # 公告日期
}

_COLUMNS = ",".join(FIELD_MAP.keys())


def fetch_performance(
    client: HttpClient,
    report_date: str,
    page_size: int = MAX_PAGE_SIZE,
    max_pages: int | None = None,
    use_cache: bool = True,
) -> list[dict]:
    """采集指定报告期的业绩报表数据。

    参数
    ----
    report_date : 报告期，如 "2024-12-31"（年报）、"2025-06-30"（半年报）
    page_size   : 每页条数，不要超过 MAX_PAGE_SIZE
    max_pages   : 最多抓几页（None=全部）。调试时传 1 可以快速验证
    use_cache   : 是否使用本地缓存。首次采集后再次运行会直接读本地，
                  避免重复请求（我们已经被限流教过一次了）

    返回：原始记录列表
    """
    if page_size > MAX_PAGE_SIZE:
        logger.warning("page_size=%d 超过接口上限，已自动调整为 %d",
                       page_size, MAX_PAGE_SIZE)
        page_size = MAX_PAGE_SIZE

    cache_key = report_date

    # ---------- 优先读缓存 ----------
    if use_cache:
        from ..cache import load_cached

        cached = load_cached("finance_performance", cache_key)
        if cached and isinstance(cached, list) and len(cached) > 0:
            logger.info("使用本地缓存（%d 条），跳过网络请求", len(cached))
            return cached

    # ---------- 网络采集 ----------
    records: list[dict] = []
    page = 1

    while True:
        params: dict[str, Any] = {
            "reportName": REPORT_NAME,
            "columns": _COLUMNS,
            "filter": f"(REPORTDATE='{report_date}')",
            "pageSize": page_size,
            "pageNumber": page,
            "source": "WEB",
            "client": "WEB",
        }

        try:
            payload = client.get_json(
                config.EASTMONEY_FINANCE_URL, params=params, headers=_HEADERS
            )
        except RuntimeError as exc:
            # 网络层最终失败：不要丢掉已采集的数据，先存盘再抛出
            logger.error("第 %d 页采集失败：%s", page, exc)
            if records:
                logger.warning("已采集 %d 条，先落盘避免丢失", len(records))
                merged = merge_cached("finance_performance", cache_key, records)
                save_cached("finance_performance", cache_key, merged)
            raise

        result = payload.get("result") or {}
        data = result.get("data") or []
        total_pages = result.get("pages")

        if page == 1:
            est_total = (total_pages or 0) * page_size
            logger.info("报告期 %s：接口报告约 %s 页 × %d 条 ≈ %d 条记录",
                        report_date, total_pages, page_size, est_total)

        if not data:
            logger.info("第 %d 页无数据，采集结束", page)
            break

        records.extend(data)
        logger.info("第 %d/%s 页完成，累计 %d 条",
                    page, total_pages if total_pages else "?", len(records))

        # 判断是否已取完
        if total_pages is not None and page >= int(total_pages):
            logger.info("已取完全部 %s 页", total_pages)
            break
        if len(data) < page_size:
            logger.info("本页不足一页（%d < %d），视为已取完", len(data), page_size)
            break
        if max_pages is not None and page >= max_pages:
            logger.info("达到页数上限 %d，停止采集", max_pages)
            break

        page += 1
        time.sleep(0.5)  # 主动间隔，降低被限流风险

    # ---------- 合并进缓存（多次运行不丢数据）----------
    merged = merge_cached("finance_performance", cache_key, records)
    save_cached("finance_performance", cache_key, merged)
    return merged


def parse_performance(records: list[dict]) -> pd.DataFrame:
    """把原始记录解析成规范 DataFrame。

    职责：
      1. 字段改名
      2. 类型转换（金额转数值、日期转 datetime）
      3. 派生业务指标（净利率、营收规模分档等）
      4. 数据质量校验（标记异常值）
    """
    if not records:
        logger.warning("没有记录可解析")
        return pd.DataFrame()

    df = pd.DataFrame(records)

    existing = {k: v for k, v in FIELD_MAP.items() if k in df.columns}
    missing = sorted(set(FIELD_MAP) - set(existing))
    if missing:
        logger.warning("接口未返回这些字段（可能接口变更）：%s", missing)
    df = df[list(existing.keys())].rename(columns=existing)

    # ---------- 类型转换 ----------
    numeric_cols = ["eps", "revenue", "net_profit", "roe", "revenue_yoy",
                    "profit_yoy", "gross_margin", "dividend_yield", "bps",
                    "ocf_per_share"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ("report_date", "notice_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    for col in ("stock_code", "stock_name", "industry", "market",
                "security_type", "report_type"):
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()

    # ---------- 派生指标 ----------
    # 1) 金额换算成"亿元"，可读性更好
    if "revenue" in df.columns:
        df["revenue_yi"] = (df["revenue"] / 1e8).round(4)
    if "net_profit" in df.columns:
        df["net_profit_yi"] = (df["net_profit"] / 1e8).round(4)

    # 2) 净利率 = 净利润 / 营收 × 100（衡量盈利质量的核心指标）
    if {"net_profit", "revenue"}.issubset(df.columns):
        safe_revenue = df["revenue"].where(df["revenue"] > 0)
        df["net_margin"] = (df["net_profit"] / safe_revenue * 100).round(4)

    # 3) 报告期所属年份与季度，方便做时间维度分析
    if "report_date" in df.columns:
        df["report_year"] = df["report_date"].dt.year.astype("Int64")
        df["report_quarter"] = df["report_date"].dt.quarter.astype("Int64")

    # ---------- 数据质量标记 ----------
    # 为什么不直接删掉异常值？因为"哪家公司被标记为异常"本身是有价值的分析素材，
    # 而且删除会让"公司总数"对不上财报披露数量，反而不好解释。
    df["flag_negative_equity"] = df["roe"].lt(-50) if "roe" in df.columns else False
    df["flag_loss"] = df["net_profit"].lt(0) if "net_profit" in df.columns else False
    df["flag_missing_key"] = df[["revenue", "net_profit"]].isna().any(axis=1) \
        if {"revenue", "net_profit"}.issubset(df.columns) else False

    logger.info("解析完成：%d 行 × %d 列", len(df), df.shape[1])
    return df
