"""探测东方财富财务数据接口：确认我们能拿到哪些真实字段。

背景：
  行情接口（push2 域名）被限流，但财务接口（datacenter-web 域名）正常。
  在决定项目方向之前，必须先搞清楚这个接口到底能给什么，
  否则可能白写一堆代码。

要查清三件事：
  1. 有哪些报表可用（业绩报表、资产负债表、利润表……）
  2. 字段是否包含"行业分类"（有了行业，才能做分组对比分析）
  3. 单次能取多少条、分页是否正常
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*",
           "Referer": "https://data.eastmoney.com/"}


def query(report_name: str, columns: str = "ALL", page_size: int = 3,
          page: int = 1, extra: dict | None = None) -> dict:
    params = {
        "reportName": report_name,
        "columns": columns,
        "pageSize": page_size,
        "pageNumber": page,
        "source": "WEB",
        "client": "WEB",
    }
    if extra:
        params.update(extra)
    resp = requests.get(BASE, params=params, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    return resp.json()


def show(title: str, report: str, columns: str = "ALL",
         page_size: int = 2) -> None:
    print("\n" + "=" * 78)
    print(f"{title}")
    print(f"  reportName = {report}")
    print("=" * 78)
    try:
        payload = query(report, columns=columns, page_size=page_size)
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 请求失败：{type(exc).__name__}: {str(exc)[:120]}")
        return

    result = payload.get("result") or {}
    data = result.get("data") or []
    print(f"  ✅ 成功 | 总记录数 pages={result.get('pages')} | 本页 {len(data)} 条")
    if not data:
        print("  （无数据，可能报表名不对）")
        return

    # 打印第一条记录的所有字段名和值——这是最有用的信息
    first = data[0]
    print(f"\n  该报表共 {len(first)} 个字段，清单如下：")
    for i, (k, v) in enumerate(first.items(), 1):
        sval = str(v)
        if len(sval) > 46:
            sval = sval[:46] + "…"
        print(f"    {i:>3}. {k:<34} = {sval}")


if __name__ == "__main__":
    print("探测东方财富财务数据接口（datacenter-web 域名）")

    # 1) 业绩报表（之前验证过可用）
    show("① 业绩报表 RPT_LICO_FN_CPD（含 ROE / 营收 / 净利润）",
         "RPT_LICO_FN_CPD")

    # 2) 看看能不能带上行业分类
    show("② 业绩报表 + 指定列（测试行业字段是否可查）",
         "RPT_LICO_FN_CPD",
         columns=("SECURITY_CODE,SECURITY_NAME_ABBR,WEIGHTAVG_ROE,"
                  "BASIC_EPS,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,"
                  "INDUSTRY_NAME,TRADE_MARKET"))

    # 3) 资产负债表
    time.sleep(1)
    show("③ 资产负债表 RPT_DMSK_FN_BALANCE", "RPT_DMSK_FN_BALANCE")

    # 4) 利润表
    time.sleep(1)
    show("④ 利润表 RPT_DMSK_FN_INCOME", "RPT_DMSK_FN_INCOME")

    # 5) 分红送配（"高股息"是很好的分析主题）
    time.sleep(1)
    show("⑤ 分红送转 RPT_SHAREBONUS_DET", "RPT_SHAREBONUS_DET")

    print("\n" + "=" * 78)
    print("判读要点：")
    print("  1. 找出包含“行业/板块/所属行业”字样的字段 → 有它就能做分组分析")
    print("  2. 找出金额类字段（营收/净利润/资产）→ 有它就能做规模分析")
    print("  3. 找出比率类字段（ROE/EPS/毛利率）→ 有它就能做质量分析")
    print("=" * 78)
