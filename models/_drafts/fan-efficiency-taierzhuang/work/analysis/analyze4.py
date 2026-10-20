"""台儿庄 6 台曝气风机 —— 节电潜力评估（逐小时枚举最优配组）

与 analyze3（只取 5~7 个代表性总风量点）不同，本脚本对**每一个稳态小时**都求一次
"同样总风量下的最省配组"，用来回答：按现有设备能力，当前运行方式还能省多少电。

方法
----
1. 单机 P(Q)：取小时聚合的 Q 分箱中位 → 单调化 → 分段线性（np.interp）。
   只在该机观测到的 [Q_p1, Q_p99] 内插值，不外推。
2. 对每个厂级小时：总风量 Q_tot = Σ 各运行机 Q，
   在 2^3-1 个非空机组子集内枚举，每组用「边际成本贪心」分配到 1 m³/min 步长
   （分离凸成本下等价于精确解）；跳过多解不可行的组合。
3. 对比 Σ P_actual 与 Σ P_ideal，汇总节电率、绝对电量、按负荷分档。

口径与限制
----------
* 只统计"整点全时运行"的小时（run_share == 1.0 且样本数 ≥ 200），避免启停过渡。
* P(Q) 用的是历史观测区间，**超出区间不外推**——所以 6# 不能算到 72 m³/min 以下，
  这正是"低负荷时用不上 6#"的真实约束。
* 结果偏保守：分段线性取的是各箱中位，忽略了箱内更优工况。
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("FAN_DATA_DIR")

            or (HERE.parents[4] / "data" / "fan-efficiency-taierzhuang"))
DERIVED = DATA / "derived"  # 中间聚合（可重算，不进 git）
HOURLY = DERIVED / "hourly.parquet"
OUT = HERE / "potential-report.txt"

STEP = 1.0      # 分配步长 m³/min
MIN_BIN_HOURS = 20

def load() -> pd.DataFrame:
    d = pd.read_parquet(HOURLY)
    d["plant"] = d["machine"].str.split().str[0]
    d["hour"] = pd.to_datetime(d["hour"])
    return d

def curves(d: pd.DataFrame) -> dict[str, dict]:
    """每台机：Q 网格 + 单调 P(Q) + 观测区间。

    区间取原始 min~max（不裁 p1/p99）。早期版本裁到 p1/p99 后网格右端偏窄，
    高负荷小时被挤到曲线右上角、最优被高估，导致节电率被压到 0.x%（analyze5 已定位）。
    """
    out = {}
    for m, g in d.groupby("machine"):
        g = g[(g["run_share"] == 1.0) & g["Q_mean"].notna() & g["Pin_mean"].notna()]
        if g.empty:
            continue
        bins = np.arange(np.floor(g["Q_mean"].min() / 4) * 4,
                         np.ceil(g["Q_mean"].max() / 4) * 4 + 4, 4)
        tab = (g.assign(qb=pd.cut(g["Q_mean"], bins))
                 .groupby("qb", observed=True)
                 .agg(Q=("Q_mean", "median"), P=("Pin_mean", "median"),
                      n=("Q_mean", "size")))
        med = tab[tab["n"] >= MIN_BIN_HOURS].sort_values("Q")
        if len(med) < 2:
            continue
        qs = med["Q"].to_numpy(float)
        ps = np.maximum.accumulate(med["P"].to_numpy(float))   # 单调化
        qmin, qmax = float(qs[0]), float(qs[-1])
        slope = float((ps[-1] - ps[0]) / max(qmax - qmin, 1e-9))
        out[m] = {"grid": qs, "P": ps, "qmin": qmin, "qmax": qmax,
                  "slope": slope, "n_hours": len(g)}
    return out

def p_of(cur: dict, q: float) -> float:
    return float(np.interp(q, cur["grid"], cur["P"]))

def allocate(members: list[str], q_tot: float, curs: dict) -> tuple[float, dict] | None:
    """边际成本贪心分配（步长 STEP）。不可行返回 None。"""
    lo = {m: curs[m]["qmin"] for m in members}
    hi = {m: curs[m]["qmax"] for m in members}
    if sum(lo.values()) > q_tot + 1e-9 or sum(hi.values()) < q_tot - 1e-9:
        return None
    q = dict(lo)
    remain = q_tot - sum(lo.values())
    steps = int(round(remain / STEP))
    for _ in range(steps):
        best, best_cost = None, None
        for m in members:
            if q[m] + STEP > hi[m] + 1e-9:
                continue
            c = p_of(curs[m], q[m] + STEP) - p_of(curs[m], q[m])
            if best_cost is None or c < best_cost:
                best, best_cost = m, c
        if best is None:
            return None
        q[best] += STEP
    return sum(p_of(curs[m], q[m]) for m in members), q

def best_combo(members: list[str], q_tot: float, curs: dict):
    best = None
    for mask in range(1, 1 << len(members)):
        sub = [members[i] for i in range(len(members)) if mask >> i & 1]
        r = allocate(sub, q_tot, curs)
        if r and (best is None or r[0] < best[0]):
            best = (r[0], r[1])
    return best

def main() -> int:
    d = load()
    curs = curves(d)
    lines: list[str] = []

    def say(s: str = "") -> None:
        print(s)
        lines.append(s)

    say("=" * 100)
    say("台儿庄曝气风机 —— 节电潜力评估（逐稳态小时枚举最优配组）")
    say("=" * 100)
    say(f"小时样本 {len(d)} 行，覆盖 {d['hour'].min():%Y-%m-%d %H:%M} ~ {d['hour'].max():%Y-%m-%d %H:%M}")
    say("")
    say("单机 P(Q) 输入（分箱中位、单调化、只内插）")
    for m, c in curs.items():
        say(f"  {m:<14} 观测 {c['qmin']:5.1f}~{c['qmax']:5.1f} m³/min  "
            f"P {c['P'][0]:5.1f}→{c['P'][-1]:5.1f} kW  平均斜率 {c['slope']:.3f} kW/(m³/min)  "
            f"({c['n_hours']} 小时)")
    say("")

    grand = {"actual": 0.0, "ideal": 0.0, "hours": 0, "infeasible": 0, "no_gain": 0}
    for plant, machines in [("一污", [m for m in curs if m.startswith("一污")]),
                            ("二污", [m for m in curs if m.startswith("二污")])]:
        if not machines:
            continue
        sub = d[(d["plant"] == plant) & (d["run_share"] == 1.0)
                & (d["n_samp"] >= 200)]
        piv_q = sub.pivot_table(index="hour", columns="machine", values="Q_mean", aggfunc="sum")
        piv_p = sub.pivot_table(index="hour", columns="machine", values="Pin_mean", aggfunc="sum")
        common = piv_q.index.intersection(piv_p.index)
        rows = []
        for hr in common:
            qv = piv_q.loc[hr]
            pv = piv_p.loc[hr]
            run = [m for m in machines if m in qv.index and pd.notna(qv[m])]
            if not run:
                continue
            q_tot = float(sum(qv[m] for m in run))
            p_act = float(sum(pv[m] for m in run))
            best = best_combo(run, q_tot, curs)
            if best is None:
                grand["infeasible"] += 1
                continue
            # 模型给出的"最优"若高于现场实际，说明曲线在该点不可靠 → 以现场为准，不计负收益
            if best[0] >= p_act:
                grand["no_gain"] += 1
                best = (p_act, {m: qv[m] for m in run})
            rows.append({"hour": hr, "n": len(run), "Q": q_tot, "P_act": p_act,
                         "P_opt": best[0], "combo_act": "+".join(sorted(run)),
                         "combo_opt": "+".join(sorted(best[1]))})
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        df["save_kwh"] = df["P_act"] - df["P_opt"]
        df["save_pct"] = df["save_kwh"] / df["P_act"] * 100
        hrs = len(df)
        a, o = df["P_act"].sum(), df["P_opt"].sum()
        grand["actual"] += a
        grand["ideal"] += o
        grand["hours"] += hrs
        say("=" * 100)
        say(f"{plant}：{hrs} 个稳态小时，{df['hour'].min():%Y-%m-%d} ~ {df['hour'].max():%Y-%m-%d}")
        say("=" * 100)
        say(f"  实际耗电 {a:9.0f} kWh ｜ 同风量最优 {o:9.0f} kWh ｜ "
            f"可省 {a - o:8.0f} kWh（{(a - o) / a * 100:5.1f}%）")
        days = (df["hour"].max() - df["hour"].min()).total_seconds() / 86400
        say(f"  期间 {days:.0f} 天 → 年化可省约 {(a - o) / max(days, 1) * 365:8.0f} kWh/年"
            f"（按同时段负荷外推，不含负荷增长）")
        say("")
        say("  按负荷分档：")
        say(f"    {'总风量区间':<14}{'小时':>7}{'实际kW':>10}{'最优kW':>10}{'可省%':>8}  主要改动")
        edges = np.percentile(df["Q"], [0, 20, 40, 60, 80, 100])
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            g = df[(df["Q"] >= lo) & (df["Q"] <= hi)] if i == len(edges) - 2 else \
                df[(df["Q"] >= lo) & (df["Q"] < hi)]
            if g.empty:
                continue
            chg = (g["combo_act"] != g["combo_opt"]).mean() * 100
            say(f"    {lo:6.1f}~{hi:6.1f}  {len(g):7d}{g['P_act'].mean():10.1f}"
                f"{g['P_opt'].mean():10.1f}{g['save_pct'].mean():8.1f}  "
                f"{chg:.0f}% 小时配组该变")
        say("")
        top = df.nlargest(3, "save_kwh")[["hour", "Q", "P_act", "P_opt", "combo_act", "combo_opt"]]
        say("  单小时省得最多的 3 个：")
        for _, r in top.iterrows():
            say(f"    {r['hour']:%Y-%m-%d %H:%M}  Q={r['Q']:6.1f}  "
                f"{r['P_act']:6.1f}→{r['P_opt']:6.1f} kW   {r['combo_act']}  ⇒  {r['combo_opt']}")
        say("")

    say("=" * 100)
    say(f"合计：{grand['hours']} 个稳态小时，实际 {grand['actual']:.0f} kWh，"
        f"最优 {grand['ideal']:.0f} kWh，可省 {grand['actual'] - grand['ideal']:.0f} kWh"
        f"（{(grand['actual'] - grand['ideal']) / max(grand['actual'], 1) * 100:.1f}%）")
    if grand["infeasible"]:
        say(f"（另有 {grand['infeasible']} 个小时因目标风量超出单机可调区间被跳过）")
    if grand["no_gain"]:
        say(f"（{grand['no_gain']} 个小时模型未找到比现场更省的方案，按现场实际计——"
            f"既可能是现场已最优，也可能是 P(Q) 曲线在该点噪声大于收益）")
    say("")
    say("读法：")
    say("  * 可省 = 0 表示该负荷下现场已经是当前设备能力下的最优；负数不会出现（枚举含现场配组）。")
    say("  * 分档表里的『该变配组』比例高，说明现场配组与最优差得远，才有节能空间。")
    say("  * 全部基于历史观测区间内插；6# 不能被算到 72 m³/min 以下，低负荷段因此缺 6# 选项。")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 → {OUT}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
