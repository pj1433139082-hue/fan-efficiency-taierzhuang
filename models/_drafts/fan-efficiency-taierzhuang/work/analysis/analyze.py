"""台儿庄 6 台曝气风机 —— 历史数据校验 + 小时聚合

输入：<仓库>/data/fan-efficiency-taierzhuang/raw/<plant>-<YYYYMM>.parquet
      （由 scripts/取数探针/iot_bulk_fetch.py 产出。首轮取数脚本 pull_all.py 已删除：
       它固定 8h 窗口，对远期数据会被 1500 条上限静默截断，且含硬编码平台凭据）
      原始表格数据不能放在模型工作区内（安全门禁 workspace-safe 禁止工作区出现
      parquet/csv 等原始数据），所以数据落在仓库级 data/ 下，本目录只放脚本与报告。
输出：
  <仓库>/data/fan-efficiency-taierzhuang/hourly.parquet   小时级聚合（建模用）
  verify-report.txt                                       校验与统计报告（本目录）

效率公式（修正版）：W = (1/K)·P_entry·Q1·[(P_exit/P_entry)^K − 1]，η = W / P_in
  K = 0.2857，P_entry = 101.325 − H/12·0.133（H = 38.5 m），P_exit = P_entry + V_OP
Excel 口径（错的，用于对照）：前导项用标准状态 P0·Q0，缺 T1/T0。

已知数据缺陷（见 verify-report）：
  - S_AUT 全期不可靠（二污含 31/7/3 等非 0-1 值），不用作判定；S_RUN 可靠（严格 0/1）
  - 存在量级尖峰（V_IF 6332、V_MTIT 1231、V_I 1022、V_ITIT 330），按物理量程过滤
"""
import os
import numpy as np
import pandas as pd
from pathlib import Path

D = Path(__file__).resolve().parent
DATA = Path(os.environ.get("FAN_DATA_DIR")

            or (D.parents[4] / "data" / "fan-efficiency-taierzhuang"))
DERIVED = DATA / "derived"  # 中间聚合（可重算，不进 git）
RAW = DATA / "raw"
K = 0.2857
P0 = 101.325
T0 = 293.15
H_ELEV = 38.5
P_ENTRY = 101.325 - H_ELEV / 12 * 0.133      # 100.8982917
C18 = 354635.537746665                       # Excel 常数 = P0'/K

SIGS = ["V_IF", "V_OP", "V_P", "V_ITIT", "V_I", "V_MTIT", "S_RUN"]
MACH_LABEL = {
    "PS37217-01-BE_GFJF_0001-C_003_0001": "一污 1期 4#",
    "PS37217-01-BE_GFJF_0001-C_003_0002": "一污 1期 5#",
    "PS37217-01-BE_GFJF_0001-C_003_0003": "一污 1期 6#",
    "PS37217-02-BE_GFJF_0001-C_003_0001": "二污 2期 1#",
    "PS37217-02-BE_GFJF_0001-C_003_0002": "二污 2期 2#",
    "PS37217-02-BE_GFJF_0001-C_003_0003": "二污 2期 3#",
}
# 物理量程（超出即判脏值丢弃）
LIMIT = {
    "V_IF": (0.0, 250.0),      # m3/min，额定 140 上限
    "V_OP": (0.0, 120.0),      # kPa 表压
    "V_P": (0.0, 300.0),       # kW
    "V_I": (0.0, 400.0),       # A
    "V_ITIT": (-20.0, 80.0),   # ℃
    "V_MTIT": (-20.0, 180.0),  # ℃
    "S_RUN": (-0.5, 1.5),
}

rep = []

def say(s=""):
    print(s, flush=True)
    rep.append(s)

def eta_calc(T1_c, Q1, dp, Pin):
    T1 = T1_c + 273.15
    P_exit = P_ENTRY + dp
    br = (P_exit / P_ENTRY) ** K - 1.0
    Wc = (P_ENTRY / K) * (Q1 / 60.0) * br
    Q0 = Q1 * (P_ENTRY / P0) * (T0 / T1)
    We = (C18 * (Q0 / 60.0) * br) / 1000.0
    with np.errstate(divide="ignore", invalid="ignore"):
        ec = np.where(Pin > 1, Wc / Pin, np.nan)
        ee = np.where(Pin > 1, We / Pin, np.nan)
    return ec, ee, Wc, We, Q0

