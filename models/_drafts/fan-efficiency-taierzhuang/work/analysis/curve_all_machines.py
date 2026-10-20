"""台儿庄六台风机「风量-效率」实测曲线与最佳效率评估。

回答两个问题：
1. 每台风机的效率曲线（风量 → 效率）能否画出来？
2. 模型算出来的效率是不是「最佳效率」？

口径
----
- ``excel``     平台本期生效口径（前导项用标准状态 P0·Q0）
- ``corrected`` 严格多变压缩口径（前导项用入口状态 P1·Q1）

样本 = 有效分钟（``quality == VALID``），按 2 m3/min 分箱，箱内 >= 15 个样本才出一行，
取箱内中位数；同时给 p10/p90 作为离散带。不外推、不补点、不跨机合并。

输出
----
- ``<derived>/curves-6machines.csv``        逐台逐箱曲线（含背压中位、样本数）
- ``<derived>/curves-6machines-summary.csv`` 逐台汇总（峰值、运行点、差距）
- ``<工作区>/plots/efficiency-curves-6machines.png`` 六联图

用法
----
    .\\.venv-311\\Scripts\\python.exe curve_all_machines.py
"""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

DATA = Path(os.environ.get("FAN_DATA_DIR") or
            r"E:\beikong-code\learn-model\data\fan-efficiency-taierzhuang")
DERIVED = DATA / "derived"
WORKSPACE = Path(__file__).resolve().parents[2]
PLOTS = WORKSPACE / "plots"

BIN_WIDTH = 2.0
MIN_SAMPLES = 15
ORDER = ["一污4#", "一污5#", "一污6#", "二污1#", "二污2#", "二污3#"]

CJK_FONTS = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC", "DejaVu Sans"]


