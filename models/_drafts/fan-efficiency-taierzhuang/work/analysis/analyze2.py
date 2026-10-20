"""台儿庄 6 台曝气风机 —— 建模可用性分析

输入：data/fan-efficiency-taierzhuang/hourly.parquet（analyze.py 产出）
输出：work/analysis/modeling-report.txt

回答三个问题：
  Q1 风量-效率曲线能不能建？
  Q2 多台同时运行的样本够不够？（组合优化的证据基础）
  Q3 同样的总风量，不同配组的能耗差多大？

口径：只用「稳定小时」——某机 run_share>=0.99 记为全时运行，run_share<=0.01 记为全时停机，
      其余（启停过渡小时）整小时剔除，避免台数误判。
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

MACH = ["一污 1期 4#", "一污 1期 5#", "一污 1期 6#",
        "二污 2期 1#", "二污 2期 2#", "二污 2期 3#"]

def q1(h):
    say("\n" + "=" * 100)
    say("Q1  风量-效率曲线：每台风机按入口风量分箱（小时级）")
    say("=" * 100)
    for m in MACH:
        g = h[(h["machine"] == m) & h["Q_mean"].notna() & h["eta_mean"].notna()]
        if len(g) < 20:
            say(f"\n{m}：样本不足（{len(g)}）")
            continue
        q = g["Q_mean"]
        lo, hi = q.quantile([.01, .99])
        bins = np.arange(np.floor(lo / 5) * 5, np.ceil(hi / 5) * 5 + 5, 5)
        if len(bins) < 2:
            continue
        g = g.assign(qb=pd.cut(q, bins))
        say(f"\n{m}  总 {len(g):,} 小时，Q p1~p99 = {lo:.1f}~{hi:.1f} m³/min")
        say(f"  {'风量区间':>10s} {'小时':>6s} {'η中位':>7s} {'η_p25':>7s} {'η_p75':>7s} "
            f"{'Pin中位':>8s} {'dp中位':>7s} {'T中位':>7s}")
        rows = []
        for b, gg in g.groupby("qb", observed=True):
            if len(gg) < 10:
                continue
            say(f"  {f'{b.left:.0f}-{b.right:.0f}':>10s} {len(gg):6,d} "
                f"{gg['eta_mean'].median() * 100:6.2f}% "
                f"{gg['eta_mean'].quantile(.25) * 100:6.2f}% "
                f"{gg['eta_mean'].quantile(.75) * 100:6.2f}% "
                f"{gg['Pin_mean'].median():8.1f} {gg['dp_mean'].median():7.1f} "
                f"{gg['T_mean'].median():7.1f}")
            rows.append((b.left, b.right, len(gg), gg["eta_mean"].median()))
        if len(rows) >= 3:
            best = max(rows, key=lambda r: r[3])
            worst = min(rows, key=lambda r: r[3])
            say(f"  → 最高效 {best[0]:.0f}-{best[1]:.0f} m³/min（η {best[3] * 100:.1f}%），"
                f"最低 {worst[0]:.0f}-{worst[1]:.0f}（η {worst[3] * 100:.1f}%），"
                f"差 {(best[3] - worst[3]) * 100:.1f} 个百分点")

def stable_table(h):
    """把小时级数据展成 厂级×小时 的稳定表。"""
    h = h.copy()
    h["is_run"] = h["run_share"] >= 0.99
    h["is_off"] = h["run_share"].fillna(0) <= 0.01
    h["is_mid"] = ~(h["is_run"] | h["is_off"])

    # 有过渡小时的 厂-小时 整行剔除
    mid = h[h["is_mid"]].groupby(["plant", "hour"]).size()
    clean = h.merge(mid.rename("n_mid").reset_index(), on=["plant", "hour"], how="left")
    clean = clean[clean["n_mid"].isna()]

    run = clean[clean["is_run"]]
    piv_q = run.pivot_table(index=["plant", "hour"], columns="machine", values="Q_mean")
    piv_p = run.pivot_table(index=["plant", "hour"], columns="machine", values="Pin_mean")
    piv_w = run.pivot_table(index=["plant", "hour"], columns="machine", values="W_mean")
    n = piv_q.notna().sum(axis=1)
    out = pd.DataFrame({
        "n_run": n,
        "Q_tot": piv_q.sum(axis=1, min_count=1),
        "P_tot": piv_p.sum(axis=1, min_count=1),
        "W_tot": piv_w.sum(axis=1, min_count=1),
    })
    for m in MACH:
        if m in piv_q.columns:
            out[f"Q_{m}"] = piv_q[m]
    out = out.reset_index()
    out["eta_sys"] = out["W_tot"] / out["P_tot"]
    out["kw_per_q"] = out["P_tot"] / out["Q_tot"]
    return out

def q2(st):
    say("\n" + "=" * 100)
    say("Q2  多台同时运行样本（稳定小时，已剔除启停过渡）")
    say("=" * 100)
    for p in ["一污", "二污"]:
        sub = st[st["plant"] == p]
        if not len(sub):
            continue
        say(f"\n{p}：{len(sub):,} 个稳定小时")
        say(f"  {'开机台数':>6s} {'小时':>7s} {'占比':>7s} {'总风量中位':>10s} "
            f"{'总功率中位':>10s} {'系统η中位':>9s} {'kW/(m³/min)':>12s}")
        for n in sorted(sub["n_run"].unique()):
            gg = sub[sub["n_run"] == n].dropna(subset=["Q_tot", "P_tot"])
            if len(gg) < 3:
                continue
            say(f"  {int(n):6d} {len(gg):7,d} {len(gg) / len(sub) * 100:6.1f}% "
                f"{gg['Q_tot'].median():10.1f} {gg['P_tot'].median():10.1f} "
                f"{gg['eta_sys'].median() * 100:8.2f}% {gg['kw_per_q'].median():12.3f}")
        # 具体是哪些机在跑
        cols = [c for c in sub.columns
                if c.startswith("Q_") and c != "Q_tot" and sub[c].notna().any()]
        if cols:
            say(f"  运行组合分布（前 8 种）：")
            combo = sub[cols].notna().apply(
                lambda r: "+".join(c[2:] for c in cols if r[c]), axis=1)
            for k, v in combo.value_counts().head(8).items():
                say(f"    {v:6,d} ({v / len(sub) * 100:5.1f}%)  {k}")

def q3(st):
    say("\n" + "=" * 100)
    say("Q3  同样的总风量，不同配组的能耗差（组合模型的空间）")
    say("=" * 100)
    for p in ["一污", "二污"]:
        sub = st[(st["plant"] == p) & (st["n_run"] >= 1) & (st["Q_tot"] > 0)].dropna(
            subset=["Q_tot", "P_tot", "kw_per_q"])
        if len(sub) < 50:
            continue
        say(f"\n{p}：{len(sub):,} 个稳定小时")
        qs = sub["Q_tot"].quantile([.2, .4, .6, .8])
        edges = [0] + list(qs.values) + [sub["Q_tot"].max() + 1]
        sub = sub.assign(qb=pd.cut(sub["Q_tot"], bins=edges))
        say(f"  {'总风量区间':>14s} {'台数':>4s} {'小时':>6s} {'总功率中位':>10s} "
            f"{'kW/(m³/min)':>12s}")
        for qb, gq in sub.groupby("qb", observed=True):
            stats = []
            for n, gg in gq.groupby("n_run"):
                if len(gg) < 10:
                    continue
                med = gg["kw_per_q"].median()
                stats.append((n, len(gg), gg["P_tot"].median(), med))
                say(f"  {f'{qb.left:.0f}-{qb.right:.0f}':>14s} {int(n):4d} {len(gg):6,d} "
                    f"{gg['P_tot'].median():10.1f} {med:12.3f}")
            if len(stats) >= 2:
                vals = [s[3] for s in stats]
                best, worst = min(vals), max(vals)
                say(f"  {'':>14s} → 同风量下最优配组 {best:.3f} vs 最差 {worst:.3f}，"
                    f"省 {(1 - best / worst) * 100:.1f}%")

def q4(st):
    """单机 P-Q 曲线：组合模型需要的输入"""
    say("\n" + "=" * 100)
    say("Q4  单机 P-Q 与 η-Q 关系式（组合优化所需输入的可拟合性）")
    say("=" * 100)
    for m in MACH:
        col = f"Q_{m}"
        if col not in st.columns:
            continue
        sub = st[[col, f"P_tot"]].copy() if False else None
    # 用小时级机级数据直接拟合
    h = pd.read_parquet(DERIVED / "hourly.parquet")
    for m in MACH:
        g = h[(h["machine"] == m) & h["Q_mean"].notna() & h["Pin_mean"].notna()]
        if len(g) < 100:
            continue
        x = g["Q_mean"].values
        y = g["Pin_mean"].values
        # 二次拟合 Pin = a*Q^2 + b*Q + c
        coef = np.polyfit(x, y, 2)
        pred = np.polyval(coef, x)
        ss_res = ((y - pred) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot
        qmin, qmax = np.percentile(x, [1, 99])
        say(f"  {m:12s} Pin ≈ {coef[0]:+.5f}·Q² {coef[1]:+.4f}·Q {coef[2]:+.2f}   "
            f"R²={r2:.4f}  n={len(g):,}  有效 Q {qmin:.1f}~{qmax:.1f}")
        for qq in (qmin, (qmin + qmax) / 2, qmax):
            say(f"      Q={qq:6.1f} → Pin≈{np.polyval(coef, qq):7.2f} kW，"
                f"实测中位 {np.median(y[np.abs(x - qq) < 1]):7.2f} kW"
                if (np.abs(x - qq) < 1).sum() >= 5 else
                f"      Q={qq:6.1f} → Pin≈{np.polyval(coef, qq):7.2f} kW")

def main():
    h = pd.read_parquet(DERIVED / "hourly.parquet")
    h["plant"] = np.where(h["machine"].str.startswith("一污"), "一污", "二污")
    say(f"小时聚合 {len(h):,} 行，{h['hour'].min():%Y-%m-%d} ~ {h['hour'].max():%Y-%m-%d}")
    q1(h)
    st = stable_table(h)
    say(f"\n稳定小时表：{len(st):,} 个厂级小时（已剔除含启停过渡的小时）")
    q2(st)
    q3(st)
    q4(st)
    (D / "modeling-report.txt").write_text("\n".join(rep), encoding="utf-8")
    say(f"\n报告 → {D / 'modeling-report.txt'}")

if __name__ == "__main__":
    main()
