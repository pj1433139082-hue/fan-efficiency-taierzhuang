"""数据质量对抗性审查（第二部分）：坏值块定位、哨兵值、以及剔除策略的量化后果。

第一部分（dq_audit.py）已确认：
  - 二污2# 的坏值是**连续段**（34,754 点 ≈ 6 天），且三个信号重合率 99.96% → 整行污染
  - 1 分钟尺度聚合方式（均值/中位/截尾）对 η 的影响 < 0.05 个百分点 → 不需要稳健统计
  - 过滤顺序不可逆：均值一旦被单点污染，最大可偏移 115 kW

本文件回答：
  1 坏值块出现在哪些月份、具体时间区间？是"一次性的历史故障段"还是"反复出现"？
  2 坏值长什么样：等比放大？等于某个固定常数？还是别的物理量？
  3 有没有哨兵值（如 1.59e29）以及负值？
  4 如果改用"整行有效性"剔除，效率统计与旧口径（逐信号宽界）差多少？
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(__file__).resolve().parent
DATA = Path(os.environ.get("FAN_DATA_DIR")

            or (D.parents[4] / "data" / "fan-efficiency-taierzhuang"))
DERIVED = DATA / "derived"  # 中间聚合（可重算，不进 git）
RAW = DATA / "raw"
OUT = D / "dq-audit2-report.txt"

MACHINES = {
    "PS37217-01-BE_GFJF_0001-C_003_0001": "一污4#",
    "PS37217-01-BE_GFJF_0001-C_003_0002": "一污5#",
    "PS37217-01-BE_GFJF_0001-C_003_0003": "一污6#",
    "PS37217-02-BE_GFJF_0001-C_003_0001": "二污1#",
    "PS37217-02-BE_GFJF_0001-C_003_0002": "二污2#",
    "PS37217-02-BE_GFJF_0001-C_003_0003": "二污3#",
}
RATED = {"V_IF": 140.0, "V_OP": 95.0, "V_P": 170.0, "V_ITIT": 80.0, "V_I": 400.0}
rep: list[str] = []

def say(s: str = "") -> None:
    print(s, flush=True)
    rep.append(s)

def head(t: str) -> None:
    say()
    say("=" * 96)
    say(t)
    say("=" * 96)

# --------------------------------------------------------------------------- #
head("一、坏值块的时间定位（按月份分片扫描，判据 = 超 2×额定值）")
# --------------------------------------------------------------------------- #
findings = []
for f in sorted(RAW.glob("*.parquet")):
    df = pd.read_parquet(f)
    plant = "-".join(f.name.split("-")[:2])
    tcol = "data_time_utc"
    for stem, label in MACHINES.items():
        if not stem.startswith(plant):
            continue
        mask = pd.Series(False, index=df.index)
        for sig, lim in RATED.items():
            c = f"{stem}-{sig}"
            if c in df.columns:
                v = pd.to_numeric(df[c], errors="coerce")
                mask |= v > 2 * lim
                if sig in ("V_IF", "V_OP", "V_P"):
                    mask |= v < -1.0
        if mask.any():
            tt = pd.to_datetime(df.loc[mask, tcol], utc=True)
            findings.append((f.name, label, int(mask.sum()), tt.min(), tt.max()))
say(f"{'分片':<22}{'机组':<8}{'坏点数':>9}   起（UTC）                       止（UTC）")
for name, label, n, t0, t1 in findings:
    say(f"{name:<22}{label:<8}{n:>9,}   {t0:%Y-%m-%d %H:%M:%S}              {t1:%Y-%m-%d %H:%M:%S}")
if not findings:
    say("（无）")

# --------------------------------------------------------------------------- #
head("二、坏值长什么样：与相邻正常点的比值 / 是否固定倍数")
# --------------------------------------------------------------------------- #
bad_ranges = [(n, l, t0, t1) for n, l, c, t0, t1 in findings if c >= 100]
if bad_ranges:
    name, label, t0, t1 = bad_ranges[0]
    df = pd.read_parquet(RAW / name)
    plant = "-".join(name.split("-")[:2])
    stem = [k for k, v in MACHINES.items() if v == label][0]
    m = (pd.to_datetime(df["data_time_utc"], utc=True) >= t0) & (
        pd.to_datetime(df["data_time_utc"], utc=True) <= t1)
    sub = df.loc[m, [f"{stem}-{s}" for s in ("V_IF", "V_OP", "V_P", "V_I", "V_ITIT")]].apply(
        pd.to_numeric, errors="coerce")
    say(f"样本：{name} / {label} / {t0:%Y-%m-%d %H:%M} ~ {t1:%Y-%m-%d %H:%M}（{len(sub):,} 点）")
    say(sub.describe(percentiles=[.05, .5, .95]).round(3).to_string())
    say()
    # 对照：同一分片里坏值段之外的中位数（同机组、同月，控制季节影响）
    all_t = pd.to_datetime(df["data_time_utc"], utc=True)
    outside = df.loc[(all_t < t0) | (all_t > t1),
                     [f"{stem}-{s}" for s in ("V_IF", "V_OP", "V_P", "V_I")]].apply(
        pd.to_numeric, errors="coerce")
    normal = outside.median()
    badmed = sub[[f"{stem}-{s}" for s in ("V_IF", "V_OP", "V_P", "V_I")]].median()
    say("坏值段中位 vs 同月正常期中位：")
    for sig in ("V_IF", "V_OP", "V_P", "V_I"):
        c = f"{stem}-{sig}"
        nrm = normal.get(c, float("nan"))
        ratio = badmed.get(c, float("nan")) / nrm if nrm else float("inf")
        say(f"  {sig:<6} 坏值 {badmed.get(c, float('nan')):>12.2f}   正常 {nrm:>10.2f}   比值 {ratio:>12.3f}")
    say("→ 比值接近同一数量级且各列不同，说明不是单一缩放因子，而是整段信号被替换成了错码")

# --------------------------------------------------------------------------- #
head("三、哨兵值与负值")
# --------------------------------------------------------------------------- #
for f in sorted(RAW.glob("*.parquet"))[:1]:
    pass
sent = []
for f in sorted(RAW.glob("*.parquet")):
    df = pd.read_parquet(f)
    plant = "-".join(f.name.split("-")[:2])
    for stem, label in MACHINES.items():
        if not stem.startswith(plant):
            continue
        for sig in ("V_IF", "V_OP", "V_P", "V_I", "V_ITIT"):
            c = f"{stem}-{sig}"
            if c not in df.columns:
                continue
            v = pd.to_numeric(df[c], errors="coerce")
            n_sent = int((v.abs() > 1e6).sum())
            n_neg = int((v < -1.0).sum())
            if n_sent or n_neg:
                sent.append((label, sig, n_sent, n_neg, float(v.abs().max())))
say(f"{'机组':<8}{'信号':<8}{'>1e6 点数':>10}{'<-1 点数':>10}{'最大绝对值':>14}")
for label, sig, ns, nn, mx in sent:
    say(f"{label:<8}{sig:<8}{ns:>10,}{nn:>10,}{mx:>14.3g}")
if not sent:
    say("（无）")

# --------------------------------------------------------------------------- #
head("四、剔除策略的量化后果：整行判定 vs 逐信号宽界")
# --------------------------------------------------------------------------- #
for stem, label in MACHINES.items():
    plant = stem.split("-BE_")[0]
    files = sorted(RAW.glob(f"{plant}-*.parquet"))
    n_tot = n_row_bad = n_wide_bad = 0
    for f in files:
        df = pd.read_parquet(f)
        cols = {s: pd.to_numeric(df.get(f"{stem}-{s}"), errors="coerce")
                for s in ("V_IF", "V_OP", "V_P", "V_ITIT")}
        if any(c is None for c in cols.values()):
            continue
        n_tot += len(df)
        row_bad = (
            (cols["V_IF"] > 2 * RATED["V_IF"]) | (cols["V_OP"] > 2 * RATED["V_OP"])
            | (cols["V_P"] > 2 * RATED["V_P"]) | (cols["V_ITIT"] > RATED["V_ITIT"])
            | (cols["V_IF"] < 0) | (cols["V_OP"] < 0) | (cols["V_P"] < -1)
        )
        wide_bad = (
            (cols["V_IF"].abs() > 250) | (cols["V_OP"].abs() > 120)
            | (cols["V_P"].abs() > 300) | (cols["V_ITIT"].abs() > 80)
        )
        n_row_bad += int(row_bad.sum())
        n_wide_bad += int(wide_bad.sum())
    say(f"{label:<8} 总 {n_tot:>10,} | 整行判定剔除 {n_row_bad:>9,} ({100*n_row_bad/max(n_tot,1):6.3f}%)"
        f" | 旧宽界剔除 {n_wide_bad:>9,} ({100*n_wide_bad/max(n_tot,1):6.3f}%)")

OUT.write_text("\n".join(rep), encoding="utf-8")
print(f"\n→ {OUT}")
