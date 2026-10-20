"""诊断：为什么 analyze4（逐小时）说省 0.2%，而 analyze3（代表性点）说省 4.6~11.4%？

怀疑点：
  (a) analyze4 的曲线区间取「分箱中位的 min/max」，比原始 Q 的 p1/p99 更窄，
      高负荷段被挤到曲线右上角，导致"最优"被高估；
  (b) P(Q) 二维曲线混合了不同 dp / T 的工况，同一 Q 的中位功率不代表该小时可达的最低功率。

做法：放宽曲线区间到原始 min/max，并在同一 (Q_tot, dp) 邻域内做经验包络对照。
"""
from __future__ import annotations

from pathlib import Path

import os
import numpy as np
import pandas as pd

D = Path(__file__).resolve().parent
DATA = Path(os.environ.get("FAN_DATA_DIR")

            or (D.parents[4] / "data" / "fan-efficiency-taierzhuang"))
DERIVED = DATA / "derived"  # 中间聚合（可重算，不进 git）
h = pd.read_parquet(DERIVED / "hourly.parquet")
h["plant"] = h["machine"].str.split().str[0]
h["hour"] = pd.to_datetime(h["hour"])

run = h[(h["run_share"] == 1.0) & (h["n_samp"] >= 200)].copy()

def build(machine: str, qlo_q: float = 0.0, qhi_q: float = 1.0, binw: float = 4.0):
    g = run[run["machine"] == machine]
    q = g["Q_mean"]
    lo, hi = q.quantile([qlo_q, qhi_q])
    gg = g[(q >= lo) & (q <= hi)]
    bins = np.arange(np.floor(lo / binw) * binw, np.ceil(hi / binw) * binw + binw, binw)
    tab = (gg.assign(qb=pd.cut(gg["Q_mean"], bins))
             .groupby("qb", observed=True)
             .agg(n=("Q_mean", "size"), Q=("Q_mean", "median"), P=("Pin_mean", "median"))
             .query("n >= 15").sort_values("Q"))
    qk, pk = tab["Q"].to_numpy(float), tab["P"].to_numpy(float)
    pk = np.maximum.accumulate(pk)
    return {"q": qk, "p": pk, "n": int(tab["n"].sum())}

MACH = ["一污 1期 4#", "一污 1期 5#", "一污 1期 6#",
        "二污 2期 1#", "二污 2期 2#", "二污 2期 3#"]

print("=" * 96)
print("一、曲线区间对比：p1~p99 截断 vs 原始 min~max")
print("=" * 96)
print(f"{'机':<12}{'原Q p1~p99':>18}{'原Q min~max':>20}{'bins(p1~p99)':>14}{'bins(full)':>12}")
curves_narrow, curves_full = {}, {}
for m in MACH:
    g = run[run["machine"] == m]
    q1, q99 = g["Q_mean"].quantile([.01, .99])
    curves_narrow[m] = build(m, .01, .99)
    curves_full[m] = build(m, 0.0, 1.0)
    cn, cf = curves_narrow[m], curves_full[m]
    print(f"{m:<12}{q1:7.1f}~{q99:6.1f}{g['Q_mean'].min():10.1f}~{g['Q_mean'].max():6.1f}"
          f"{cn['q'][0]:7.1f}~{cn['q'][-1]:6.1f}{cf['q'][0]:6.1f}~{cf['q'][-1]:6.1f}")

def p_of(c, q):
    if q < c["q"][0] - 1e-9 or q > c["q"][-1] + 1e-9:
        return None
    return float(np.interp(q, c["q"], c["p"]))

def best(curves, members, q_tot, step=1.0):
    lo = {m: curves[m]["q"][0] for m in members}
    hi = {m: curves[m]["q"][-1] for m in members}
    if sum(lo.values()) > q_tot or sum(hi.values()) < q_tot:
        return None
    q = dict(lo)
    remain = q_tot - sum(lo.values())
    for _ in range(int(round(remain / step))):
        pick, cost = None, None
        for m in members:
            if q[m] + step > hi[m] + 1e-9:
                continue
            c = p_of(curves[m], q[m] + step) - p_of(curves[m], q[m])
            if cost is None or c < cost:
                pick, cost = m, c
        if pick is None:
            return None
        q[pick] += step
    return sum(p_of(curves[m], q[m]) for m in members), q

print()
print("=" * 96)
print("二、一个真实高负荷小时的逐机读数（一污 Q_tot 最大的一小时）")
print("=" * 96)
sub = run[run["plant"] == "一污"]
piv = sub.pivot_table(index="hour", columns="machine", values=["Q_mean", "Pin_mean", "dp_mean"])
qtot = piv[("Q_mean", "一污 1期 4#")].fillna(0) + piv[("Q_mean", "一污 1期 5#")].fillna(0) \
    + piv[("Q_mean", "一污 1期 6#")].fillna(0)
hi_hour = qtot.idxmax()
row = piv.loc[hi_hour]
for m in MACH[:3]:
    try:
        print(f"  {m:<12} Q={row[('Q_mean', m)]:6.1f}  Pin={row[('Pin_mean', m)]:6.1f}  "
              f"dp={row[('dp_mean', m)]:5.1f}")
    except KeyError:
        print(f"  {m:<12} 未运行")
print(f"  → 合计 Q={qtot.loc[hi_hour]:.1f}  Pin={piv.loc[hi_hour][('Pin_mean', '一污 1期 4#')] + piv.loc[hi_hour][('Pin_mean', '一污 1期 5#')]:.1f}")

print()
print("=" * 96)
print("三、同 (Q_tot, dp) 邻域的经验包络：现场实际 vs 同工况实际最低")
print("=" * 96)
print("  只看**真实观测到的小时**，不做曲线外推：")
print("  对每个小时，在 (Q_tot±1, dp±1.5) 邻域内取实际 P_tot 的最小值，")
print("  若该最小值显著低于本小时，说明同工况下确实有更省的运行方式。")
print()
for plant, members in [("一污", MACH[:3]), ("二污", MACH[3:])]:
    s = run[run["plant"] == plant]
    pq = s.pivot_table(index="hour", columns="machine", values="Q_mean", aggfunc="sum")
    pp = s.pivot_table(index="hour", columns="machine", values="Pin_mean", aggfunc="sum")
    pd_ = s.pivot_table(index="hour", columns="machine", values="dp_mean", aggfunc="mean")
    df = pd.DataFrame({"Q": pq.sum(axis=1, min_count=1),
                       "P": pp.sum(axis=1, min_count=1),
                       "dp": pd_.mean(axis=1)}).dropna()
    df = df.reset_index()
    df["band"] = pd.cut(df["Q"], bins=5)
    print(f"  {plant}：{len(df)} 小时")
    print(f"    {'Q_tot 区间':<16}{'小时':>7}{'实际kW':>9}{'同工况最低kW':>13}{'差距%':>8}{'差距>3%占比':>12}")
    for b, g in df.groupby("band", observed=True):
        q, p, dp = g["Q"].to_numpy(), g["P"].to_numpy(), g["dp"].to_numpy()
        env = np.empty(len(g))
        for i in range(len(g)):
            m = (np.abs(q - q[i]) <= 1.0) & (np.abs(dp - dp[i]) <= 1.5)
            env[i] = p[m].min() if m.sum() >= 1 else np.nan
        gap = (p - env) / p * 100
        print(f"    {b.left:6.1f}~{b.right:6.1f}{len(g):9d}{p.mean():9.1f}"
              f"{np.nanmean(env):13.1f}{np.nanmean(gap):8.2f}{(gap > 3).mean() * 100:11.0f}%")
    print()
