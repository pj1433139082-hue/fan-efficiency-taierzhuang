"""数据质量对抗性审查：为"1 分钟聚合 → 双口径效率"选定数据处理策略提供证据。

回答的问题（每条都要有数字，不许拍脑袋）：
  1 采样真的 15 s 吗？间隔抖动多大？有没有重复时间戳？
  2 0 是"停机真值"还是"缺测"？怎么区分？
  3 物理量程该按什么定？现有 0~250 / 0~120 / 0~300 是拍的还是从设备额定值来的？
  4 异常点长什么样：单点尖峰，还是连续漂移/卡死？
  5 聚合量选算术均值、中位数、截尾均值还是时间加权均值？差多少？
  6 过滤应该在聚合前还是聚合后？
  7 停机与启停过渡怎么判？
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
OUT = D / "dq-audit-report.txt"

MACHINES = {
    "PS37217-01-BE_GFJF_0001-C_003_0001": "一污4#",
    "PS37217-01-BE_GFJF_0001-C_003_0002": "一污5#",
    "PS37217-01-BE_GFJF_0001-C_003_0003": "一污6#",
    "PS37217-02-BE_GFJF_0001-C_003_0001": "二污1#",
    "PS37217-02-BE_GFJF_0001-C_003_0002": "二污2#",
    "PS37217-02-BE_GFJF_0001-C_003_0003": "二污3#",
}
SIGS = ["V_IF", "V_OP", "V_P", "V_ITIT", "V_I", "S_RUN"]
# 设备额定值（模板 5、空气系统 示例铭牌：额定风量 80~140 Nm3/min、升压 50~95 kPa、功率 75~170 kW）
RATED = {"V_IF": 140.0, "V_OP": 95.0, "V_P": 170.0}

rep: list[str] = []

def say(s: str = "") -> None:
    print(s, flush=True)
    rep.append(s)

def head(title: str) -> None:
    say()
    say("=" * 96)
    say(title)
    say("=" * 96)

def load_month(plant: str) -> pd.DataFrame:
    frames = []
    for f in sorted(RAW.glob(f"{plant}-*.parquet")):
        df = pd.read_parquet(f)
        keep = ["data_time_utc"] + [c for c in df.columns if c.startswith(plant + "-")]
        frames.append(df[keep])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def col(plant: str, stem: str, sig: str) -> str:
    return f"{stem}-{sig}"

# --------------------------------------------------------------------------- #
head("一、采样时间轴：间隔、重复、时区")
# --------------------------------------------------------------------------- #
one = load_month("PS37217-02")
t = pd.to_datetime(one["data_time_utc"], utc=True).sort_values()
dt = t.diff().dt.total_seconds().dropna()
say(f"样本：二污 17 个月合并，{len(t):,} 个时间戳")
say(f"  重复时间戳占比      {100 * (1 - t.nunique() / len(t)):.4f}%")
say(f"  间隔 p1/p25/p50/p75/p99 = "
    f"{dt.quantile(.01):.1f} / {dt.quantile(.25):.1f} / {dt.median():.1f} / "
    f"{dt.quantile(.75):.1f} / {dt.quantile(.99):.1f} 秒")
say(f"  间隔 >60 s 的占比   {100 * (dt > 60).mean():.3f}%   >300 s 占 {100 * (dt > 300).mean():.3f}%")
say(f"  结论：采样并非严格等间隔，1 分钟窗口必须按【时间戳】切，不能按【固定行数】切")

# --------------------------------------------------------------------------- #
head("二、0 与缺失的语义：停机真值 vs 缺测")
# --------------------------------------------------------------------------- #
for stem, label in MACHINES.items():
    plant = stem.split("-BE_")[0]
    src = one if plant == "PS37217-02" else None
    if src is None:
        continue
    s_run = src.get(col(plant, stem, "S_RUN"))
    s_p = src.get(col(plant, stem, "V_P"))
    s_q = src.get(col(plant, stem, "V_IF"))
    if s_run is None or s_p is None or s_q is None:
        continue
    run = s_run.fillna(-1) == 1
    p0 = s_p.fillna(np.nan) == 0
    q0 = s_q.fillna(np.nan) == 0
    say(f"{label}: 运行 {run.mean()*100:5.1f}% | 运行中 V_P==0 占 {100*(run & p0).sum()/max(run.sum(),1):5.3f}%"
        f" | 运行中 V_IF==0 占 {100*(run & q0).sum()/max(run.sum(),1):5.3f}%"
        f" | 停机中 V_P>1 占 {100*((~run) & (s_p.fillna(0) > 1)).sum()/max((~run).sum(),1):5.3f}%")
say()
say("判读：运行中 V_P==0 的比例就是「0 当缺失用」的上限；若这个数很小，说明 0 基本是真的 0（停机），")
say("      不能把 0 一律当缺失，否则会把停机样本当成坏点丢掉，也会把缺测样本当成停机样本算进效率。")

# --------------------------------------------------------------------------- #
head("三、各信号的真实分布 vs 设备额定值（量程该按什么定）")
# --------------------------------------------------------------------------- #
rows = []
for stem, label in MACHINES.items():
    plant = stem.split("-BE_")[0]
    df = one if plant == "PS37217-02" else load_month(plant)
    if df.empty:
        continue
    for sig in ["V_IF", "V_OP", "V_P", "V_ITIT", "V_I"]:
        c = col(plant, stem, sig)
        if c not in df.columns:
            continue
        v = pd.to_numeric(df[c], errors="coerce").dropna()
        if v.empty:
            continue
        rows.append((label, sig, len(v), v.min(), v.quantile(.001), v.median(),
                     v.quantile(.999), v.max(),
                     (v > RATED.get(sig, np.inf)).mean() * 100))
say(f"{'机组':<8}{'信号':<8}{'n':>10}{'min':>9}{'p0.1':>9}{'p50':>9}{'p99.9':>9}{'max':>10}{'>额定%':>9}")
for r in rows:
    say(f"{r[0]:<8}{r[1]:<8}{r[2]:>10,}{r[3]:>9.2f}{r[4]:>9.2f}{r[5]:>9.2f}{r[6]:>9.2f}{r[7]:>10.2f}{r[8]:>9.3f}")
say()
say("判读：额定值是设备物理上限，超额定值一倍以上的点必是坏值；但【额定值 ~ 2×额定】之间存在真实过载，")
say("      用 250/120/300 这种宽松界，会把真实过载放过；用额定值直接卡，会把真实过载误杀。")

# --------------------------------------------------------------------------- #
head("四、异常形态：整行坏值 vs 单信号尖峰")
# --------------------------------------------------------------------------- #
# 判据不拍脑袋：超过 2 倍额定值 = 物理不可能；再看这些点时其它信号是否同时异常
stem = "PS37217-02-BE_GFJF_0001-C_003_0002"
plant = "PS37217-02"
cols = pd.DataFrame({s_: pd.to_numeric(one[col(plant, stem, s_)], errors="coerce")
                     for s_ in ["V_IF", "V_OP", "V_P"]})
bad_q = cols["V_IF"] > 2 * RATED["V_IF"]
bad_p = cols["V_OP"] > 2 * RATED["V_OP"]
bad_w = cols["V_P"] > 2 * RATED["V_P"]
say(f"二污2# 超 2×额定：V_IF {bad_q.sum():,} ({100*bad_q.mean():.3f}%)  "
    f"V_OP {bad_p.sum():,} ({100*bad_p.mean():.3f}%)  V_P {bad_w.sum():,} ({100*bad_w.mean():.3f}%)")
say(f"  三者同时超限 {int((bad_q & bad_p & bad_w).sum()):,}；任一个 {int((bad_q | bad_p | bad_w).sum()):,}；"
    f"重合率 {100*(bad_q & bad_p & bad_w).sum()/max((bad_q | bad_p | bad_w).sum(),1):.2f}%")
sub = cols.loc[bad_q | bad_p | bad_w]
if len(sub):
    say(f"  坏值倍数（相对该机组中位）：V_IF {sub['V_IF'].median()/cols['V_IF'].median():.1f}×，"
        f"V_OP {sub['V_OP'].median()/max(cols['V_OP'].median(),1e-9):.1f}×，"
        f"V_P {sub['V_P'].median()/max(cols['V_P'].median(),1e-9):.1f}×")
runs, cur = [], 0
for b in (bad_q | bad_p | bad_w).to_numpy():
    if b:
        cur += 1
    elif cur:
        runs.append(cur)
        cur = 0
if cur:
    runs.append(cur)
runs = np.array(runs) if runs else np.array([0])
say(f"  坏值段：单点 {int((runs==1).sum()):,}，2~3 点 {int(((runs>=2)&(runs<=3)).sum()):,}，"
    f">=4 点 {int((runs>=4).sum()):,}，最长 {int(runs.max())}")
say()
say("判读：三个信号的重合率接近 100% 且倍数接近，说明这不是三个独立传感器同时坏，")
say("      而是【整行数据被同一次采集/换算故障污染】——逐信号设量程只是事后补救，")
say("      真正该做的是整行有效性判定：任一分量超物理极限，整行作废。")

# --------------------------------------------------------------------------- #
head("五、聚合方式敏感性：同一分钟 4 个点，不同统计量对效率的影响")
# --------------------------------------------------------------------------- #
win = one.copy()
win["t"] = pd.to_datetime(win["data_time_utc"], utc=True)
for sig in ["V_IF", "V_OP", "V_P", "V_ITIT", "S_RUN"]:
    win[sig] = pd.to_numeric(win[col(plant, stem, sig)], errors="coerce")
win["minute"] = win["t"].dt.floor("min")
g = win.groupby("minute")
agg = pd.DataFrame({
    "n": g["V_P"].count(),
    "Q_mean": g["V_IF"].mean(), "Q_med": g["V_IF"].median(),
    "P_mean": g["V_P"].mean(), "P_med": g["V_P"].median(),
    "dp_mean": g["V_OP"].mean(), "dp_med": g["V_OP"].median(),
    "T_mean": g["V_ITIT"].mean(),
    "run": g["S_RUN"].mean(),
})
agg = agg[(agg["run"] > .99) & (agg["n"] >= 3) & (agg["P_mean"] > 5)]
say(f"二污2# 全时运行且样本>=3 的分钟窗口：{len(agg):,} 个")

def eta_c(Q, dp, P, T_c):
    T1 = T_c + 273.15
    p_in = 101.325 - 38.5 / 12 * 0.133
    br = ((p_in + dp) / p_in) ** 0.2857 - 1
    w = (p_in / 0.2857) * (Q / 60.0) * br
    return np.where(P > 1, w / P, np.nan)

for name, (Q, dp, P) in {
    "全用均值": (agg["Q_mean"], agg["dp_mean"], agg["P_mean"]),
    "全用中位": (agg["Q_med"], agg["dp_med"], agg["P_med"]),
    "Q中位+dp中位+P均值": (agg["Q_med"], agg["dp_med"], agg["P_mean"]),
}.items():
    e = eta_c(Q, dp, P, agg["T_mean"])
    e = e[np.isfinite(e)]
    say(f"  {name:<22} η 中位 {np.median(e)*100:6.2f}%   p10 {np.percentile(e,10)*100:6.2f}%   p90 {np.percentile(e,90)*100:6.2f}%")
say()
say("判读：若三种口径的 η 中位差 <0.1 个百分点，说明 1 分钟尺度上数据平滑、聚合方式不敏感，")
say("      应优先选【时间加权均值】保能量口径正确，而不是中位数（中位数会系统性低估峰谷幅度）。")

# --------------------------------------------------------------------------- #
head("六、过滤顺序：先过滤后聚合 vs 先聚合后过滤")
# --------------------------------------------------------------------------- #
raw_agg = win.groupby("minute")[["V_P", "V_IF"]].mean()
viol = ((win["V_P"] > 340) | (win["V_IF"] > 280)).sum()
say(f"二污2# 超宽松界的单点 {viol:,} 个（占 {100*viol/len(win):.3f}%）")
for sig, thr in [("V_P", 340.0), ("V_IF", 280.0)]:
    pre = win.loc[win[sig] <= thr].groupby("minute")[sig].mean()
    post = raw_agg[sig]
    diff = (post - pre).abs()
    day = win["minute"].dt.date.nunique()
    say(f"  {sig}: 先过滤后聚合 vs 先聚合后过滤，均值差中位 {diff.median():.4f}、p99 {diff.quantile(.99):.4f}、最大 {diff.max():.3f}")
say()
say("判读：单点坏值一旦进了均值就抹不掉（均值不可逆），所以【过滤必须在聚合之前】，这是顺序问题不是精度问题。")

# --------------------------------------------------------------------------- #
head("七、停机与启停过渡：判定阈值怎么选")
# --------------------------------------------------------------------------- #
p = pd.to_numeric(one[col(plant, stem, "V_P")], errors="coerce")
run = pd.to_numeric(one[col(plant, stem, "S_RUN")], errors="coerce")
for thr in [0, 0.5, 1, 5, 10]:
    a = (p <= thr).sum()
    say(f"  V_P <= {thr:>4} kW：{a:>9,} 点（{100*a/len(p):5.2f}%）；其中 S_RUN=1 的 {(run.eq(1) & (p <= thr)).sum():>8,}")
say()
say("判读：S_RUN 与 V_P 阈值应当【同时】满足才算停机；只信 S_RUN 会把'已启机但尚未加载'的过渡点算进效率（分母极小 → η 虚高）。")

OUT.write_text("\n".join(rep), encoding="utf-8")
print(f"\n→ {OUT}")