def _binned(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["q_bin"] = (work["inlet_flow"] // BIN_WIDTH) * BIN_WIDTH + BIN_WIDTH / 2
    grouped = work.groupby("q_bin", observed=True)
    table = pd.DataFrame({
        "n_samples": grouped.size(),
        "q_median": grouped["inlet_flow"].median(),
        "eta_excel": grouped["eta_excel"].median(),
        "eta_excel_p10": grouped["eta_excel"].quantile(0.10),
        "eta_excel_p90": grouped["eta_excel"].quantile(0.90),
        "eta_corrected": grouped["eta_corrected"].median(),
        "dp_median": grouped["outlet_pressure"].median(),
        "t_median": grouped["inlet_temperature"].median(),
        "power_median": grouped["motor_power"].median(),
    }).reset_index()
    return table[table["n_samples"] >= MIN_SAMPLES].sort_values("q_bin").reset_index(drop=True)


def main() -> int:
    path = DERIVED / "minute-v2.parquet"
    frame = pd.read_parquet(path, columns=[
        "machine", "inlet_flow", "outlet_pressure", "motor_power", "inlet_temperature",
        "quality", "eta_excel", "eta_corrected",
    ])
    valid = frame[frame["quality"] == "VALID"].copy()
    print(f"总分钟行 {len(frame):,}，有效 {len(valid):,}")

    curves: list[pd.DataFrame] = []
    summary: list[dict] = []
    for machine in ORDER:
        part = valid[valid["machine"] == machine]
        if part.empty:
            print(f"{machine}: 无有效样本，跳过")
            continue
        table = _binned(part)
        table.insert(0, "machine", machine)
        curves.append(table)

        peak_idx = table["eta_excel"].idxmax()
        peak = table.loc[peak_idx]
        # 峰值落在样本覆盖的左右边缘（前/后 15% 的箱）时不可信：曲线在边缘仍在上升或下降，
        # 真实峰值可能在没跑过的工况里。
        span = max(len(table) - 1, 1)
        peak_pos = float(table.index.get_loc(peak_idx)) / span
        peak_is_edge = bool(peak_pos <= 0.15 or peak_pos >= 0.85)
        q_operating = part["inlet_flow"].median()
        op_idx = (table["q_median"] - q_operating).abs().idxmin()
        operating = table.loc[op_idx]
        peak_corr = table.loc[table["eta_corrected"].idxmax()]

        summary.append({
            "machine": machine,
            "valid_minutes": len(part),
            "q_min": round(part["inlet_flow"].quantile(0.01), 2),
            "q_p10": round(part["inlet_flow"].quantile(0.10), 2),
            "q_median": round(q_operating, 2),
            "q_p90": round(part["inlet_flow"].quantile(0.90), 2),
            "q_max": round(part["inlet_flow"].quantile(0.99), 2),
            "q_span_p10_p90": round(part["inlet_flow"].quantile(0.90) - part["inlet_flow"].quantile(0.10), 2),
            "bins": len(table),
            "eta_peak_excel": round(peak["eta_excel"] * 100, 2),
            "q_at_peak": round(peak["q_median"], 2),
            "eta_at_qmedian": round(operating["eta_excel"] * 100, 2),
            "gap_to_peak": round((peak["eta_excel"] - operating["eta_excel"]) * 100, 2),
            "eta_peak_corrected": round(peak_corr["eta_corrected"] * 100, 2),
            "q_at_peak_corrected": round(peak_corr["q_median"], 2),
            "peak_is_edge": peak_is_edge,
            "dp_median": round(part["outlet_pressure"].median(), 1),
            "dp_p10": round(part["outlet_pressure"].quantile(0.10), 1),
            "dp_p90": round(part["outlet_pressure"].quantile(0.90), 1),
            "t_median": round(part["inlet_temperature"].median(), 1),
            "t_p10": round(part["inlet_temperature"].quantile(0.10), 1),
            "t_p90": round(part["inlet_temperature"].quantile(0.90), 1),
            "corr_q_t": round(part["inlet_flow"].corr(part["inlet_temperature"]), 3),
        })
        print(f"{machine}: 有效 {len(part):,} 分钟 / {len(table)} 箱，"
              f"峰值 {peak['eta_excel'] * 100:.2f}% @ {peak['q_median']:.1f} m3/min，"
              f"常跑 {q_operating:.1f} m3/min 处 {operating['eta_excel'] * 100:.2f}%")

    all_curves = pd.concat(curves, ignore_index=True)
    curves_path = DERIVED / "curves-6machines.csv"
    all_curves.to_csv(curves_path, index=False, encoding="utf-8-sig")

    summary_frame = pd.DataFrame(summary)
    summary_path = DERIVED / "curves-6machines-summary.csv"
    summary_frame.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print("\n汇总:")
    print(summary_frame.to_string(index=False))
    print(f"\n写出 {curves_path}\n写出 {summary_path}")

    _plot(all_curves, summary_frame)

    dp_curves = _dp_groups(valid)
    if not dp_curves.empty:
        dp_path = DERIVED / "curves-6machines-dp.csv"
        dp_curves.to_csv(dp_path, index=False, encoding="utf-8-sig")
        print(f"写出 {dp_path}")
        _plot_dp(dp_curves)
    return 0


def _dp_groups(valid: pd.DataFrame) -> pd.DataFrame:
    """按各自背压三分位分组，看同一台风机的效率曲线是否随背压平移。"""
    rows: list[pd.DataFrame] = []
    for machine in ORDER:
        part = valid[valid["machine"] == machine]
        if len(part) < 20_000:
            continue
        lo, hi = part["outlet_pressure"].quantile([1 / 3, 2 / 3])
        groups = {
            f"低压 ≤{lo:.0f} kPa": part[part["outlet_pressure"] <= lo],
            f"中压 {lo:.0f}–{hi:.0f} kPa": part[(part["outlet_pressure"] > lo) & (part["outlet_pressure"] <= hi)],
            f"高压 >{hi:.0f} kPa": part[part["outlet_pressure"] > hi],
        }
        for label, sub in groups.items():
            if len(sub) < 5_000:
                continue
            table = _binned(sub)
            if table.empty:
                continue
            table.insert(0, "dp_group", label)
            table.insert(0, "machine", machine)
            rows.append(table)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _plot(curves: pd.DataFrame, summary: pd.DataFrame) -> None:
    plt.rcParams["font.sans-serif"] = CJK_FONTS
    plt.rcParams["axes.unicode_minus"] = False
    PLOTS.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 3, figsize=(16.5, 10.0))
    for ax, machine in zip(axes.ravel(), ORDER):
        table = curves[curves["machine"] == machine]
        stat = summary[summary["machine"] == machine].iloc[0]
        x = table["q_median"].to_numpy()
        ax.fill_between(x, table["eta_excel_p10"] * 100, table["eta_excel_p90"] * 100,
                        color="#8ba3b8", alpha=0.28, linewidth=0, label="p10–p90 离散带")
        ax.plot(x, table["eta_excel"] * 100, color="#1f4e79", linewidth=2.2, marker="o",
                markersize=3.2, label="Excel 口径（平台生效）")
        ax.plot(x, table["eta_corrected"] * 100, color="#b45309", linewidth=1.6,
                linestyle="--", label="修正口径")

        peak_x, peak_y = stat["q_at_peak"], stat["eta_peak_excel"]
        ax.scatter([peak_x], [peak_y], marker="*", s=280, color="#c0392b", zorder=5,
                   label="实测峰值")
        # 峰值标注朝着图内侧放，避免压到角标
        right_side = peak_x > (x.min() + x.max()) / 2
        ax.annotate(f"峰值 {peak_y:.1f}%\n@ {peak_x:.0f} m³/min",
                    xy=(peak_x, peak_y), xytext=(-10 if right_side else 10, 14),
                    textcoords="offset points", fontsize=9, color="#c0392b",
                    ha="right" if right_side else "left")

        ax.axvline(stat["q_median"], color="#2e7d32", linestyle=":", linewidth=1.4)
        low, high = min(table["eta_excel_p10"].min() * 100, table["eta_corrected"].min() * 100), \
            max(table["eta_excel_p90"].max() * 100, table["eta_corrected"].max() * 100)
        ax.set_ylim(low - 3, high + 7)
        op_right = stat["q_median"] > (x.min() + x.max()) / 2
        ax.annotate(f"常用 {stat['q_median']:.0f} m³/min · {stat['eta_at_qmedian']:.1f}%",
                    xy=(stat["q_median"], high + 6), xytext=(-5 if op_right else 5, 0),
                    textcoords="offset points", fontsize=8.5, color="#2e7d32", va="top",
                    ha="right" if op_right else "left")

        ax.set_title(f"{machine}（有效 {stat['valid_minutes']:,} 分钟，{stat['bins']} 箱）", fontsize=11.5)
        ax.set_xlabel("入口风量（m³/min，工况）", fontsize=10)
        ax.grid(alpha=0.25, linestyle=":")
        ax.text(0.985, 0.03,
                f"背压 {stat['dp_median']:.0f} kPa（{stat['dp_p10']:.0f}–{stat['dp_p90']:.0f}）\n"
                f"进气 {stat['t_median']:.1f} ℃ · 峰值−常用 {stat['gap_to_peak']:+.2f} 点",
                transform=ax.transAxes, fontsize=8.2, va="bottom", ha="right", color="#555555")

    for ax in axes[:, 0]:
        ax.set_ylabel("风机效率（气动功率 / 电机输入功率，%）", fontsize=10)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, frameon=False, fontsize=10)
    fig.suptitle("台儿庄六台曝气风机：实测风量-效率曲线（2025-05 ~ 2026-10，逐分钟有效样本分箱中位）",
                 fontsize=14)
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))
    out = PLOTS / "efficiency-curves-6machines.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"写出 {out}")