def load():
    files = sorted(RAW.glob("*.parquet"))
    frames = []
    dropped = {k: 0 for k in LIMIT}
    n_raw = 0
    for f in files:
        df = pd.read_parquet(f)
        plant = "-".join(f.name.split("-")[:2])
        for stem, label in MACH_LABEL.items():
            if not stem.startswith(plant):
                continue
            have = {s: f"{stem}-{s}" for s in SIGS if f"{stem}-{s}" in df.columns}
            if len(have) < len(SIGS):
                continue
            sub = df[list(have.values())].copy()
            sub.columns = list(have.keys())
            sub["data_time_bj"] = df["data_time_bj"]
            n_raw += len(sub)
            for sig, (lo, hi) in LIMIT.items():
                if sig not in sub.columns:
                    continue
                v = sub[sig]
                bad = v.notna() & ((v < lo) | (v > hi))
                dropped[sig] += int(bad.sum())
                sub.loc[bad, sig] = np.nan
            sub["machine"] = label
            sub["plant"] = plant
            frames.append(sub)
    return files, frames, n_raw, dropped

def main():
    files, frames, n_raw, dropped = load()
    say(f"原始 parquet 文件 {len(files)} 个")
    say(f"原始行数 {n_raw:,}（按机计）")
    say("\n脏值过滤（超物理量程置空）：")
    for k, v in dropped.items():
        if v:
            say(f"  {k:8s} 丢弃 {v:,}  ({v / n_raw * 100:.3f}%)")
    allraw = pd.concat(frames, ignore_index=True)
    say(f"合并后 {len(allraw):,} 行 × {len(allraw.columns)} 列")

    r = allraw["S_RUN"].dropna()
    say(f"S_RUN 取值集合 = {sorted(r.unique())[:6]} → "
        f"{'0/1 严格合法 ✅' if set(r.unique()) <= {0.0, 1.0} else '⚠ 存在非 0/1'}")

    # ---------- 一、每台概况 ----------
    say("\n" + "=" * 100)
    say("一、每台风机数据概况（全时段）")
    say("=" * 100)
    say(f"{'风机':12s} {'行数':>10s} {'时段':>26s} {'运行率':>7s} {'T范围(运行)':>17s} "
        f"{'Q中位':>7s} {'dp中位':>7s} {'Pin中位':>8s}")
    for label in MACH_LABEL.values():
        g = allraw[allraw["machine"] == label]
        if not len(g):
            say(f"{label:12s} {'— 无数据 —':>10s}")
            continue
        run = g["S_RUN"].fillna(0)
        gs = g[(run == 1) & (g["V_IF"] > 1) & (g["V_P"] > 5)]
        t = g["data_time_bj"]
        tr = f"{gs['V_ITIT'].min():.1f}~{gs['V_ITIT'].max():.1f}℃" if len(gs) else "—"
        say(f"{label:12s} {len(g):10,d} {t.min():%Y-%m-%d}→{t.max():%Y-%m-%d} "
            f"{run.mean() * 100:6.1f}% {tr:>17s} {gs['V_IF'].median():7.1f} "
            f"{gs['V_OP'].median():7.1f} {gs['V_P'].median():8.1f}")

    # ---------- 二、效率 ----------
    say("\n" + "=" * 100)
    say("二、效率（修正公式 vs Excel 口径），仅运行且稳态样本")
    say("=" * 100)
    say(f"{'风机':12s} {'样本数':>8s} {'η中位':>7s} {'η_p10':>7s} {'η_p90':>7s} "
        f"{'Excel中位':>9s} {'偏差中位':>9s} {'Q p5~p95':>16s}")
    effs = []
    for label in MACH_LABEL.values():
        g = allraw[allraw["machine"] == label]
        if not len(g):
            continue
        m = ((g["S_RUN"] == 1) & (g["V_IF"] > 1) & (g["V_P"] > 5) & (g["V_OP"] > 5)
             & (g["V_ITIT"] > -10) & (g["V_ITIT"] < 80))
        g = g[m]
        if not len(g):
            say(f"{label:12s} 无稳态样本")
            continue
        ec, ee, Wc, We, Q0 = eta_calc(g["V_ITIT"].values, g["V_IF"].values,
                                      g["V_OP"].values, g["V_P"].values)
        g = g.assign(eta=ec, eta_excel=ee, W=Wc, W_excel=We, Q0=Q0)
        g = g[np.isfinite(g["eta"]) & np.isfinite(g["eta_excel"])]
        if not len(g):
            say(f"{label:12s} 无有效效率样本")
            continue
        dev = (g["eta"] / g["eta_excel"] - 1) * 100
        q5, q95 = g["V_IF"].quantile([.05, .95])
        say(f"{label:12s} {len(g):8,d} {g['eta'].median() * 100:6.2f}% "
            f"{g['eta'].quantile(.1) * 100:6.2f}% {g['eta'].quantile(.9) * 100:6.2f}% "
            f"{g['eta_excel'].median() * 100:8.2f}% {dev.median():8.2f}% "
            f"{q5:6.1f}~{q95:5.1f}")
        effs.append(g)

    eff = pd.concat(effs, ignore_index=True) if effs else None
    if eff is not None:
        dv = (eff["eta"] / eff["eta_excel"] - 1) * 100
        say(f"\n总稳态样本 {len(eff):,}")
        say(f"进气温度跨度 {eff['V_ITIT'].min():.1f} ~ {eff['V_ITIT'].max():.1f} ℃")
        say(f"Excel 偏差（正=Excel 偏低）{dv.min():+.2f}% ~ {dv.max():+.2f}%，中位 {dv.median():+.2f}%")

        say("\n进气温度分档")
        eff["tbin"] = pd.cut(eff["V_ITIT"], [-20, 10, 15, 20, 25, 30, 35, 45])
        for b, gg in eff.groupby("tbin", observed=True):
            say(f"  {str(b):14s} n={len(gg):8,d}  η中位={gg['eta'].median() * 100:5.2f}%  "
                f"Excel={gg['eta_excel'].median() * 100:5.2f}%  "
                f"偏差={(gg['eta'] / gg['eta_excel'] - 1).median() * 100:+5.2f}%")

        say("\n风量分布（判断能否建风量-效率曲线）")
        for label in MACH_LABEL.values():
            gg = eff[eff["machine"] == label]
            if not len(gg):
                continue
            q = gg["V_IF"]
            say(f"  {label:12s} min={q.min():5.1f} p1={q.quantile(.01):5.1f} "
                f"中位={q.median():5.1f} p99={q.quantile(.99):5.1f} max={q.max():5.1f} "
                f"不同值(0.1分辨)={np.round(q, 1).nunique():5d}")

    # ---------- 三、小时聚合 ----------
    say("\n" + "=" * 100)
    say("三、小时聚合 → hourly.parquet")
    say("=" * 100)
    aggs = []
    for label in MACH_LABEL.values():
        g = allraw[allraw["machine"] == label]
        if not len(g):
            continue
        g = g.assign(hour=g["data_time_bj"].dt.floor("h"))
        m = ((g["S_RUN"] == 1) & (g["V_IF"] > 1) & (g["V_P"] > 5) & (g["V_OP"] > 5)
             & (g["V_ITIT"] > -10) & (g["V_ITIT"] < 80))
        gs = g[m].copy()
        if not len(gs):
            continue
        ec, ee, Wc, We, Q0 = eta_calc(gs["V_ITIT"].values, gs["V_IF"].values,
                                      gs["V_OP"].values, gs["V_P"].values)
        gs = gs.assign(eta=ec, eta_excel=ee, W=Wc, W_excel=We, Q0=Q0)
        gs = gs[np.isfinite(gs["eta"]) & np.isfinite(gs["eta_excel"])]
        if not len(gs):
            continue
        runs = g.groupby("hour")["S_RUN"].mean().rename("run_share")
        n_samp = g.groupby("hour")["S_RUN"].size().rename("n_samp")
        a = gs.groupby("hour").agg(
            n=("V_IF", "size"),
            Q_mean=("V_IF", "mean"), Q_p10=("V_IF", lambda s: s.quantile(.1)),
            Q_p90=("V_IF", lambda s: s.quantile(.9)),
            T_mean=("V_ITIT", "mean"), T_min=("V_ITIT", "min"), T_max=("V_ITIT", "max"),
            dp_mean=("V_OP", "mean"), Pin_mean=("V_P", "mean"),
            I_mean=("V_I", "mean"), MTIT_mean=("V_MTIT", "mean"),
            eta_mean=("eta", "mean"), eta_p10=("eta", lambda s: s.quantile(.1)),
            eta_p90=("eta", lambda s: s.quantile(.9)),
            eta_excel_mean=("eta_excel", "mean"), W_mean=("W", "mean"),
        )
        a = a.join(runs).join(n_samp).reset_index()
        a["machine"] = label
        aggs.append(a)
    if aggs:
        hourly = pd.concat(aggs, ignore_index=True)
        DERIVED.mkdir(parents=True, exist_ok=True)
        hourly.to_parquet(DERIVED / "hourly.parquet", compression="zstd", index=False)
        say(f"  {len(hourly):,} 行 × {len(hourly.columns)} 列 → {DERIVED / 'hourly.parquet'}")
        say(f"  覆盖 {hourly['hour'].min()} ~ {hourly['hour'].max()}")
        for label in MACH_LABEL.values():
            h = hourly[hourly["machine"] == label]
            if not len(h):
                continue
            say(f"  {label:12s} {len(h):6,d} 小时，有运行 {(h['run_share'] > 0).sum():6,d}，"
                f"全时运行 {(h['run_share'] >= .99).sum():6,d}")

    (D / "verify-report.txt").write_text("\n".join(rep), encoding="utf-8")
    say(f"\n报告 → {D / 'verify-report.txt'}")

if __name__ == "__main__":
    main()
