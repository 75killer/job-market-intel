"""存储层：把采集到的数据落到磁盘和数据库。

为什么要有"原始层"？（数据工程里的 Bronze/ODS 层）
因为它保证数据可回溯：解析代码写错了，原始数据还在，重新解析即可，
不用重新请求对方服务器。这是数据工程师的基本素养。

本模块提供：
    save_raw_json()      原始响应原样落盘（带时间戳，永不覆盖）
    save_dataframe()     把 DataFrame 存成 CSV + 写入 SQLite
    upsert_dataframe()   按主键合并写入（不覆盖其他数据）
    read_table()         从 SQLite 读回数据
    count_rows()         回读校验用：查某张表的实际行数

【重要踩坑记录：为什么不用 pandas 的 to_sql？】
——————————————————————————————————————————————
最初本模块用 `df.to_sql(name, conn, if_exists="replace")` 写库，结果出现了
一个极其隐蔽的 bug：
    - 日志显示"数据库表已写入：ods_finance_performance（23246 行）"
    - CSV 文件确实有 23246 行
    - 但 SELECT COUNT(*) 返回 **0**，表结构存在、数据却没了

诊断依据：`PRAGMA freelist_count = 772`（文件里 772 个页面是空闲的），
说明"表被创建 → 数据插入 → 事务回滚"，页面又被释放了。

根因是 sqlite3 连接的事务语义：
    `with sqlite3.connect(...) as conn:` 在退出时只做 **commit/rollback**，
    并不关闭连接；而 pandas 的 to_sql 与隐式事务配合时，
    DDL（建表）和 DML（插数据）可能落在不同的事务边界上，
    导致建表生效、插入被回滚。

**这个 bug 最危险的地方不是它发生了，而是它"看起来成功了"。**
所以本模块的写入函数全部改成显式 SQL，并且**写完立刻回读校验行数**——
宁可在写的时候报错，也不能让下游拿到一个空表还以为有数据。
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


# =========================================================
# SQLite 写入（显式 SQL 实现）
# =========================================================

def _sqlite_type(dtype: Any) -> str:
    """把 pandas 的 dtype 映射成 SQLite 的列类型。

    为什么要做映射而不是全部用 TEXT？
    因为后续要用 SQL 做聚合（AVG / SUM / ORDER BY），
    如果数字列存成文本，`ORDER BY` 会按字符串排序（"9" > "10"），
    `AVG` 也会算错。类型正确是 SQL 分析正确的前提。
    """
    name = str(dtype)
    if name.startswith("int") or name.startswith("Int") or name == "bool":
        return "INTEGER"
    if name.startswith("float") or name.startswith("Float"):
        return "REAL"
    return "TEXT"


# =========================================================
# 布尔列的显式声明（读写时自动还原类型）
#
# 【踩坑记录】SQLite 没有原生 BOOLEAN 类型，布尔值会存成 0/1 整数。
# 读回来时 pandas 得到的是 int64 而不是 bool，于是：
#     df[df["is_analyzable"]]      # 期望按行筛选
# 在 pandas 3.0 下会被解释成"选取名为 1/0 的列"，直接抛 KeyError：
#     KeyError: "None of [Index([1, 1, 1, ...])] are in the [columns]"
# 更隐蔽的是 bool.sum() 对整数列仍然正常，所以错误不会立刻暴露。
#
# 解决方案：在此集中声明布尔列，write_table 写入时转成 0/1，
# read_table 读出后立刻还原成真正的 bool。
# 这样上层代码永远拿到正确的类型，不需要每个脚本各自转换。
# =========================================================
BOOLEAN_COLUMNS: set[str] = {
    "flag_loss", "flag_missing_key", "flag_negative_equity",
    "is_a_share", "is_neeq", "is_st", "is_delisted",
    "has_industry", "has_key_metrics", "is_analyzable", "is_model_ready",
    "is_reliable",
}


def _connect() -> sqlite3.Connection:
    """创建数据库连接。

    注意 isolation_level=None：关闭 Python 的隐式事务管理，
    由我们显式调用 commit()。这样事务边界清晰，不会出现
    "看起来写成功了、其实被回滚"的情况。
    """
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode=DELETE")
    return conn


def _to_sql_value(value: Any) -> Any:
    """把 pandas 的值转成 sqlite3 能绑定的 Python 原生类型。

    sqlite3 只认识 int / float / str / bytes / None。
    pandas 的 Int64 / Timestamp / NA 都不是它认识的类型，直接传会报错，
    所以必须逐个转换——这一步是很多"写库失败"问题的根源。
    """
    if value is None:
        return None
    # pandas 缺失值（pd.NA / NaN / NaT）
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, bool):
        return int(value)
    # numpy 标量转成 Python 原生类型
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except (ValueError, AttributeError):
            return str(value)
    if isinstance(value, (int, float, str, bytes)):
        return value
    return str(value)


def write_table(
    df: pd.DataFrame,
    name: str,
    if_exists: str = "replace",
) -> int:
    """把 DataFrame 写入 SQLite 表，并回读校验行数。

    参数
    ----
    if_exists : "replace" 重建表 / "append" 追加

    返回：回读到的实际行数（校验通过才返回）
    """
    if df.empty:
        logger.warning("DataFrame 为空，跳过写库：%s", name)
        return 0

    columns = [str(c) for c in df.columns]
    col_defs = ", ".join(
        f'"{col}" {_sqlite_type(df[col].dtype)}' for col in df.columns
    )
    placeholders = ", ".join("?" for _ in columns)
    # 列名统一加双引号，避免与 SQL 关键字（如 from、order）冲突
    quoted_cols = ", ".join(f'"{c}"' for c in columns)
    insert_sql = f'INSERT INTO "{name}" ({quoted_cols}) VALUES ({placeholders})'

    # 逐行转换数据（sqlite3 不接受 pandas 的 NA/Timestamp 等类型）
    rows = [
        tuple(_to_sql_value(v) for v in rec)
        for rec in df.itertuples(index=False, name=None)
    ]

    conn = _connect()
    try:
        if if_exists == "replace":
            conn.execute(f'DROP TABLE IF EXISTS "{name}"')
            conn.execute(f'CREATE TABLE "{name}" ({col_defs})')
        conn.execute("BEGIN")
        conn.executemany(insert_sql, rows)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        conn.close()
        raise

    # ---------- 回读校验（这一步是踩坑之后加的，非常重要）----------
    actual = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    conn.close()

    if actual != len(df):
        raise RuntimeError(
            f"写库校验失败：期望 {len(df)} 行，实际只有 {actual} 行。"
            f"这说明写入被回滚或部分失败，请检查事务处理。"
        )

    logger.info("数据库表已写入：%s（%d 行 × %d 列，已回读校验）",
                name, actual, len(columns))
    return actual


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
        write_table(df, name, if_exists="replace")
        result["db"] = config.DB_PATH

    return result


def read_table(name: str) -> pd.DataFrame:
    """从 SQLite 读一张表回来。

    读出后会把声明过的布尔列还原成真正的 bool 类型（见 BOOLEAN_COLUMNS 的说明）。
    这样 `df[df["is_analyzable"]]` 这样的写法才能正常工作。
    """
    if not config.DB_PATH.exists():
        raise FileNotFoundError(f"数据库不存在：{config.DB_PATH}，请先运行采集脚本")
    conn = _connect()
    try:
        df = pd.read_sql_query(f'SELECT * FROM "{name}"', conn)
    finally:
        conn.close()

    # 还原布尔列类型
    restored = []
    for col in df.columns:
        if col in BOOLEAN_COLUMNS:
            df[col] = df[col].fillna(0).astype(bool)
            restored.append(col)
    if restored:
        logger.debug("已还原布尔列类型：%s", ", ".join(restored))
    return df


def count_rows(name: str) -> int:
    """查某张表的实际行数（用于校验和自检）。"""
    if not config.DB_PATH.exists():
        return 0
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        if not row or row[0] == 0:
            return 0
        return conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    finally:
        conn.close()


def upsert_dataframe(
    df: pd.DataFrame,
    name: str,
    key_cols: list[str],
) -> pd.DataFrame:
    """把新数据合并进已有表，而不是覆盖整张表。

    为什么需要这个函数？（另一个真实踩坑的修复）
    ---------------------------------------------------------------
    最初 save_dataframe 用的是整表替换。这在"永远一次采全量"的假设下没问题，
    但现实中：
        先跑 `--report-date 2024-12-31` → 表里是 2024 年报
        再跑 `--report-date 2025-06-30` → 表被重建，2024 年报**全丢了**
    表面上看脚本每次都"成功"，数据却悄悄少了——这种静默的数据丢失
    比直接报错危险得多。

    修复方式：先读旧表，按业务主键（股票代码 + 报告期）合并，
    新数据覆盖同键旧数据（财报会被修正），其他键的旧数据保留。
    """
    if df.empty:
        logger.warning("DataFrame 为空，跳过合并写入：%s", name)
        return df

    if count_rows(name) == 0:
        logger.info("目标表不存在或为空，直接写入：%s", name)
        save_dataframe(df, name)
        return df

    old = read_table(name)
    usable_keys = [c for c in key_cols if c in old.columns and c in df.columns]
    if not usable_keys:
        logger.warning("主键列 %s 不完整，退化为整表覆盖", key_cols)
        save_dataframe(df, name)
        return df

    # 统一按字符串比较，避免 Timestamp 与 str 类型不一致导致去重失效
    combined = pd.concat([old, df], ignore_index=True)
    for k in usable_keys:
        combined[k] = combined[k].astype(str)
    before = len(combined)
    combined = combined.drop_duplicates(subset=usable_keys, keep="last")
    logger.info(
        "合并写入 %s：旧表 %d 行 + 新数据 %d 行 → %d 行（按 %s 去重，新数据优先）",
        name, len(old), len(df), len(combined), "+".join(usable_keys),
    )
    if before != len(combined):
        logger.info("    其中覆盖了 %d 行同键数据", before - len(combined))

    save_dataframe(combined, name)
    return combined


def list_tables() -> list[str]:
    """列出数据库里所有表名（方便确认数据到底存进去没有）。"""
    if not config.DB_PATH.exists():
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]
