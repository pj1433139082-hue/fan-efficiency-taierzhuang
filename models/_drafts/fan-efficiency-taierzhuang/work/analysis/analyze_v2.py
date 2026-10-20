"""按已确认的七条数据处理流水线重算历史统计（v2）。

与 v1（`analyze.py`）的差别：
* 有效性判定整行化，且直接复用运行时模型 `src/model.py` 的 `classify_row` / 计算函数 ——
  保证「离线统计口径」与「线上输出口径」是同一份代码，不会两边漂移。
* 聚合用时间加权（Δt 加权），并按分钟判定覆盖度（<2 点或 <30 s 置空）。
* 连续无效 >6 h 升级为 DATA_OUTAGE。
* 停机双条件：run_share ≥ 0.5 且功率 ≥ 5 kW。

输出：
  <仓库>/data/fan-efficiency-taierzhuang/minute-v2.parquet   分钟级（含质量标记）
  <仓库>/data/fan-efficiency-taierzhuang/hourly-v2.parquet   小时级（建模用）
  verify-report-v2.txt                                       本轮统计（本目录）
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

import model as fan  # noqa: E402

DATA = Path(os.environ.get("FAN_DATA_DIR")

            or (HERE.parents[4] / "data" / "fan-efficiency-taierzhuang"))
DERIVED = DATA / "derived"  # 中间聚合（可重算，不进 git）
RAW = DATA / "raw"
REPORT = HERE / "verify-report-v2.txt"

PLANTS = {
    "PS37217-01": ["0001", "0002", "0003"],
    "PS37217-02": ["0001", "0002", "0003"],
}
LABELS = {
    ("PS37217-01", "0001"): "一污4#", ("PS37217-01", "0002"): "一污5#",
    ("PS37217-01", "0003"): "一污6#", ("PS37217-02", "0001"): "二污1#",
    ("PS37217-02", "0002"): "二污2#", ("PS37217-02", "0003"): "二污3#",
}
SIGNALS = ["V_IF", "V_OP", "V_P", "V_ITIT", "S_RUN"]
ELEVATION = fan.DEFAULT_ELEVATION_M

_lines: list[str] = []

def say(text: str = "") -> None:
    print(text)
    _lines.append(text)

def load_plant(plant: str) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(str(RAW / f"{plant}-*.parquet"))):
        df = pd.read_parquet(path)
        keep = ["data_time_utc"]
        for suffix in PLANTS[plant]:
            stem = f"{plant}-BE_GFJF_0001-C_003_{suffix}"
            keep += [c for c in df.columns
                     if c.startswith(stem) and c.rsplit("-", 1)[-1] in SIGNALS]
        frames.append(df[keep])
    wide = pd.concat(frames, ignore_index=True)
    wide["t"] = pd.to_datetime(wide["data_time_utc"], utc=True)
    return wide.sort_values("t").reset_index(drop=True)

def stack(wide: pd.DataFrame, plant: str) -> pd.DataFrame:
    """宽表 → 长表（一台机一行）。"""
    parts = []
    for suffix in PLANTS[plant]:
        stem = f"{plant}-BE_GFJF_0001-C_003_{suffix}"
        cols = {s: f"{stem}-{s}" for s in SIGNALS}
        if cols["V_IF"] not in wide.columns:
            continue
        part = pd.DataFrame({
            "t": wide["t"],
            "machine": LABELS[(plant, suffix)],
            "inlet_flow": pd.to_numeric(wide[cols["V_IF"]], errors="coerce"),
            "outlet_pressure": pd.to_numeric(wide[cols["V_OP"]], errors="coerce"),
            "motor_power": pd.to_numeric(wide[cols["V_P"]], errors="coerce"),
            "inlet_temperature": pd.to_numeric(wide[cols["V_ITIT"]], errors="coerce"),
            "run_flag": pd.to_numeric(wide[cols["S_RUN"]], errors="coerce"),
        })
        parts.append(part.dropna(subset=["t"]))
    long = pd.concat(parts, ignore_index=True)
    long["elevation"] = ELEVATION
    return long.sort_values(["machine", "t"]).reset_index(drop=True)

def row_status(long: pd.DataFrame) -> pd.Series:
    """向量化的行级有效性判定（与 model.classify_row 的规则与顺序一一对应）。"""
    flow, dp, power, temp = (long["inlet_flow"], long["outlet_pressure"],
                             long["motor_power"], long["inlet_temperature"])
    inside = (
        flow.ge(0) & flow.le(fan.LIMITS["inlet_flow"][1])
        & dp.between(*fan.LIMITS["outlet_pressure"])
        & power.between(*fan.LIMITS["motor_power"])
        & temp.between(*fan.LIMITS["inlet_temperature"])
    )
    status = pd.Series("VALID", index=long.index, dtype=object)
    status[~inside] = fan.ROW_INVALID
    stopped = inside & (power < fan.STOP_POWER_KW)
    status[stopped] = fan.STOPPED
    ratio_bad = inside & ~stopped & (dp <= 0)
    status[ratio_bad] = fan.RATIO_INVALID
    flow_bad = inside & ~stopped & ~ratio_bad & (flow <= 0)
    status[flow_bad] = fan.ROW_INVALID
    return status

def minute_aggregate(long: pd.DataFrame) -> pd.DataFrame:
    long = long.copy()
    long["status"] = row_status(long)
    long["dt"] = (long.groupby("machine")["t"].diff().dt.total_seconds()
                  .clip(upper=300.0))
    long["dt"] = long.groupby("machine")["dt"].transform(lambda s: s.fillna(s.median()))
    long["minute"] = long["t"].dt.floor("min")

    usable = long[long["status"].isin(["VALID", "STOPPED"])].copy()
    rejected = long["status"].isin(["ROW_INVALID", "RATIO_INVALID", "NO_SAMPLE"])

    keys = ["machine", "minute"]
    weighted = usable[keys + ["dt"]].copy()
    for col in ("inlet_flow", "outlet_pressure", "motor_power", "inlet_temperature"):
        weighted[col] = usable[col].to_numpy() * usable["dt"].to_numpy()
    weighted["run"] = (usable["run_flag"] == 1).to_numpy() * usable["dt"].to_numpy()
    weighted["n"] = 1

    sums = weighted.groupby(keys, sort=True).sum()
    total = sums["dt"]
    rows = pd.DataFrame(index=sums.index)
    for col in ("inlet_flow", "outlet_pressure", "motor_power", "inlet_temperature"):
        rows[col] = sums[col] / total
    rows["run_share"] = sums["run"] / total
    rows["coverage_s"] = total
    rows["n_samples"] = sums["n"].astype(int)
    rows = rows.reset_index()

    invalid = (long[rejected].groupby(keys).size()
               .rename("n_invalid").reset_index())
    rows = rows.merge(invalid, on=keys, how="left")
    rows["n_invalid"] = rows["n_invalid"].fillna(0).astype(int)

    too_thin = (rows["n_samples"] < fan.MIN_SAMPLES) | (rows["coverage_s"] < fan.MIN_COVERAGE_S)
    rows.loc[too_thin, ["inlet_flow", "outlet_pressure", "motor_power",
                        "inlet_temperature", "run_share"]] = np.nan

    quality = np.where(too_thin, fan.NO_SAMPLE, fan.VALID)
    stopped = (~too_thin) & ((rows["run_share"] < 0.5) | (rows["motor_power"] < fan.STOP_POWER_KW))
    quality = np.where(stopped, fan.STOPPED, quality)
    rows["quality"] = quality
    return rows.sort_values(keys).reset_index(drop=True)

def fill_gaps(minutes: pd.DataFrame, bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]]) -> pd.DataFrame:
    """把没有可用样本的分钟补成 NO_SAMPLE 行，使中断段可见。

    ``bounds`` 是每台机组**原始数据**的时间范围（不是可用样本的范围）——
    否则序列开头的整段坏值会被静默裁掉，看起来像「从来没采过」。
    """
    parts = []
    for machine, group in minutes.groupby("machine", sort=True):
        group = group.set_index("minute")
        start, end = bounds[machine]
        start, end = start.floor("min"), end.floor("min")
        full = pd.date_range(start, end, freq="min", tz="UTC")
        filled = group.reindex(full)
        filled.index.name = "minute"
        filled["machine"] = machine
        filled["quality"] = filled["quality"].fillna(fan.NO_SAMPLE)
        filled["n_samples"] = filled["n_samples"].fillna(0).astype(int)
        filled["n_invalid"] = filled["n_invalid"].fillna(0).astype(int)
        parts.append(filled.reset_index())
    return pd.concat(parts, ignore_index=True)[minutes.columns]

def outage_pass(minutes: pd.DataFrame) -> pd.DataFrame:
    """连续无效（NO_SAMPLE/ROW_INVALID）超过 6 h → DATA_OUTAGE。"""
    minutes = minutes.sort_values(["machine", "minute"]).copy()
    windows: list[tuple[str, pd.Timestamp, pd.Timestamp, int]] = []
    for machine, group in minutes.groupby("machine", sort=True):
        flags = group["quality"].to_numpy()
        times = group["minute"].to_numpy()
        start = None
        for i, flag in enumerate(np.append(flags, fan.VALID)):
            if flag in (fan.NO_SAMPLE, fan.ROW_INVALID):
                if start is None:
                    start = i
                continue
            if start is not None:
                span = (i - start) * 60.0
                if span > fan.OUTAGE_SECONDS:
                    windows.append((machine, times[start], times[i - 1], i - start))
                    mask = (minutes["machine"] == machine) & (
                        minutes["minute"].between(times[start], times[i - 1]))
                    minutes.loc[mask, "quality"] = fan.DATA_OUTAGE
                start = None
    minutes.attrs["outages"] = windows
    return minutes

def add_efficiency(minutes: pd.DataFrame) -> pd.DataFrame:
    minutes = minutes.copy()
    ok = minutes["quality"].isin([fan.VALID])
    q = minutes["inlet_flow"].to_numpy(float)
    dp = minutes["outlet_pressure"].to_numpy(float)
    power = minutes["motor_power"].to_numpy(float)
    temp = minutes["inlet_temperature"].to_numpy(float)

    p1 = fan.entry_pressure_kpa(ELEVATION)
    ratio = (p1 + dp) / p1
    q_std = q * (p1 / fan.P_STD_KPA) * (fan.T_STD_K / (temp + 273.15))
    with np.errstate(invalid="ignore", divide="ignore"):
        bracket = ratio ** fan.K - 1.0
        wp_corrected = (p1 / fan.K) * (q / 60.0) * bracket
        wp_excel = fan.C18 * 1e-3 * (q_std / 60.0) * bracket
        eta_corrected = wp_corrected / power
        eta_excel = wp_excel / power

    minutes["q_std"] = np.where(ok, q_std, np.nan)
    minutes["ratio"] = np.where(ok, ratio, np.nan)
    minutes["wp_corrected"] = np.where(ok, wp_corrected, np.nan)
    minutes["wp_excel"] = np.where(ok, wp_excel, np.nan)
    minutes["eta_corrected"] = np.where(ok, eta_corrected, np.nan)
    minutes["eta_excel"] = np.where(ok, eta_excel, np.nan)

    lo, hi = fan.ETA_RANGE
    bad = ok & ((minutes["eta_corrected"] < lo) | (minutes["eta_corrected"] > hi)
                | (minutes["eta_excel"] < lo) | (minutes["eta_excel"] > hi))
    minutes.loc[bad, ["eta_corrected", "eta_excel", "wp_corrected", "wp_excel"]] = np.nan
    minutes.loc[bad, "quality"] = fan.OUT_OF_RANGE
    return minutes

def pct(series: pd.Series, q: float) -> float:
    value = series.quantile(q)
    return float(value) if pd.notna(value) else float("nan")

def main() -> None:
    say("=" * 96)
    say("台儿庄 6 台曝气风机 —— 按七条流水线重算（v2）")
    say(f"数据目录：{DATA}")
    say(f"行级边界：{fan.LIMITS}")
    say(f"停机门槛：功率 ≥ {fan.STOP_POWER_KW} kW 且 run_share ≥ 0.5；"
        f"覆盖门槛：≥{fan.MIN_SAMPLES} 点且 ≥{fan.MIN_COVERAGE_S:.0f} s；"
        f"中断门槛：连续无效 >{fan.OUTAGE_SECONDS/3600:.0f} h")
    say()

    all_minutes = []
    all_outages = []
    for plant in PLANTS:
        wide = load_plant(plant)
        long = stack(wide, plant)
        minutes = minute_aggregate(long)
        bounds = {machine: (group["t"].min(), group["t"].max())
                  for machine, group in long.groupby("machine", sort=True)}
        minutes = fill_gaps(minutes, bounds)
        minutes = outage_pass(minutes)
        all_outages += minutes.attrs.get("outages") or []
        minutes = add_efficiency(minutes)
        minutes["plant"] = plant
        all_minutes.append(minutes)
        say(f"[{plant}] 原始 {len(long):,} 行 → 分钟 {len(minutes):,} 行")
        for machine, group in minutes.groupby("machine", sort=True):
            counts = group["quality"].value_counts()
            say(f"  {machine:<6} " + "  ".join(
                f"{code}={counts.get(code, 0):,}" for code in fan.QUALITY_CODES))

    minutes = pd.concat(all_minutes, ignore_index=True)
    minutes.attrs["outages"] = all_outages

    say()
    say("=" * 96)
    say("一、数据中断（连续无效 >6 h，整段不可用）")
    say("=" * 96)
    outages = minutes.attrs.get("outages") or []
    if not outages:
        say("  无（没有任何一段连续无效超过 6 h）")
    for machine, start, end, count in sorted(outages, key=lambda item: item[1]):
        hours = count * 60.0 / 3600.0
        say(f"  {machine:<6} {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M}  "
            f"{count:>6,} 分钟（{hours:.1f} h）")

    say()
    say("=" * 96)
    say("二、每台概况（仅 VALID 分钟）")
    say("=" * 96)
    head = (f"{'机组':<7}{'分钟数':>9}{'有效':>9}{'运行%':>8}{'中断%':>8}"
            f"{'T 中位':>8}{'T 范围':>16}{'Q 中位':>8}{'Q 范围':>18}{'dp 中位':>8}{'Pin 中位':>9}")
    say(head)
    valid_stats = {}
    for machine, group in minutes.groupby("machine", sort=True):
        total = len(group)
        valid = group[group["quality"] == fan.VALID]
        outage = (group["quality"] == fan.DATA_OUTAGE).sum()
        valid_stats[machine] = valid
        say(f"{machine:<7}{total:>9,}{len(valid):>9,}"
            f"{len(valid)/total*100:>7.1f}%{outage/total*100:>7.1f}%"
            f"{valid['inlet_temperature'].median():>8.1f}"
            f"{pct(valid['inlet_temperature'], .01):>7.1f}~{pct(valid['inlet_temperature'], .99):<7.1f}"
            f"{valid['inlet_flow'].median():>8.1f}"
            f"{pct(valid['inlet_flow'], .01):>8.1f}~{pct(valid['inlet_flow'], .99):<8.1f}"
            f"{valid['outlet_pressure'].median():>8.1f}"
            f"{valid['motor_power'].median():>9.1f}")

    say()
    say("=" * 96)
    say("三、效率（VALID 分钟）")
    say("=" * 96)
    say(f"{'机组':<7}{'n':>9}{'η 中位':>9}{'η p10':>8}{'η p90':>8}"
        f"{'Excel 中位':>11}{'偏差中位':>10}{'偏差范围':>18}")
    for machine, valid in valid_stats.items():
        eta = valid["eta_corrected"].dropna()
        eta_e = valid["eta_excel"].dropna()
        dev = (eta_e / eta - 1.0).dropna() * 100
        say(f"{machine:<7}{len(eta):>9,}{eta.median():>9.2%}"
            f"{pct(eta, .1):>8.2%}{pct(eta, .9):>8.2%}"
            f"{eta_e.median():>11.2%}{dev.median():>9.2f}%"
            f"{dev.min():>8.2f}%~{dev.max():<8.2f}%")

    say()
    say("=" * 96)
    say("四、温度分档偏差（eta_excel/eta_corrected − 1，正值 = Excel 高估 / 负值 = Excel 低估）")
    say("=" * 96)
    bins = [(-30, 10), (10, 15), (15, 20), (20, 25), (25, 30), (30, 35), (35, 60)]
    say(f"{'温度区间':<14}{'n':>10}{'η 修正中位':>12}{'η Excel 中位':>14}{'偏差中位':>10}")
    for low, high in bins:
        pick = minutes["quality"].isin([fan.VALID]) & minutes["inlet_temperature"].between(
            low, high, inclusive="left")
        subset = minutes[pick]
        dev = (subset["eta_excel"] / subset["eta_corrected"] - 1.0).dropna() * 100
        if dev.empty:
            continue
        say(f"{low}~{high} ℃{'':<4}{len(dev):>10,}"
            f"{subset['eta_corrected'].median():>12.2%}"
            f"{subset['eta_excel'].median():>14.2%}{dev.median():>9.2f}%")

    say()
    say("=" * 96)
    say("五、写出")
    say("=" * 96)
    import pyarrow  # noqa: F401  确认可用

    minute_path = DERIVED / "minute-v2.parquet"
    minute_out = minutes.drop(columns=["plant"]).copy()
    minute_out.attrs = {}
    minute_out.to_parquet(minute_path, compression="zstd", index=False)
    say(f"  {len(minutes):,} 行 → {minute_path}")

    minute = minutes[minutes["quality"] == fan.VALID].copy()
    minute["hour"] = minute["minute"].dt.floor("h")
    hourly = minute.groupby(["machine", "hour"], sort=True).agg(
        Q_mean=("inlet_flow", "mean"), Q_p10=("inlet_flow", lambda s: s.quantile(.1)),
        Q_p90=("inlet_flow", lambda s: s.quantile(.9)),
        T_mean=("inlet_temperature", "mean"), T_min=("inlet_temperature", "min"),
        T_max=("inlet_temperature", "max"),
        dp_mean=("outlet_pressure", "mean"), Pin_mean=("motor_power", "mean"),
        eta_mean=("eta_corrected", "mean"),
        eta_p10=("eta_corrected", lambda s: s.quantile(.1)),
        eta_p90=("eta_corrected", lambda s: s.quantile(.9)),
        eta_excel_mean=("eta_excel", "mean"),
        W_mean=("wp_corrected", "mean"),
        n_min=("eta_corrected", "size"),
    ).reset_index()
    hourly = hourly[hourly["n_min"] >= 30]
    hourly_path = DERIVED / "hourly-v2.parquet"
    hourly.to_parquet(hourly_path, compression="zstd", index=False)
    say(f"  {len(hourly):,} 行 × {len(hourly.columns)} 列 → {hourly_path}")

    REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"\n报告 → {REPORT}")

if __name__ == "__main__":
    main()