def _plot_dp(curves: pd.DataFrame) -> None:
    """同一台风机在低/中/高压三段的曲线：验证「效率不是纯风量的函数」。"""
    plt.rcParams["font.sans-serif"] = CJK_FONTS
    plt.rcParams["axes.unicode_minus"] = False
    machines = [m for m in ORDER if m in set(curves["machine"])]
    if not machines:
        return
    colors = ["#1f6f8b", "#e08b1e", "#8e2f2f"]
    fig, axes = plt.subplots(2, 3, figsize=(16.5, 10.0))
    for ax, machine in zip(axes.ravel(), machines):
        table = curves[curves["machine"] == machine]
        for color, (label, part) in zip(colors, table.groupby("dp_group", sort=True)):
            ax.plot(part["q_median"], part["eta_excel"] * 100, color=color, linewidth=2.0,
                    marker="o", markersize=3, label=label)
        ax.set_title(f"{machine}：按背压三段", fontsize=11.5)
        ax.set_xlabel("入口风量（m³/min，工况）", fontsize=10)
        ax.grid(alpha=0.25, linestyle=":")
        ax.legend(fontsize=8.5, frameon=False, loc="lower right")
    for ax in axes.ravel()[len(machines):]:
        ax.axis("off")
    for ax in axes[:, 0]:
        ax.set_ylabel("风机效率（Excel 口径，%）", fontsize=10)
    fig.suptitle("同一台风机在不同背压下的效率曲线：说明「效率 = f(风量, 背压)」，不是单值函数", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out = PLOTS / "efficiency-curves-6machines-dp.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"写出 {out}")


if __name__ == "__main__":
    raise SystemExit(main())
