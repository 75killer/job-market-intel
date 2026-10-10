"""存储层：把采集到的数据落到磁盘和数据库。

为什么要有“原始层”？（数据工程里的 Bronze/ODS 层）
因为它保证数据可回溯：解析代码写错了，原始数据还在，重新解析即可，
不用重新请求对方服务器。这是数据工程师的基本素养。

本模块提供：
    save_raw_json()      原始响应原样落盘（带时间戳，永不覆盖）
    save_dataframe()     把 DataFrame 存成 CSV + 写入 SQLite
    read_table()         从 SQLite 读回数据
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import config
from .logging_setup import get_logger

logger = get_logger(__name__)


def timestamp() -> str:
    """返回形如 20260115_143012 的时间戳，用于给原始文件命名。

    用时间戳而不是固定文件名，好处是每次采集都留档，不会覆盖历史数据。
    """
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_raw_json(payload: Any, source: str) -> Path:
    """把原始响应保存为 JSON 文件。

    参数
    ----
    payload : 任意可 JSON 序列化的对象（通常是 dict 或 list）
    source  : 数据源名字，作为文件名前缀，如 "eastmoney_company_spot"

    返回保存的文件路径。
    """
    config.ensure_dirs()
    path = config.RAW_DIR / f"{source}_{timestamp()}.json"
    # ensure_ascii=False 才能让中文正常显示，否则会变成 \uXXXX
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    size_kb = path.stat().st_size / 1024
    logger.info("原始数据已保存：%s（%.1f KB）", path.name, size_kb)
    return path


def save_dataframe(
    df: pd.DataFrame,
    name: str,
    to_csv: bool = True,
    to_db: bool = True,
) -> dict[str, Path | None]:
    """把一个 DataFrame 同时存成 CSV 和 SQLite 表。

    参数
    ----
    df    : 要保存的数据
    name  : 表名/文件名，如 "dwd_company"
    to_csv: 是否写 CSV（方便用 Excel 打开检查）
    to_db : 是否写数据库（方便用 SQL 查询）

    返回 {"csv": 路径或None, "db": 路径或None}
    """
    config.ensure_dirs()
    result: dict[str, Path | None] = {"csv": None, "db": None}

    if df.empty:
        logger.warning("DataFrame 为空，跳过保存：%s", name)
        return result

    if to_csv:
        csv_path = config.PROCESSED_DIR / f"{name}.csv"
        # utf-8-sig 让 Excel 打开中文不乱码（这是个很实用的细节）
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info("CSV 已写入：%s（%d 行 × %d 列）", csv_path.name, len(df), df.shape[1])
        result["csv"] = csv_path

    if to_db:
        # if_exists="replace"：每次重建表，保证结果幂等（重复跑结果一致）
        with sqlite3.connect(config.DB_PATH) as conn:
            df.to_sql(name, conn, if_exists="replace", index=False)
        logger.info("数据库表已写入：%s", name)
        result["db"] = config.DB_PATH

    return result


def read_table(name: str) -> pd.DataFrame:
    """从 SQLite 读一张表回来。"""
    if not config.DB_PATH.exists():
        raise FileNotFoundError(f"数据库不存在：{config.DB_PATH}，请先运行采集脚本")
    with sqlite3.connect(config.DB_PATH) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{name}"', conn)


def list_tables() -> list[str]:
    """列出数据库里所有表名（方便确认数据到底存进去没有）。"""
    if not config.DB_PATH.exists():
        return []
    with sqlite3.connect(config.DB_PATH) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [r[0] for r in rows]
