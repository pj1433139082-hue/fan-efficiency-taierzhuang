"""IoT 取数接口性能与上限实测（回答「是不是最快了 / 有没有限制」）

测四件事：
  A. maxSizePerNode 的真实上限（服务端钳在哪）
  B. 单请求延迟与负载大小（决定吞吐上限）
  C. 并发上限（加线程还快不快、有没有 429 / 5xx）
  D. 数据保留边界（更早还能不能取到）

用法：.venv-311\\Scripts\\python.exe bench.py
"""
import os
import time
import json
import statistics
import threading
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ["NO_PROXY"] = ",".join(
    [v for v in [os.environ.get("NO_PROXY", ""), "bewg.net.cn", ".bewg.net.cn"] if v]
)

TOKEN_URL = "https://saas-iam.bewg.net.cn/realms/bewg/protocol/openid-connect/token"
API = "https://be-iot-saas.bewg.net.cn/ts/v3/datapoint/history/rawvalue"
CLIENT_ID = os.environ.get("IOT_CLIENT_ID", "ai-console")
CLIENT_SECRET = os.environ.get("IOT_CLIENT_SECRET", "")
TENANT = os.environ.get("IOT_TENANT", "bewg")

PLANT = "PS37217-02"
SIGS = ["V_IF", "V_OP", "V_P", "V_ITIT", "V_I", "V_MTIT", "S_RUN", "S_AUT"]
MACHINES = ["0001", "0002", "0003"]
NODES = [{"id": s, "namespace": f"{PLANT}-BE_GFJF_0001-C_003_{i}"}
         for i in MACHINES for s in SIGS]

_TL = threading.local()
_TOK = {"v": None, "t": 0.0}
_LK = threading.Lock()


def sess():
    if not hasattr(_TL, "s"):
        s = requests.Session()
        s.trust_env = False
        s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16))
        _TL.s = s
    return _TL.s


def token():
    with _LK:
        if _TOK["v"] and time.time() - _TOK["t"] < 2400:
            return _TOK["v"]
        r = requests.post(TOKEN_URL, data={
            "grant_type": "client_credentials", "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET}, timeout=(5, 30))
        r.raise_for_status()
        _TOK["v"] = r.json()["access_token"]
        _TOK["t"] = time.time()
        return _TOK["v"]


def ms(dt):
    return int(dt.timestamp() * 1000)


def call(start, hours, max_size, nodes=None, retries=2):
    """返回 (ok, rows, seconds, bytes, status)"""
    end = start + timedelta(hours=hours)
    body = {"detail": {"returnBounds": False, "endTime": str(ms(end)),
                       "maxSizePerNode": max_size, "startTime": str(ms(start))},
            "nodes": nodes or NODES}
    last = None
    for a in range(retries):
        try:
            t0 = time.time()
            r = sess().post(API, headers={"Content-Type": "application/json",
                                          "X-Tenant-Id": TENANT,
                                          "Authorization": f"Bearer {token()}"},
                            json=body, timeout=(5, 60))
            el = time.time() - t0
            if r.status_code != 200:
                last = (False, 0, el, len(r.content), r.status_code)
                time.sleep(0.3 * (a + 1))
                continue
            d = r.json()
            if d.get("code") != 0:
                last = (False, 0, el, len(r.content), f"code={d.get('code')}")
                continue
            results = d.get("results") or []
            rows = 0
            per_node = []
            for res in results:
                if isinstance(res, dict):
                    hd = res.get("historyData") or []
                    rows += len(hd)
                    per_node.append(len(hd))
            return (True, rows, el, len(r.content), max(per_node) if per_node else 0)
        except Exception as e:
            last = (False, 0, 0.0, 0, type(e).__name__)
            time.sleep(0.3 * (a + 1))
    return last


BASE = datetime(2026, 9, 10, 0, 0, 0, tzinfo=timezone(timedelta(hours=8)))
out = []


def say(s=""):
    print(s, flush=True)
    out.append(s)


def main():
    say("=" * 92)
    say("A. maxSizePerNode 的真实上限（8h 窗口 × 26 测点）")
    say("=" * 92)
    say(f"{'请求值':>8s} {'每测点返回':>10s} {'总行数':>8s} {'耗时':>7s} {'响应':>8s} {'状态':>10s}")
    for n in (2000, 2001, 2500, 3000, 5000, 10000, 50000):
        ok, rows, el, size, per = call(BASE, 8, n)
        say(f"{n:8d} {per if ok else 0:10d} {rows:8d} {el:6.2f}s {size / 1024:7.0f}K "
            f"{'OK' if ok else str(per):>10s}")
        time.sleep(0.2)

    say("\n" + "=" * 92)
    say("B. 窗口长度 vs 每测点返回（固定 maxSizePerNode=2000）")
    say("=" * 92)
    say(f"{'窗口':>8s} {'理论点数':>10s} {'实际点数':>10s} {'总行数':>8s} {'耗时':>7s} {'响应':>8s}")
    for h in (1, 4, 6, 8, 9, 12, 24):
        ok, rows, el, size, per = call(BASE, h, 2000)
        theo = int(h * 3600 / 15)
        say(f"{h:7d}h {theo:10d} {per if ok else 0:10d} {rows:8d} {el:6.2f}s {size / 1024:7.0f}K")
        time.sleep(0.2)

    say("\n" + "=" * 92)
    say("C. 并发扫描（每窗 8h，共 48 窗 = 4 天 × 26 测点）")
    say("=" * 92)
    starts = [BASE + timedelta(hours=8 * i) for i in range(48)]
    for workers in (4, 8, 16, 24, 32, 48, 64):
        t0 = time.time()
        res = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(call, s, 8, 2000) for s in starts]
            for f in as_completed(futs):
                res.append(f.result())
        el = time.time() - t0
        okc = sum(1 for r in res if r[0])
        rows = sum(r[1] for r in res)
        lat = [r[2] for r in res if r[0]]
        errs = [r[4] for r in res if not r[0]]
        say(f"  并发 {workers:3d}: {okc:3d}/{len(starts)} 成功  {rows:7d} 行  "
            f"{el:6.1f}s  → {rows / el:8.0f} 行/s  "
            f"p50 {statistics.median(lat):.2f}s  max {max(lat):.2f}s  "
            f"错误 {errs[:3] if errs else 0}")
        time.sleep(1)

    say("\n" + "=" * 92)
    say("D. 数据保留边界（各取 8h）")
    say("=" * 92)
    now = datetime(2026, 10, 17, 16, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    for months in (1, 6, 12, 18, 24, 25, 30, 36, 48):
        st = now - timedelta(days=int(months * 30.4))
        ok, rows, el, size, per = call(st, 8, 2000)
        say(f"  {months:3d} 个月前 ({st:%Y-%m-%d})：{per if ok else 0:5d} 点/测点  "
            f"{rows:7d} 行  {el:5.2f}s")
        time.sleep(0.2)

    print("\n" + "=" * 92)
    print("结论摘要")
    print("=" * 92)
    worst = None
    Path = __import__("pathlib").Path
    (Path(__file__).resolve().parent / "bench-report.txt").write_text(
        "\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
