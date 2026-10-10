"""HTTP 工具：统一的网络请求层。

为什么不在每个爬虫里直接用 requests.get()？
因为那会导致：
  - 超时设置到处不一致
  - 没有重试，一次网络抖动整个任务失败
  - 没有限速，容易被封 IP（这是我们踩过的最大的坑）
把请求逻辑抽成一层，所有数据源共用，改一处全局生效。

本模块提供的能力：
  1. 统一 User-Agent 和超时
  2. 指数退避重试（失败后等 1s、2s、4s 再试，而不是立刻重试）
  3. 请求间隔限速（默认 1 秒一次）
  4. 返回 JSON / 文本两种便捷方法
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from . import config
from .logging_setup import get_logger

logger = get_logger(__name__)


class HttpClient:
    """带重试和限速的 HTTP 客户端。

    用法：
        client = HttpClient()
        data = client.get_json("https://example.com/api")

    建议用 with 语句，保证连接被正确关闭：
        with HttpClient() as client:
            data = client.get_json(url)
    """

    def __init__(
        self,
        timeout: int = config.REQUEST_TIMEOUT,
        retries: int = config.REQUEST_RETRIES,
        interval: float = config.REQUEST_INTERVAL,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.interval = interval
        self._last_request_at: float = 0.0  # 上次请求时间，用于算限速

        # Session 会复用 TCP 连接，比每次新建快很多
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )

    # ---------- 上下文管理器协议，让 with 语句可用 ----------
    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    # ---------- 内部：限速 ----------
    def _wait_for_slot(self) -> None:
        """确保两次请求之间至少间隔 interval 秒。

        为什么要限速？
        —— 一是避免给对方服务器造成压力（礼貌爬取），
           二是高频请求会触发风控导致 IP 被封，任务反而更慢。
        """
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)

    # ---------- 核心：带重试的 GET ----------
    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        """发起 GET 请求，失败会自动重试。

        重试策略：第 n 次失败后等待 2**(n-1) 秒，即 1s、2s、4s。
        这叫“指数退避”，比固定间隔更科学（给对方恢复时间）。
        """
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            self._wait_for_slot()
            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
                self._last_request_at = time.monotonic()
                resp.raise_for_status()  # 4xx/5xx 会抛异常，被下面的 except 接住
                return resp
            except requests.RequestException as exc:
                self._last_request_at = time.monotonic()
                last_error = exc
                wait = 2 ** (attempt - 1)
                logger.warning(
                    "请求失败（第 %d/%d 次）：%s，%d 秒后重试",
                    attempt, self.retries, exc.__class__.__name__, wait,
                )
                if attempt < self.retries:
                    time.sleep(wait)

        # 所有重试都失败，抛出最后一个异常，让上层决定怎么办
        raise RuntimeError(f"请求最终失败：{url}（{last_error}）") from last_error

    # ---------- 便捷方法 ----------
    def get_json(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> Any:
        """请求并解析 JSON。"""
        resp = self.get(url, params=params, headers=headers)
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            snippet = resp.text[:200].replace("\n", " ")
            raise RuntimeError(f"返回内容不是合法 JSON：{snippet}") from exc

    def get_text(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> str:
        """请求并返回文本（用于 HTML 页面）。"""
        return self.get(url, params=params, headers=headers).text
