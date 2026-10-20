"""台儿庄二期（二污 1#/2#/3#）逐台效率曲线导出。

给下游组合模型（blower-optimization 仓库）用：``derive_lower`` 需要逐台曲线，
不能把「二污汇总曲线」克隆给单台，也不能把汇总区间当单台的允许运行范围。

口径
----
两个口径都出：
- ``excel``     = 平台本期生效口径（前导项用标准状态 P0·Q0）
- ``corrected`` = 严格多变压缩口径（前导项用入口状态 P1·Q1）

每个有效分钟就是一个样本；按 2 m3/min 分箱、箱内样本数 >= 15 才出一行，
取箱内效率中位数。不外推、不补点、不跨机合并。

用法
----
    .\\.venv-311\\Scripts\\python.exe export_p2_curves.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

DATA = Path(os.environ.get("FAN_DATA_DIR") or
            r"E:\beikong-code\learn-model\data\fan-efficiency-taierzhuang")
DERIVED = DATA / "derived"
OUT = DATA / "delivery" / "curves-p2"

# 二期（二污）三台：台账点位编号 → 机号
MACHINES = {
    "PS37217-02-BE_GFJF_0001-C_003_0001": ("二污1#", "1#"),
    "PS37217-02-BE_GFJF_0001-C_003_0002": ("二污2#", "2#"),
    "PS37217-02-BE_GFJF_0001-C_003_0003": ("二污3#", "3#"),
}
BIN_WIDTH = 2.0
MIN_SAMPLES = 15


def curve_for(frame: pd.DataFrame, eta_col: str) -> pd.DataFrame:
    """单个机台的 (q, eta) 曲线；q 是有效运行分钟的进口风量。"""
    sub = frame[frame["quality"] == "VALID"].copy()
    sub = sub[sub["run_share"] >= 1.0]
    sub = sub[(sub["inlet_flow"] > 0) & sub[eta_col].notna()]
    if sub.empty:
        return pd.DataFrame(columns=["q_m3min", "eta_pct", "n_samples"])
    sub["bin"] = (sub["inlet_flow"] / BIN_WIDTH).round() * BIN_WIDTH
    rows = []
    for q, group in sub.groupby("bin"):
        if len(group) < MIN_SAMPLES:
            continue
        rows.append({"q_m3min": float(q),
                     "eta_pct": float(group[eta_col].median() * 100.0),
                     "n_samples": int(len(group))})
    return pd.DataFrame(rows).sort_values("q_m3min").reset_index(drop=True)


def describe(table: pd.DataFrame) -> dict:
    """曲线的可用区间与峰值，用于推导 D（效率下限）候选。"""
    if table.empty:
        return {}
    peak = table.loc[table["eta_pct"].idxmax()]
    good = table[table["eta_pct"] >= table["eta_pct"].max() - 2.0]
    return {
        "q_min": float(table["q_m3min"].min()),
        "q_max": float(table["q_m3min"].max()),
        "eta_peak": float(peak["eta_pct"]),
        "eta_peak_q": float(peak["q_m3min"]),
        "eta_min": float(table["eta_pct"].min()),
        "eta_median": float(table["eta_pct"].median()),
        "q_range_within_2pt": (float(good["q_m3min"].min()), float(good["q_m3min"].max())),
        "n_bins": int(len(table)),
        "n_valid_minutes": int(table["n_samples"].sum()),
    }


def main() -> int:
    minute = pd.read_parquet(DERIVED / "minute-v2.parquet")
    OUT.mkdir(parents=True, exist_ok=True)
    lines = ["# 台儿庄二期（二污 1#/2#/3#）逐台效率曲线", "",
             f"来源：`minute-v2.parquet`，VALID 且 run_share=1.0 的运行分钟；"
             f"分箱 {BIN_WIDTH:g} m3/min，箱内样本 >= {MIN_SAMPLES}，取效率中位数。",
             "不外推、不补点、不跨机合并；q 为进口风量（m3/min），eta 为百分比。", ""]
    for point, (machine, short) in MACHINES.items():
        frame = minute[minute["machine"] == machine]
        for caliber, col in (("excel", "eta_excel"), ("corrected", "eta_corrected")):
            table = curve_for(frame, col)
            csv = OUT / f"curve-taierzhuang-p2-{short}-{caliber}.csv"
            table.to_csv(csv, index=False, encoding="utf-8")
            info = describe(table)
            label = f"{machine}（{point}）"
            if info:
                lines += [f"## {label} · {caliber}", "",
                          f"- 箱数 {info['n_bins']}，有效运行分钟 {info['n_valid_minutes']:,}",
                          f"- 风量覆盖 {info['q_min']:g} ~ {info['q_max']:g} m3/min",
                          f"- 效率 峰值 {info['eta_peak']:.2f}%（在 {info['eta_peak_q']:g}）、"
                          f"中位 {info['eta_median']:.2f}%、最低 {info['eta_min']:.2f}%",
                          f"- 距峰值 2 个百分点以内 {info['q_range_within_2pt'][0]:g} ~ "
                          f"{info['q_range_within_2pt'][1]:g} m3/min", ""]
            else:
                lines += [f"## {label} · {caliber}", "", "- 有效运行样本不足，未生成曲线", ""]
            print(f"{machine:6s} {caliber:10s} bins={info.get('n_bins', 0):3d} "
                  f"q={info.get('q_min', float('nan')):.1f}~{info.get('q_max', float('nan')):.1f} "
                  f"peak={info.get('eta_peak', float('nan')):.2f}%")
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
