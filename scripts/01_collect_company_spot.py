"""采集入口脚本：一条命令跑通“取数 -> 解析 -> 落盘”。

用法（在项目根目录执行）：
    # 先试着抓 1 页，确认通了
    .venv\\Scripts\\python.exe scripts\\01_collect_company_spot.py --max-pages 1

    # 全量采集（5000+ 家公司，约 1-2 分钟）
    .venv\\Scripts\\python.exe scripts\\01_collect_company_spot.py

为什么要单独写一个 scripts/ 目录？
因为“能跑起来的入口”和“可复用的逻辑”要分开：
  - src/jmintel/  放逻辑（可以被测试、被别的脚本复用）
  - scripts/      放入口（只负责调用逻辑 + 解析命令行参数）
这是 Python 项目的主流分层方式。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把 src/ 加入模块搜索路径，这样不用安装成包也能 import jmintel
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jmintel import config, storage  # noqa: E402
from jmintel.http import HttpClient  # noqa: E402
from jmintel.logging_setup import get_logger, setup_logging  # noqa: E402
from jmintel.sources import eastmoney  # noqa: E402

logger = get_logger("collect_company_spot")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    给脚本加参数是专业习惯：别人不用改代码就能调整行为。
    """
    parser = argparse.ArgumentParser(description="采集 A 股上市公司行情快照")
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="最多采集多少页（每页100条）。不填=全量；调试建议填 1",
    )
    parser.add_argument(
        "--page-size", type=int, default=100,
        help="每页条数，接口上限 100",
    )
    parser.add_argument(
        "--table", type=str, default="ods_company_spot",
        help="写入数据库的表名（ods = 原始数据层）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(log_file=PROJECT_ROOT / "logs" / "collect.log")
    config.ensure_dirs()

    logger.info("=" * 60)
    logger.info("开始采集：A股上市公司行情快照")
    logger.info("=" * 60)

    with HttpClient() as client:
        # 第 1 步：请求原始数据
        records = eastmoney.fetch_company_spot(
            client, max_pages=args.max_pages, page_size=args.page_size
        )

    if not records:
        logger.error("没有采集到任何数据，请检查网络或接口是否变更")
        return 1

    # 第 2 步：原始数据原样落盘（可回溯，永不覆盖）
    storage.save_raw_json(records, "eastmoney_company_spot")

    # 第 3 步：解析成规范表
    df = eastmoney.parse_company_spot(records)

    # 第 4 步：写入 CSV 和数据库
    storage.save_dataframe(df, args.table)

    # 第 5 步：打印一小段结果，让人直观看到数据长什么样
    logger.info("-" * 60)
    logger.info("数据预览（按市值降序前 5 行）：")
    preview_cols = [c for c in ("stock_code", "stock_name", "industry",
                                "market_cap_yi", "pe_ratio") if c in df.columns]
    print(df[preview_cols].head(5).to_string(index=False))
    logger.info("-" * 60)
    logger.info("行业数量：%s 个", df["industry"].nunique() if "industry" in df else "N/A")
    logger.info("采集完成，数据已可用于下一步分析")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
