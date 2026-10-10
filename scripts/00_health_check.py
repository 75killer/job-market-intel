"""数据源健康巡检：在动手写采集代码之前，先确认"哪些源现在真的活着"。

为什么要写这个脚本？（这是很专业的做法）
  数据源会时不时挂掉、改接口、封 IP。如果每次都是"写代码 → 跑失败 → 再去查"，
  效率极低。正确做法是先做一次健康巡检，拿到一张"当前可用源"的清单，
  再决定项目用哪些源。

巡检结果会写入 docs/notes/数据源巡检报告.md，作为项目文档的一部分——
面试时你可以说"我为数据源做了健康巡检机制"，这是个加分项。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------
# 候选数据源清单：每个源记录"用途 + 是否关键"
# 关键源挂了要告警，非关键源挂了只记录。
# ---------------------------------------------------------
CANDIDATES: list[dict] = [
    {
        "name": "GitHub REST API",
        "url": "https://api.github.com/search/repositories",
        "params": {"q": "data science", "per_page": 2},
        "purpose": "开源项目热度数据（可分析技术趋势）",
        "critical": False,
    },
    {
        "name": "Open-Meteo 气象 API",
        "url": "https://api.open-meteo.com/v1/forecast",
        "params": {"latitude": 39.9, "longitude": 116.4,
                   "hourly": "temperature_2m", "forecast_days": 1},
        "purpose": "天气数据（可作为外部影响因子）",
        "critical": False,
    },
    {
        "name": "RestCountries API",
        "url": "https://restcountries.com/v3.1/all",
        "params": {"fields": "name,population,region,area"},
        "purpose": "国家基础数据（用于地域维度练习）",
        "critical": False,
    },
    {
        "name": "Remotive 岗位 API",
        "url": "https://remotive.com/api/remote-jobs",
        "params": {"limit": 2},
        "purpose": "真实岗位数据（含技能标签）",
        "critical": True,
    },
    {
        "name": "东方财富 行情接口",
        "url": "https://push2.eastmoney.com/api/qt/clist/get",
        "params": {"pn": 1, "pz": 2, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                   "fid": "f20", "fs": "m:0+t:6", "fields": "f12,f14"},
        "purpose": "A股上市公司行情与行业分类",
        "critical": True,
    },
    {
        "name": "东方财富 财务接口",
        "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
        "params": {"reportName": "RPT_LICO_FN_CPD",
                   "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,WEIGHTAVG_ROE",
                   "pageSize": 2, "pageNumber": 1},
        "purpose": "上市公司财务指标（ROE等）",
        "critical": True,
    },
    {
        "name": "HackerNews 招聘榜",
        "url": "https://hacker-news.firebaseio.com/v0/jobstories.json",
        "params": None,
        "purpose": "科技公司招聘动态",
        "critical": False,
    },
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def probe(candidate: dict) -> dict:
    """探测单个数据源。返回结果字典。"""
    name = candidate["name"]
    result = {"name": name, "purpose": candidate["purpose"],
              "critical": candidate["critical"], "ok": False,
              "status": None, "bytes": 0, "ms": 0, "error": None, "sample": ""}
    try:
        start = time.monotonic()
        resp = requests.get(
            candidate["url"], params=candidate["params"], timeout=20,
            headers={"User-Agent": UA, "Accept": "*/*",
                     "Referer": "https://quote.eastmoney.com/"},
        )
        result["ms"] = int((time.monotonic() - start) * 1000)
        result["status"] = resp.status_code
        result["bytes"] = len(resp.content)
        resp.raise_for_status()
        result["ok"] = True
        result["sample"] = resp.text[:150].replace("\n", " ")
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {str(exc)[:110]}"
    return result


def main() -> int:
    print("=" * 78)
    print(f"数据源健康巡检  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 78)

    results = []
    for c in CANDIDATES:
        r = probe(c)
        results.append(r)
        icon = "✅" if r["ok"] else "❌"
        crit = "关键" if r["critical"] else "可选"
        print(f"\n{icon} [{crit}] {r['name']}")
        print(f"     用途：{r['purpose']}")
        if r["ok"]:
            print(f"     {r['status']} | {r['bytes']} 字节 | {r['ms']} ms")
            print(f"     样例：{r['sample'][:110]}")
        else:
            print(f"     错误：{r['error']}")
        time.sleep(0.8)

    # ---------- 汇总 ----------
    alive = [r for r in results if r["ok"]]
    dead = [r for r in results if not r["ok"]]
    critical_down = [r["name"] for r in dead if r["critical"]]

    print("\n" + "=" * 78)
    print(f"巡检汇总：可用 {len(alive)}/{len(results)}")
    print("=" * 78)
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'}  {r['name']}")
    if critical_down:
        print(f"\n⚠️  关键源不可用：{', '.join(critical_down)}")
        print("   → 处理策略：启用本地缓存降级，不要阻塞整条流水线")
    else:
        print("\n✅ 所有关键源均可用")

    # ---------- 生成报告 ----------
    report = [
        "# 数据源巡检报告",
        "",
        f"巡检时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"**结果：{len(alive)}/{len(results)} 个数据源可用**",
        "",
        "| 数据源 | 用途 | 关键 | 状态 | 耗时 | 大小 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        status = "✅ 可用" if r["ok"] else "❌ 不可用"
        report.append(
            f"| {r['name']} | {r['purpose']} | {'是' if r['critical'] else '否'} "
            f"| {status} | {r['ms']} ms | {r['bytes']} B |"
        )
    report += ["", "## 失败详情", ""]
    for r in dead:
        report.append(f"- **{r['name']}**：`{r['error']}`")
    if not dead:
        report.append("（无）")

    out = PROJECT_ROOT / "docs" / "notes" / "数据源巡检报告.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"\n报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
