"""日志配置：让程序“会说话”。

新手常见问题：程序跑完只打印几个 print，出了问题完全不知道哪一步挂了。
日志的作用是回答三个问题：
  1. 程序跑到哪一步了？（进度）
  2. 有没有出错？（错误级别）
  3. 什么时候跑的、花了多久？（时间戳）
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# 日志级别说明（数字越大越严重）：
# DEBUG=10 调试细节 / INFO=20 正常进度 / WARNING=30 警告 / ERROR=40 错误
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO, log_file: Path | None = None) -> None:
    """初始化日志：同时输出到控制台（给人看）和文件（事后查）。

    参数
    ----
    level    : 日志级别，默认 INFO
    log_file : 如果给了路径，就额外写一份到文件
    """
    handlers: list[logging.Handler] = []

    # 控制台输出：stdout，格式紧凑
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(DEFAULT_FORMAT, DATE_FORMAT))
    handlers.append(console)

    # 文件输出：完整时间戳，方便排查历史问题
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        handlers.append(file_handler)

    # basicConfig 只在第一次调用时生效，force=True 强制覆盖（Python 3.8+）
    logging.basicConfig(level=level, handlers=handlers, force=True)

    # requests / urllib3 的日志太吵，压到 WARNING
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取一个带名字的 logger。

    约定：每个模块用 get_logger(__name__)，
    这样日志里能看到是哪一层打的（jmintel.sources.eastmoney 等），
    定位问题非常快。
    """
    return logging.getLogger(name)
