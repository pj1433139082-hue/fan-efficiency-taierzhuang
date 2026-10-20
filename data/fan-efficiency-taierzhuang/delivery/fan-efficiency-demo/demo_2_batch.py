# -*- coding: utf-8 -*-
"""演示二：读一天的 15 秒原始数据，按分钟聚合成效率序列，再给出统计。

用法：
    python demo_2_batch.py                      # 用自带的 sample-data.csv
    python demo_2_batch.py 你的文件.csv          # 用你自己的数据
CSV 需要的列：data_time_bj, V_IF, V_OP, V_P, V_ITIT, S_RUN
（单位：m3/min, kPa, kW, ℃, 0/1；时间格式 2026-09-15 00:00:00）
"""
import csv
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime

import model as fan

ELEV = 38.5
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample-data.csv")

buckets = defaultdict(list)
last_ts = {}
with open(path, encoding="utf-8-sig") as fh:
    for row in csv.DictReader(fh):
        raw = row["data_time_bj"].strip()
        try:
            ts = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            ts = datetime.fromisoformat(raw.replace(" ", "T")).replace(tzinfo=None)
        minute = ts.replace(second=0)
        prev = last_ts.get(minute)
        dt = (ts - prev).total_seconds() if prev else 15.0
        dt = min(max(dt, 1.0), 300.0)
        last_ts[minute] = ts
        def num(key):
            try:
                return float(row[key])
            except (ValueError, KeyError):
                return float("nan")
        buckets[minute].append({
            "inlet_flow": num("V_IF"), "outlet_pressure": num("V_OP"),
            "motor_power": num("V_P"), "inlet_temperature": num("V_ITIT"),
            "elevation": ELEV, "run_flag": num("S_RUN"), "dt_s": dt,
        })

quality = defaultdict(int)
eta_e, eta_c, dev, power = [], [], [], []
for minute in sorted(buckets):
    agg = fan.aggregate_minute(buckets[minute])
    quality[agg["quality"]] += 1
    if agg["quality"] != fan.VALID:
        continue
    r = fan.compute_row(agg["inlet_flow"], agg["outlet_pressure"], agg["motor_power"],
                        agg["inlet_temperature"], agg["elevation"])
    eta_e.append(r["eta_excel"]*100)
    eta_c.append(r["eta_corrected"]*100)
    dev.append((r["eta_excel"]/r["eta_corrected"]-1)*100)
    power.append(r["wp_corrected"])

def stat(values, unit=""):
    if not values:
        return "无数据"
    return "中位 %.2f%s / 最小 %.2f / 最大 %.2f" % (statistics.median(values),
                                                unit, min(values), max(values))

print("=" * 78)
print("数据文件:", os.path.basename(path), "| 分钟数:", len(buckets))
print("=" * 78)
print("质量标记分布:")
for code, n in sorted(quality.items(), key=lambda kv: -kv[1]):
    print("  %-14s %5d 分钟" % (code, n))
print()
print("有效分钟数:", len(eta_e))
print("Excel 口径效率 :", stat(eta_e, "%"))
print("修正口径效率   :", stat(eta_c, "%"))
print("两口径偏差     :", stat(dev, "%"), "（负数＝Excel 算低了）")
print("修正口径气动功率:", stat(power, " kW"))
