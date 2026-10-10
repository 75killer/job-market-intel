"""全局配置：所有路径、常量、可调参数都集中在这里。

为什么要有这个文件？
- 新手常见写法是到处写死 "D:\\xxx\\data.csv"，换台电脑就崩。
- 把所有「会变的东西」集中到一处，改一个地方就能全局生效。
- 配置与逻辑分离是工程规范的基本要求。
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------
# 1. 项目根目录
#    __file__ 是当前文件路径，parents[2] 表示往上数三层：
#    config.py -> jmintel -> src -> 项目根
# ---------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------
# 2. 数据目录（分层，这是数据仓库的经典做法）
#    raw        原始数据，只落盘不修改（出了问题能回溯）
#    interim    清洗中的中间数据
#    processed  最终可用于分析的干净数据
# ---------------------------------------------------------
DATA_DIR: Path = PROJECT_ROOT / "data"
RAW_DIR: Path = DATA_DIR / "raw"
INTERIM_DIR: Path = DATA_DIR / "interim"
PROCESSED_DIR: Path = DATA_DIR / "processed"

# 文档与产物目录
DOCS_DIR: Path = PROJECT_ROOT / "docs"
FIGURES_DIR: Path = DOCS_DIR / "figures"
MODELS_DIR: Path = PROJECT_ROOT / "models"

# SQLite 数据库文件
DB_PATH: Path = DATA_DIR / "jmintel.db"

# ---------------------------------------------------------
# 3. 数据源配置
#    注意：这里只放“公开、无需登录”的接口。
#    绝对不要把自己的账号密码写进代码再传到 GitHub——
#    应该用环境变量（下面的 get_env 就是干这个的）。
# ---------------------------------------------------------
REQUEST_TIMEOUT: int = 20      # 单次请求超时（秒）
REQUEST_RETRIES: int = 3       # 失败重试次数
REQUEST_INTERVAL: float = 1.0  # 两次请求之间的间隔（秒）——礼貌抓取，别把人家服务器打挂

USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 东方财富：A股上市公司行情与财务（公开接口）
EASTMONEY_QUOTE_URL: str = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_FINANCE_URL: str = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 【踩坑记录】必须带 Referer，否则会被服务端直接断开连接
# ------------------------------------------------------------------
# 现象：单页请求（手动指定 User-Agent）成功；换成 HttpClient 走会话请求后，
#       连续 3 次 ConnectionError: RemoteDisconnected（对方连响应都不返回就断连）。
# 原因：东方财富的接口会校验请求来源，缺失 Referer 的请求被视为非常规调用，
#       在 TCP 层就被拒掉——所以连 HTTP 状态码都拿不到，看到的是连接错误
#       而不是 403。这类"看起来像网络故障、实际是缺少请求头"的问题，
#       排查时一定要和真正的网络问题区分开：
#         真正的网络故障 → 所有域名都失败
#         缺少请求头     → 只有特定域名/接口失败，且重试必然连续失败
# 修复：统一在会话头里带上 Referer。
# ------------------------------------------------------------------
EASTMONEY_REFERER: str = "https://quote.eastmoney.com/"

# Remotive：公开的远程岗位 API（官方开放，允许调用）
REMOTIVE_API_URL: str = "https://remotive.com/api/remote-jobs"


def get_env(key: str, default: str | None = None) -> str | None:
    """从环境变量读配置，避免把敏感信息写进代码。

    用法示例（以后要接需要 token 的接口时）：
        token = get_env("JMINTEL_TOKEN")
        if token is None:
            raise RuntimeError("请先设置环境变量 JMINTEL_TOKEN")
    """
    return os.environ.get(key, default)


def ensure_dirs() -> None:
    """确保所有目录存在。

    程序启动时调用一次，避免因为目录不存在而报 FileNotFoundError。
    """
    for d in (RAW_DIR, INTERIM_DIR, PROCESSED_DIR, FIGURES_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
