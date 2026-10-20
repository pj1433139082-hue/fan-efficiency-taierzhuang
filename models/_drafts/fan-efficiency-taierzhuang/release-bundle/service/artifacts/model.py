"""风机效率实时计算（台儿庄）—— 双口径纯函数实现。

本文件是 bundle-build 的唯一打包入口，必须自包含：不访问网络、不读凭据、不依赖项目内其他模块。

口径
----
气动功率（修正口径，物理正确）::

    W = (1/K) · P入口 · Q入口 · [(P出口/P入口)^K − 1]      P 用 kPa，Q 用 m³/s

Excel 口径（现场模板原式，平台本期生效值）::

    W = (1/K) · P标准 · Q标准 · [(P出口/P入口)^K − 1]

两者只差 ``T入口/T标准`` 一个因子：``P入口·Q入口 = P标准·Q标准·(T入口/T标准)``。
20 ℃ 时完全相同；气温偏离 20 ℃ 时 Excel 口径分别高估（低温）或低估（高温）。

数据处理七条（见 docs/数据质量对抗性审查.md、decisions.yaml#build-data-quality-treatment）
------------------------------------------------------------------------------------
1. 有效性以「行」为单位：任一物理量越界 → 整行无效，不逐信号丢弃。
2. 边界用物理极限（可配置），不用统计分位。
3. 连续无效超过 6 h 为 DATA_OUTAGE（整段不可用），由 :func:`mark_outages` 标记。
4. 停机双条件：运行时需 ``run_flag == 1`` 且 ``motor_power >= 5 kW``。
5. 先过滤再聚合；聚合用时间加权均值。
6. 1 分钟内有效样本 < 2 点或有效覆盖 < 30 s → NO_SAMPLE（不插值、不外推）。
7. 每个输出点带质量标记（见 QUALITY_CODES）。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# ---------------------------------------------------------------- 常量（可审计）

K = 0.2857                    # (k-1)/k，k = 1.4 空气比热比
P_STD_KPA = 101.325           # 标准大气压（记账基准，不是一个真实位置的压力）
T_STD_K = 293.15              # 标准温度 20 ℃
C18 = 354635.537746665        # Excel 前导常数 = (1/K)·P0'，P0' = C18·K ≈ 101319.4 Pa
DEFAULT_ELEVATION_M = 38.5    # 台儿庄竣工图设计海拔（模板示例值，待现场确认）

# 行级物理边界（配置项：待厂家额定确认后调整，不要散落在代码里）
LIMITS: dict[str, tuple[float, float]] = {
    "inlet_flow": (0.0, 250.0),           # m³/min
    "outlet_pressure": (0.0, 120.0),      # kPa（表压）
    "motor_power": (0.0, 300.0),          # kW
    "inlet_temperature": (-20.0, 60.0),   # ℃
    "elevation": (-100.0, 5000.0),        # m
}

STOP_POWER_KW = 5.0          # 低于额定约 3%，气动功率已无意义
MIN_SAMPLES = 2              # 每分钟最少有效样本数
MIN_COVERAGE_S = 30.0        # 每分钟最少有效覆盖时长
OUTAGE_SECONDS = 6 * 3600    # 连续无效超过 6 h 记为数据中断
ETA_RANGE = (0.2, 1.0)       # 效率合理区间（铭牌额定 65%~77%）

# ---------------------------------------------------------------- 质量标记

VALID = "VALID"
NO_SAMPLE = "NO_SAMPLE"
ROW_INVALID = "ROW_INVALID"
STOPPED = "STOPPED"
DATA_OUTAGE = "DATA_OUTAGE"
RATIO_INVALID = "RATIO_INVALID"
OUT_OF_RANGE = "OUT_OF_RANGE"

QUALITY_CODES = (VALID, NO_SAMPLE, ROW_INVALID, STOPPED, DATA_OUTAGE,
                 RATIO_INVALID, OUT_OF_RANGE)

OUTPUT_COLUMNS = ("eta_excel", "eta_corrected", "wp_excel", "wp_corrected", "quality")


# ---------------------------------------------------------------- 单点计算

def entry_pressure_kpa(elevation_m: float) -> float:
    """当地大气压（kPa）：海拔每升高 12 m，水银柱约降 0.133 kPa。"""
    return P_STD_KPA - elevation_m / 12.0 * 0.133


def standard_flow(flow_actual: float, temperature_c: float, elevation_m: float) -> float:
    """工况流量 → 标况流量（Nm³/min）：``Q标准 = Q入口·(P入口/P标准)·(T标准/T入口)``。"""
    p1 = entry_pressure_kpa(elevation_m)
    t1 = temperature_c + 273.15
    return flow_actual * (p1 / P_STD_KPA) * (T_STD_K / t1)


def pressure_ratio(dp_kpa: float, elevation_m: float) -> float:
    """压比 ``Π = P出口/P入口``（两个都是真实压力，与记账基准无关）。"""
    p1 = entry_pressure_kpa(elevation_m)
    return (p1 + dp_kpa) / p1


def corrected_power(flow_actual: float, dp_kpa: float, elevation_m: float) -> float:
    """修正口径气动功率（kW）：前导项用进气口状态 ``P入口·Q入口``。"""
    if flow_actual <= 0:
        return 0.0
    p1 = entry_pressure_kpa(elevation_m)
    ratio = (p1 + dp_kpa) / p1
    return (p1 / K) * (flow_actual / 60.0) * (ratio ** K - 1.0)


def excel_power(flow_std: float, dp_kpa: float, elevation_m: float) -> float:
    """Excel 口径气动功率（kW）：逐字复现模板 ``C20 = C18*C6/60*(C19^C14-1)/1000``。"""
    if flow_std <= 0:
        return 0.0
    ratio = pressure_ratio(dp_kpa, elevation_m)
    return C18 * 1e-3 * (flow_std / 60.0) * (ratio ** K - 1.0)


def _in_range(name: str, value: float) -> bool:
    lo, hi = LIMITS[name]
    return lo <= value <= hi


def classify_row(flow_actual: Any, dp_kpa: Any, motor_power: Any,
                 temperature_c: Any, elevation_m: Any) -> str:
    """行级有效性判定。整行判定：任一分量越界即整行无效。

    判定顺序是有讲究的：**先排除越界，再判停机，最后判运行态异常**。
    停机时 ``V_IF≈0、V_OP≈0、V_P≈0`` 是合法读数（实测停机分钟占二污 64%），
    若把 0 当脏值，停机分钟会被误判成「没有数据」，运行率与覆盖率全部失真。
    """
    values = (flow_actual, dp_kpa, motor_power, temperature_c, elevation_m)
    if any(v is None or isinstance(v, str) or _is_nan(v) for v in values):
        return NO_SAMPLE
    if not (_in_range("inlet_flow", flow_actual)
            and _in_range("outlet_pressure", dp_kpa)
            and _in_range("motor_power", motor_power)
            and _in_range("inlet_temperature", temperature_c)
            and _in_range("elevation", elevation_m)):
        return ROW_INVALID
    if motor_power < STOP_POWER_KW:
        return STOPPED
    if dp_kpa <= 0:
        return RATIO_INVALID
    if flow_actual <= 0:
        return ROW_INVALID
    return VALID


def _is_nan(value: Any) -> bool:
    try:
        return value != value  # NaN 不等于自身
    except Exception:  # pragma: no cover - 防御性
        return True


NAN = float("nan")


def compute_row(flow_actual: float, dp_kpa: float, motor_power: float,
                temperature_c: float, elevation_m: float) -> dict[str, Any]:
    """单行双口径计算（不做有效性判定，调用方应先跑 :func:`classify_row`）。"""
    q_std = standard_flow(flow_actual, temperature_c, elevation_m)
    ratio = pressure_ratio(dp_kpa, elevation_m)
    if ratio <= 1.0:
        return {"eta_excel": NAN, "eta_corrected": NAN, "wp_excel": NAN,
                "wp_corrected": NAN, "quality": RATIO_INVALID,
                "q_std": q_std, "ratio": ratio, "temperature_ratio": NAN}

    wp_corrected = (entry_pressure_kpa(elevation_m) / K) * (flow_actual / 60.0) * (ratio ** K - 1.0)
    wp_excel = C18 * 1e-3 * (q_std / 60.0) * (ratio ** K - 1.0)

    # 停机：实测停机时 V_P 读到 0 是合法读数（停机分钟占二污 64%），
    # 直接拿它当分母会抛 ZeroDivisionError，把一条合法读数变成一次崩溃。
    # 气动功率照算（停机时本就接近 0），效率置空、标记 STOPPED。
    if motor_power is None or _is_nan(motor_power) or motor_power < STOP_POWER_KW:
        return {"eta_excel": NAN, "eta_corrected": NAN,
                "wp_excel": wp_excel, "wp_corrected": wp_corrected,
                "quality": STOPPED, "q_std": q_std, "ratio": ratio,
                "temperature_ratio": (temperature_c + 273.15) / T_STD_K}

    eta_corrected = wp_corrected / motor_power
    eta_excel = wp_excel / motor_power

    lo, hi = ETA_RANGE
    quality = VALID if (lo <= eta_corrected <= hi and lo <= eta_excel <= hi) else OUT_OF_RANGE
    if quality != VALID:
        return {"eta_excel": NAN, "eta_corrected": NAN, "wp_excel": NAN,
                "wp_corrected": NAN, "quality": quality,
                "q_std": q_std, "ratio": ratio, "temperature_ratio": NAN}

    return {
        "eta_excel": eta_excel,
        "eta_corrected": eta_corrected,
        "wp_excel": wp_excel,
        "wp_corrected": wp_corrected,
        "quality": VALID,
        "q_std": q_std,
        "ratio": ratio,
        "temperature_ratio": (temperature_c + 273.15) / T_STD_K,
    }


# ---------------------------------------------------------------- 分钟聚合

def aggregate_minute(samples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """把一分钟内的 15 s 原始样本聚合成一行（时间加权均值）。

    ``samples`` 每项需含 ``inlet_flow`` / ``outlet_pressure`` / ``motor_power`` /
    ``inlet_temperature`` / ``elevation`` / ``run_flag`` / ``dt_s``（该样本代表的秒数）。

    返回四个物理量的加权均值 + 质量标记。不做插值、不外推。
    """
    buckets = {name: [] for name in
               ("inlet_flow", "outlet_pressure", "motor_power",
                "inlet_temperature", "elevation")}
    run_weight = 0.0
    coverage = 0.0
    n_valid = 0
    n_bad_rows = 0

    for sample in samples:
        dt = float(sample.get("dt_s") or 0.0)
        if dt <= 0:
            continue
        status = classify_row(sample.get("inlet_flow"), sample.get("outlet_pressure"),
                              sample.get("motor_power"), sample.get("inlet_temperature"),
                              sample.get("elevation"))
        # STOPPED 表示「物理上有效但机组没在出力」，仍应参与聚合（否则停机分钟会误报 NO_SAMPLE）。
        if status in (ROW_INVALID, NO_SAMPLE, RATIO_INVALID):
            n_bad_rows += 1
            continue
        for name in buckets:
            buckets[name].append((float(sample[name]), dt))
        if int(sample.get("run_flag") or 0) == 1:
            run_weight += dt
        coverage += dt
        n_valid += 1

    if n_valid < MIN_SAMPLES or coverage < MIN_COVERAGE_S:
        return {"inlet_flow": None, "outlet_pressure": None, "motor_power": None,
                "inlet_temperature": None, "elevation": None,
                "run_share": None, "n_samples": n_valid, "quality": NO_SAMPLE,
                "n_invalid": n_bad_rows}

    def weighted_mean(pairs: list[tuple[float, float]]) -> float:
        total = sum(w for _, w in pairs)
        return sum(v * w for v, w in pairs) / total if total else 0.0

    row: dict[str, Any] = {name: weighted_mean(pairs) for name, pairs in buckets.items()}
    row["run_share"] = run_weight / coverage
    row["n_samples"] = n_valid
    row["n_invalid"] = n_bad_rows
    # 停机双条件：S_RUN 与功率都要满足
    if row["run_share"] < 0.5 or row["motor_power"] < STOP_POWER_KW:
        row["quality"] = STOPPED
    else:
        row["quality"] = VALID
    return row


def mark_outages(flags: Iterable[str], step_seconds: float = 60.0,
                 threshold_seconds: float = OUTAGE_SECONDS) -> list[str]:
    """把「连续无效超过阈值」的样本段整体标成 DATA_OUTAGE。

    只改 NO_SAMPLE / ROW_INVALID 这类「没数据」的标记，不动 STOPPED / VALID。
    """
    result = list(flags)
    run_start: int | None = None
    for index, flag in enumerate(result + [VALID]):
        if flag in (NO_SAMPLE, ROW_INVALID):
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            span = (index - run_start) * step_seconds
            if span > threshold_seconds:
                for i in range(run_start, index):
                    result[i] = DATA_OUTAGE
            run_start = None
    return result


# ---------------------------------------------------------------- 契约入口

def predict(inputs: object) -> object:
    """契约入口：接收 pandas DataFrame（或列名到序列的映射），返回契约输出列。

    输入列：``inlet_flow`` / ``outlet_pressure`` / ``motor_power`` /
    ``inlet_temperature`` / ``elevation``（缺 ``elevation`` 时取默认海拔）。
    输出列：``eta_excel`` / ``eta_corrected`` / ``wp_excel`` / ``wp_corrected`` /
    ``quality``。无效行四个数值为 NaN，``quality`` 给出原因码。
    """
    import pandas as pd

    frame = inputs if isinstance(inputs, pd.DataFrame) else pd.DataFrame(inputs)
    for name in ("inlet_flow", "outlet_pressure", "motor_power", "inlet_temperature"):
        if name not in frame.columns:
            raise KeyError(f"missing required input column: {name}")

    elevation = (frame["elevation"] if "elevation" in frame.columns
                 else pd.Series(DEFAULT_ELEVATION_M, index=frame.index))

    rows: list[dict[str, Any]] = []
    for flow, dp, power, temperature, height in zip(
            frame["inlet_flow"], frame["outlet_pressure"], frame["motor_power"],
            frame["inlet_temperature"], elevation):
        status = classify_row(flow, dp, power, temperature, height)
        if status != VALID:
            rows.append({"eta_excel": float("nan"), "eta_corrected": float("nan"),
                         "wp_excel": float("nan"), "wp_corrected": float("nan"),
                         "quality": status})
            continue
        rows.append(compute_row(float(flow), float(dp), float(power),
                                float(temperature), float(height)))

    return pd.DataFrame(rows, index=frame.index)[list(OUTPUT_COLUMNS)]
