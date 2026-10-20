"""台儿庄 风机组合优化：可省多少？

方法：
  1. 每台风机用「分箱中位」做单调分段线性 P(Q) 曲线（比二次拟合稳，且不外推）
  2. 枚举所有运行组合 × 组内风量分配（2 m³/min 步长），在观测区间内求最小总功率
  3. 与「现场实际运行」在同一总风量下对比

输出：work/analysis/combo-report.txt
"""
import os
import numpy as np
import pandas as pd
from pathlib import Path

D = Path(__file__).resolve().parent
DATA = Path(os.environ.get("FAN_DATA_DIR")

            or (D.parents[4] / "data" / "fan-efficiency-taierzhuang"))
DERIVED = DATA / "derived"  # 中间聚合（可重算，不进 git）
rep = []

def say(s=""):
    print(s, flush=True)
    rep.append(s)

MACH = {"一污 1期 4#": "一污", "一污 1期 5#": "一污", "一污 1期 6#": "一污",
        "二污 2期 1#": "二污", "二污 2期 2#": "二污", "二污 2期 3#": "二污"}

def build_curves(h):
    """返 {machine: (q_knots, p_knots)}，用分箱中位并保证单调。"""
    curves = {}
    for m in MACH:
        g = h[(h["machine"] == m) & h["Pin_mean"].notna() & h["Q_mean"].notna()]
        if len(g) < 50:
            continue
        q = g["Q_mean"]
        lo, hi = q.quantile([.01, .99])
        bins = np.arange(np.floor(lo / 5) * 5, np.ceil(hi / 5) * 5 + 5, 5)
        g = g.assign(qb=pd.cut(q, bins))
        tab = g.groupby("qb", observed=True).agg(
            n=("Q_mean", "size"), q=("Q_mean", "median"), p=("Pin_mean", "median"))
        tab = tab[tab["n"] >= 20].sort_values("q")
        if len(tab) < 3:
            continue
        qk, pk = tab["q"].values, tab["p"].values
        # 单调化（功率随风量单调不降）
        for i in range(1, len(pk)):
            if pk[i] < pk[i - 1]:
                pk[i] = pk[i - 1]
        curves[m] = (qk, pk, int(tab["n"].sum()))
    return curves

def p_of(curves, m, q):
    qk, pk, _ = curves[m]
    if q < qk[0] or q > qk[-1]:
        return None
    return float(np.interp(q, qk, pk))

def enumerate_best(curves, members, target, step=2.0):
    """枚举组合内分配，返回 (最小总功率, 分配dict, 是否全部在观测区间内)"""
    best = (None, None)
    ranges = []
    for m in members:
        qk = curves[m][0]
        lo = max(qk[0], 0)
        hi = qk[-1]
        grid = np.arange(np.ceil(lo / step) * step, hi + 1e-9, step)
        if not len(grid):
            return (None, None)
        ranges.append((m, grid))

    def rec(i, remain, acc, assign):
        nonlocal best
        if i == len(ranges):
            if abs(remain) > step * 0.51:
                return
            total = sum(acc)
            if best[0] is None or total < best[0]:
                best = (total, dict(assign))
            return
        m, grid = ranges[i]
        # 剪枝：剩余机器的最小可能功率
        for q in grid:
            if q > remain + step * 0.51:
                break
            p = p_of(curves, m, q)
            if p is None:
                continue
            assign[m] = q
            rec(i + 1, remain - q, acc + [p], assign)
            assign.pop(m, None)

    rec(0, target, [], {})
    return best

def main():
    h = pd.read_parquet(DERIVED / "hourly.parquet")
    h["plant"] = np.where(h["machine"].str.startswith("一污"), "一污", "二污")
    curves = build_curves(h)

    say("=" * 100)
    say("一、单机 P(Q) 曲线（分箱中位，单调化）")
    say("=" * 100)
    for m, (qk, pk, n) in curves.items():
        say(f"  {m:12s} n={n:6,d}  Q {qk[0]:.0f}→{qk[-1]:.0f}，"
            f"Pin {pk[0]:.1f}→{pk[-1]:.1f} kW，"
            f"平均斜率 {(pk[-1] - pk[0]) / (qk[-1] - qk[0]):.3f} kW/(m³/min)")

    # 现场实际
    h["is_run"] = h["run_share"] >= 0.99
    h["is_off"] = h["run_share"].fillna(0) <= 0.01
    mid = h[~(h["is_run"] | h["is_off"])].groupby(["plant", "hour"]).size()
    clean = h.merge(mid.rename("n_mid").reset_index(), on=["plant", "hour"], how="left")
    clean = clean[clean["n_mid"].isna()]
    run = clean[clean["is_run"]]
    pq = run.pivot_table(index=["plant", "hour"], columns="machine", values="Q_mean")
    pp = run.pivot_table(index=["plant", "hour"], columns="machine", values="Pin_mean")
    actual = pd.DataFrame({"Q_tot": pq.sum(axis=1, min_count=1),
                           "P_tot": pp.sum(axis=1, min_count=1),
                           "n_run": pq.notna().sum(axis=1)}).reset_index()
    actual = actual.dropna()

    say("\n" + "=" * 100)
    say("二、同总风量下：现场实际 vs 枚举最优")
    say("=" * 100)
    for plant in ["一污", "二污"]:
        members = [m for m, p in MACH.items() if p == plant and m in curves]
        a = actual[actual["plant"] == plant]
        if not len(a) or not members:
            continue
        say(f"\n{plant}：可用风机 {', '.join(members)}")
        qlo, qhi = a["Q_tot"].quantile([.05, .95])
        say(f"  现场总风量 p5~p95 = {qlo:.1f}~{qhi:.1f} m³/min")
        targets = [round(x, 1) for x in np.linspace(qlo, qhi, 7)]
        say(f"  {'目标总风量':>9s} {'现场实际':>16s} {'枚举最优':>20s} {'可省':>7s} {'最优配组':>26s}")
        for t in targets:
            # 现场：取 ±1.5 内的实际小时，取 P_tot 中位
            near = a[(a["Q_tot"] - t).abs() <= 1.5]
            if len(near) < 10:
                continue
            p_act = near["P_tot"].median()
            combos = []
            for r in range(1, len(members) + 1):
                import itertools
                for sub in itertools.combinations(members, r):
                    tot, assign = enumerate_best(curves, list(sub), t)
                    if tot is not None:
                        combos.append((tot, assign))
            if not combos:
                continue
            best_tot, best_assign = min(combos, key=lambda x: x[0])
            save = (1 - best_tot / p_act) * 100
            desc = " + ".join(f"{m.split()[-1]}={q:.0f}" for m, q in best_assign.items())
            say(f"  {t:9.1f} {p_act:15.1f}kW {best_tot:19.1f}kW {save:+6.1f}% {desc:>26s}")

    (D / "combo-report.txt").write_text("\n".join(rep), encoding="utf-8")
    say(f"\n报告 → {D / 'combo-report.txt'}")

if __name__ == "__main__":
    main()
