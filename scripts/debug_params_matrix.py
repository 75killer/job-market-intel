"""关键对照：请求参数差异 vs 请求库差异

排查过程中的一个重大教训（务必读）：
  我前几轮把"失败"归因到 Referer、Connection 头、HTTP 库，
  结果每次都只能"偶尔成功"——这说明**我一直在改错的变量**。

真正的排查方法：把可疑变量列成一张表，一次只改一个。
本脚本固定"用 urllib"这一个客户端，只改请求参数，做 2×2 对照：

  A. 小参数集（fs 只含深证主板，fields 少）    ← 之前成功过
  B. 大参数集（fs 含 4 个板块，fields 多）      ← 一直失败
  C. 小参数集 + pz=50（放大每页条数）
  D. 大参数集但拆成单板块（看是不是 fs 太长）

判读：
  如果 A 成功而 B 失败 → 根因是请求参数/URL 长度，与 HTTP 库无关
  如果 A、B 同时失败   → 根因在网络环境或服务端限流
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request

BASE = "https://push2.eastmoney.com/api/qt/clist/get"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "*/*",
           "Referer": "https://quote.eastmoney.com/"}


def probe(label: str, params: dict) -> bool:
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    print(f"\n{label}")
    print(f"    URL 长度 = {len(url)} 字符")
    print(f"    参数 = {params}")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
        n = body.count(b'"f12"')
        print(f"    ✅ {resp.status}，返回约 {n} 条")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"    ❌ {type(exc).__name__}: {str(exc)[:90]}")
        return False


COMMON = {"pn": 1, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f20"}

CASE_A = {**COMMON, "pz": 3, "fs": "m:0+t:6", "fields": "f12,f14,f20"}
CASE_B = {**COMMON, "pz": 3,
          "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
          "fields": "f12,f14,f2,f3,f8,f9,f20,f21,f23,f62,f100,f102,f103"}
CASE_C = {**COMMON, "pz": 50, "fs": "m:0+t:6", "fields": "f12,f14,f20"}
CASE_D = {**COMMON, "pz": 3, "fs": "m:1+t:2", "fields": "f12,f14,f20"}
CASE_E = {**COMMON, "pz": 3, "fs": "m:1+t:23", "fields": "f12,f14,f20"}

if __name__ == "__main__":
    print("=" * 72)
    print("对照组：固定 urllib，只改请求参数")
    print("=" * 72)
    results = {}
    results["A 小参数"] = probe("【A】fs=深证主板, pz=3", CASE_A)
    time.sleep(1.5)
    results["B 大参数"] = probe("【B】fs=4板块, 13字段, pz=3", CASE_B)
    time.sleep(1.5)
    results["C 小参数大页"] = probe("【C】fs=深证主板, pz=50", CASE_C)
    time.sleep(1.5)
    results["D 沪证主板"] = probe("【D】fs=沪证主板(m:1+t:2)", CASE_D)
    time.sleep(1.5)
    results["E 科创板"] = probe("【E】fs=科创板(m:1+t:23)", CASE_E)

    print("\n" + "=" * 72)
    print("结果汇总")
    print("=" * 72)
    for k, v in results.items():
        print(f"  {k:<16} {'✅ 成功' if v else '❌ 失败'}")
