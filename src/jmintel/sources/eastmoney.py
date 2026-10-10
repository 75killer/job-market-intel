"""东方财富数据源：A 股上市公司行情与财务数据。

为什么选它：
  1. 接口公开、返回 JSON、无需登录、无需破解签名
  2. 数据真实且量大（5000+ 家上市公司，覆盖全部行业）
  3. 字段丰富：行情（价格/涨跌/成交）、估值（PE/PB/市值）、
     行业分类、地域板块、概念标签、财务（ROE/EPS/营收/净利润）
  4. 有一个天然的业务问题可以回答：
     “哪些行业景气度高、哪些公司值得去？”——这正好是你求职要问的

字段含义（f 开头的都是接口的字段代号，记不住没关系，代码里已注释）：
    f12 股票代码      f14 股票名称      f2  最新价
    f3  涨跌幅(%)     f8  换手率(%)     f9  市盈率(动态)
    f20 总市值        f21 流通市值      f23 市净率
    f62 主力净流入    f100 所属行业     f102 地域板块    f103 概念标签
"""

from __future__ import annotations

import pandas as pd

from .. import config
from ..http import HttpClient
from ..logging_setup import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------
# 接口字段 -> 中文列名 的映射表
# 为什么不直接用 f12/f14 当列名？因为三个月后你自己也看不懂，
# 而且面试官看代码时完全不知道 f20 是什么。
# ---------------------------------------------------------
QUOTE_FIELD_MAP: dict[str, str] = {
    "f12": "stock_code",      # 股票代码
    "f14": "stock_name",      # 股票名称
    "f2": "latest_price",     # 最新价
    "f3": "change_pct",       # 涨跌幅 %
    "f8": "turnover_rate",    # 换手率 %
    "f9": "pe_ratio",         # 市盈率（动态）
    "f20": "market_cap",      # 总市值（元）
    "f21": "float_market_cap",# 流通市值（元）
    "f23": "pb_ratio",        # 市净率
    "f62": "main_net_inflow", # 主力净流入（元）
    "f100": "industry",       # 所属行业
    "f102": "region",         # 地域板块
    "f103": "concepts",       # 概念标签（逗号分隔）
}

# 请求时向接口索要的字段，顺序要和上面的 key 一致
_QUOTE_FIELDS = ",".join(QUOTE_FIELD_MAP.keys())

# 该数据源必须携带的请求头。
# 踩坑记录见 config.EASTMONEY_REFERER 的注释：
# 缺少 Referer 会被服务端直接断连（表现为 ConnectionError 而不是 403）。
_HEADERS = {"Referer": config.EASTMONEY_REFERER}

# 沪深京 A 股的市场筛选条件（这是接口的固定语法）
# m:0 t:6 深证主板 / m:0 t:80 创业板 / m:1 t:2 沪证主板 / m:1 t:23 科创板
_A_SHARE_MARKETS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def fetch_company_spot(
    client: HttpClient,
    max_pages: int | None = None,
    page_size: int = 100,
) -> list[dict]:
    """采集 A 股上市公司行情快照（原始 JSON 列表）。

    参数
    ----
    client    : 复用的 HttpClient（统一限速和重试）
    max_pages : 最多抓多少页，None 表示全抓。
                调试时建议先传 1，确认没问题再全量——这是好习惯。
    page_size : 每页条数，接口上限 100

    返回：原始记录列表（未做任何字段改名，保留原样以便落盘）
    """
    records: list[dict] = []
    page = 1

    while True:
        params = {
            "pn": page,                 # 页码，从 1 开始
            "pz": page_size,            # 每页条数
            "po": 1,                    # 排序方向：1 降序
            "np": 1,                    # 返回普通 JSON（不是 JSONP）
            "fltt": 2,                  # 数字格式化方式
            "invt": 2,
            "fid": "f20",               # 排序字段：按总市值降序
            "fs": _A_SHARE_MARKETS,     # 市场筛选
            "fields": _QUOTE_FIELDS,    # 需要的字段
        }
        payload = client.get_json(
            config.EASTMONEY_QUOTE_URL, params=params, headers=_HEADERS
        )

        data = payload.get("data") or {}
        diff = data.get("diff") or []
        total = data.get("total")

        if page == 1:
            logger.info("接口报告总记录数：%s 条", total)

        if not diff:
            logger.info("第 %d 页无数据，采集结束", page)
            break

        # 接口偶尔返回 dict（key 为 "0","1"...）而不是 list，这里做兼容
        if isinstance(diff, dict):
            diff = list(diff.values())

        records.extend(diff)
        logger.info("第 %d 页完成，累计 %d 条", page, len(records))

        if max_pages is not None and page >= max_pages:
            logger.info("已达到页数上限 %d，停止采集", max_pages)
            break

        if total is not None and len(records) >= int(total):
            logger.info("已采集全部 %d 条记录", len(records))
            break

        page += 1

    return records


def parse_company_spot(records: list[dict]) -> pd.DataFrame:
    """把原始记录解析成规范 DataFrame。

    职责：
      1. 字段改名（f12 -> stock_code）
      2. 类型转换（接口把数字返回成 str 或 "-"，必须转成数值）
      3. 派生出有用的新字段（如“市值亿元”）
    """
    if not records:
        logger.warning("没有记录可解析")
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # 只保留我们声明过的字段，并改名
    existing = {k: v for k, v in QUOTE_FIELD_MAP.items() if k in df.columns}
    missing = set(QUOTE_FIELD_MAP) - set(existing)
    if missing:
        logger.warning("接口未返回这些字段：%s", sorted(missing))
    df = df[list(existing.keys())].rename(columns=existing)

    # 数值列强制转成数字：接口会用 "-" 表示缺失，to_numeric 会把它变成 NaN
    numeric_cols = [
        "latest_price", "change_pct", "turnover_rate", "pe_ratio",
        "market_cap", "float_market_cap", "pb_ratio", "main_net_inflow",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 文本列去空格
    for col in ("stock_code", "stock_name", "industry", "region", "concepts"):
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()

    # 派生字段：元 -> 亿元，方便读和画图
    if "market_cap" in df.columns:
        df["market_cap_yi"] = (df["market_cap"] / 1e8).round(2)
    if "main_net_inflow" in df.columns:
        df["main_net_inflow_yi"] = (df["main_net_inflow"] / 1e8).round(4)

    logger.info("解析完成：%d 行 × %d 列", len(df), df.shape[1])
    return df
