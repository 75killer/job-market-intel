"""本地缓存层：让重复运行不再打对方接口。

为什么必须要有这一层？
—————————————————————————————————————————————
我们在采集东方财富行情接口时被按 IP 限流过：
前几次请求正常，请求量一上来就全部失败（RemoteDisconnected），
而且会持续一段时间不恢复。

这个教训带来两个设计决定：
  1. **原始响应必须落盘**——接口挂了，历史数据还在，分析和建模不受影响。
  2. **重复运行先查本地**——调试解析逻辑时不要反复打接口，
     既避免再次被限流，也是对数据提供方的尊重。

这也是数据工程里的标准做法：raw 层数据是"一次采集、反复使用"的资产，
而不是"每次运行都重新拉取"的临时产物。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .logging_setup import get_logger

logger = get_logger(__name__)


def cache_path(dataset: str, key: str) -> Path:
    """缓存文件路径，形如 data/raw/finance_performance_2024-12-31.json"""
    return config.RAW_DIR / f"{dataset}_{key}.json"


def cache_exists(dataset: str, key: str) -> bool:
    return cache_path(dataset, key).exists()


def load_cached(dataset: str, key: str) -> Any | None:
    """读取本地缓存，不存在则返回 None。"""
    path = cache_path(dataset, key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        logger.info("命中本地缓存：%s", path.name)
        return payload
    except json.JSONDecodeError:
        logger.warning("缓存文件损坏，将重新采集：%s", path.name)
        return None


def save_cached(dataset: str, key: str, payload: Any) -> Path:
    """写入本地缓存（原子写：先写临时文件再改名，避免中断留下半截文件）。"""
    config.ensure_dirs()
    path = cache_path(dataset, key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # 原子替换
    size_kb = path.stat().st_size / 1024
    logger.info("已写入缓存：%s（%.1f KB）", path.name, size_kb)
    return path


def merge_cached(dataset: str, key: str, new_records: list[dict],
                 id_field: str = "SECURITY_CODE",
                 period_field: str = "REPORTDATE") -> list[dict]:
    """把新采集的记录合并进已有缓存，按 (代码, 报告期) 去重。

    为什么要合并而不是覆盖？
    因为接口可能只更新了一部分公司的数据。合并能保证"已拿到的数据不丢"，
    同时新数据能覆盖旧数据（同一公司同一报告期的财报会被修正）。
    """
    existing = load_cached(dataset, key) or []
    if not isinstance(existing, list):
        existing = []

    # 用 (代码, 报告期) 作为唯一键：后者覆盖前者
    merged: dict[tuple, dict] = {}
    for rec in existing:
        merged[(rec.get(id_field), rec.get(period_field))] = rec

    added = 0
    for rec in new_records:
        k = (rec.get(id_field), rec.get(period_field))
        if k not in merged:
            added += 1
        merged[k] = rec

    result = list(merged.values())
    logger.info("缓存合并：原有 %d 条 + 新增 %d 条 → 合计 %d 条（去重后）",
                len(existing), added, len(result))
    return result
