"""诊断：为什么数据写进了 CSV，却读不出数据库？

现象：
    CSV 文件有 23247 行（含表头）= 23246 条数据，完全正常
    但 SELECT COUNT(*) FROM ods_finance_performance 返回 0
    表结构存在（27 列），说明表被创建了，但数据没进去

可能的原因（逐一验证）：
    1. 表被重建了（if_exists="replace"），但新表是空的
    2. 数据写在了未提交的事务里
    3. pandas 3.0 的 to_sql 行为变化
    4. 写到了另一个数据库文件（路径问题）
    5. sqlite 的 WAL 模式导致文件里有数据但连接读不到
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jmintel import config  # noqa: E402

DB = config.DB_PATH
print("=" * 74)
print("诊断：数据库写入问题")
print("=" * 74)

print(f"\n【1】数据库路径")
print(f"    config.DB_PATH = {DB}")
print(f"    文件存在: {DB.exists()}")
if DB.exists():
    print(f"    文件大小: {DB.stat().st_size / 1024 / 1024:.2f} MB")

print(f"\n【2】目录下所有 .db 文件（排查是否写到了别的地方）")
for f in config.DATA_DIR.glob("**/*.db*"):
    print(f"    {f}  ({f.stat().st_size / 1024:.1f} KB)")

print(f"\n【3】表清单与行数")
conn = sqlite3.connect(DB)
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print(f"    表: {tables}")
for t in tables:
    cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    cols = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
    print(f"    {t}: {cnt} 行, {len(cols)} 列")

print(f"\n【4】数据库文件完整性检查")
for row in conn.execute("PRAGMA integrity_check"):
    print(f"    {row[0]}")

print(f"\n【5】journal 模式（WAL 模式下数据可能在 -wal 文件里）")
for row in conn.execute("PRAGMA journal_mode"):
    print(f"    journal_mode = {row[0]}")

print(f"\n【6】是否有未提交事务 / 页统计")
for pragma in ("page_count", "freelist_count", "schema_version"):
    val = conn.execute(f"PRAGMA {pragma}").fetchone()[0]
    print(f"    {pragma} = {val}")

print(f"\n【7】直接查表内容（前 2 行）")
try:
    rows = conn.execute('SELECT stock_code, stock_name, report_date FROM ods_finance_performance LIMIT 2').fetchall()
    print(f"    返回 {len(rows)} 行: {rows}")
except Exception as exc:  # noqa: BLE001
    print(f"    ❌ {type(exc).__name__}: {exc}")

conn.close()

print(f"\n【8】pandas / sqlite 版本")
import pandas as pd  # noqa: E402

print(f"    pandas  = {pd.__version__}")
print(f"    sqlite3 = {sqlite3.sqlite_version}")

print("\n" + "=" * 74)
print("诊断完成")
print("=" * 74)
