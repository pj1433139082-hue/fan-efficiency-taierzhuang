"""风机效率模型 —— MLflow 部署主模块（对齐 infra_model_deploy 协议）。

本模块是平台部署入口，只做三件事：
1. 提供 ``online_feature_engineering`` / ``online_post_processing``（协议要求这两个函数
   必须定义在模型主模块里，CloudPickle 才能按模块名导出，跨 Python 版本解包不炸）；
2. 提供 ``EfficiencyModel``（pyfunc 实例，``predict(context, model_input)``）；
3. 复刻 ``src/model.py`` 的计算口径 —— 因为服务器端只会解包本模块，不会带着 ``src/``
   目录一起走，所以公式必须自包含。两侧一致性由 ``tests/test_deploy_module.py`` 兜住。

口径（与 src/model.py 逐字一致）：
    W_excel     = C18 * Q标准 / 60 * (压比^K - 1) / 1000
    W_corrected = (P入口 / K) * Q入口(秒) * (压比^K - 1) / 1000
    eta         = W / 电机输入功率
"""

from __future__ import annotations

import math
from typing import Any, Dict

import pandas as pd

# ---------------------------------------------------------------- 物理常量
K = 0.2857
P_STD_KPA = 101.325
T_STD_K = 293.15
C18 = 354635.537746665
DEFAULT_ELEVATION_M = 38.5

# 输入合法区间（超出即判 OUT_OF_RANGE，不猜、不外推）
LIMITS: Dict[str, tuple] = {
    "inlet_flow": (0.0, 250.0),
    "outlet_pressure": (0.0, 120.0),
    "motor_power": (0.0, 300.0),
    "inlet_temperature": (-20.0, 60.0),
}
STOP_POWER_KW = 5.0
ETA_RANGE = (0.2, 1.0)

QUALITY_VALID = "VALID"
QUALITY_NO_SAMPLE = "NO_SAMPLE"
QUALITY_ROW_INVALID = "ROW_INVALID"
QUALITY_OUT_OF_RANGE = "OUT_OF_RANGE"
QUALITY_STOPPED = "STOPPED"

INPUT_COLUMNS = [
    "inlet_flow",
    "outlet_pressure",
    "motor_power",
    "inlet_temperature",
    "elevation",
]
OUTPUT_COLUMNS = [
    "eta_excel",
    "eta_corrected",
    "wp_excel",
    "wp_corrected",
    "quality",
]

# 站端读数 → 模型输入。平台数据目录按这些名字绑定测点；推参也可以直接给这几个名字。
SIGNAL_ALIASES = {
    "inlet_flow": ("inlet_flow", "V_IF", "v_if"),
    "outlet_pressure": ("outlet_pressure", "V_OP", "v_op"),
    "motor_power": ("motor_power", "V_P", "v_p"),
    "inlet_temperature": ("inlet_temperature", "V_ITIT", "v_itit"),
}


def entry_pressure_kpa(elevation_m: float) -> float:
    """当地大气压（kPa）：沿用集团模板 101.325 - 海拔/12*0.133。"""
    return P_STD_KPA - float(elevation_m) / 12.0 * 0.133


def standard_flow(inlet_flow: float, inlet_pressure_kpa: float, inlet_temp_c: float) -> float:
    """工况流量 → 标况流量（20℃、101.325 kPa）。"""
    t1 = inlet_temp_c + 273.15
    if t1 <= 0:
        raise ValueError(f"入口温度换算开尔文后非正: {inlet_temp_c}")
    return inlet_flow * inlet_pressure_kpa / t1 / P_STD_KPA * T_STD_K


def _is_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def compute_one(
    inlet_flow: Any,
    outlet_pressure: Any,
    motor_power: Any,
    inlet_temperature: Any,
    elevation_m: Any = DEFAULT_ELEVATION_M,
) -> Dict[str, Any]:
    """单行计算，返回 OUTPUT_COLUMNS 的字典（含 quality）。"""
    raw = {
        "inlet_flow": inlet_flow,
        "outlet_pressure": outlet_pressure,
        "motor_power": motor_power,
        "inlet_temperature": inlet_temperature,
    }
    if not all(_is_number(v) for v in raw.values()):
        return _empty(QUALITY_NO_SAMPLE)
    if not _is_number(elevation_m):
        return _empty(QUALITY_ROW_INVALID)

    values = {k: float(v) for k, v in raw.items()}
    for name, (low, high) in LIMITS.items():
        if not low <= values[name] <= high:
            return _empty(QUALITY_OUT_OF_RANGE)

    power = values["motor_power"]
    if power < STOP_POWER_KW:
        return _empty(QUALITY_STOPPED)

    flow = values["inlet_flow"]
    dp = values["outlet_pressure"]
    temp = values["inlet_temperature"]
    if flow <= 0 or dp <= 0:
        return _empty(QUALITY_ROW_INVALID)

    p1 = entry_pressure_kpa(elevation_m)
    p2 = p1 + dp
    ratio = p2 / p1
    try:
        q_std = standard_flow(flow, p1, temp)
    except ValueError:
        return _empty(QUALITY_ROW_INVALID)

    factor = ratio**K - 1.0
    # P1 单位 kPa，先换 Pa 再乘体积流量（m3/s）得瓦，末尾 /1000 回 kW
    wp_excel = C18 * q_std / 60.0 * factor / 1000.0
    wp_corrected = (p1 * 1000.0 / K) * (flow / 60.0) * factor / 1000.0
    eta_excel = wp_excel / power
    eta_corrected = wp_corrected / power

    if not (ETA_RANGE[0] <= eta_excel <= ETA_RANGE[1]):
        return _empty(QUALITY_OUT_OF_RANGE)
    if not (ETA_RANGE[0] <= eta_corrected <= ETA_RANGE[1]):
        return _empty(QUALITY_OUT_OF_RANGE)

    return {
        "eta_excel": round(eta_excel, 6),
        "eta_corrected": round(eta_corrected, 6),
        "wp_excel": round(wp_excel, 6),
        "wp_corrected": round(wp_corrected, 6),
        "quality": QUALITY_VALID,
    }


def _empty(quality: str) -> Dict[str, Any]:
    return {
        "eta_excel": math.nan,
        "eta_corrected": math.nan,
        "wp_excel": math.nan,
        "wp_corrected": math.nan,
        "quality": quality,
    }


def _first_present(row: Dict[str, Any], names: tuple) -> Any:
    for name in names:
        if name in row and row[name] is not None and str(row[name]).strip() not in ("", "nan", "NaN", "None", "null"):
            return row[name]
    return None


def _row_to_inputs(merged: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for target, aliases in SIGNAL_ALIASES.items():
        out[target] = _first_present(merged, aliases)
    elevation = _first_present(merged, ("elevation", "elevation_m", "ELEVATION"))
    out["elevation"] = DEFAULT_ELEVATION_M if elevation is None else elevation
    return out


def online_feature_engineering(raw_df: Any, aux_dict: Any) -> pd.DataFrame:
    """协议入口：把 HTTP /predict 推参（raw_df）与默认配置（aux_dict）合成模型输入。

    必填 = 四个读数（缺则 ValueError，不静默兜底）；非必填 = elevation（默认 38.5 m）。
    """
    merged: Dict[str, Any] = dict(aux_dict) if isinstance(aux_dict, dict) else {}
    if isinstance(raw_df, pd.DataFrame) and not raw_df.empty:
        row = raw_df.iloc[0].to_dict()
        for key, value in row.items():
            if value is None:
                continue
            text = str(value).strip()
            if text and text.lower() not in ("none", "nan", "null"):
                merged[key] = value

    inputs = _row_to_inputs(merged)
    missing = [name for name in ("inlet_flow", "outlet_pressure", "motor_power", "inlet_temperature") if inputs[name] is None]
    if missing:
        raise ValueError(
            "缺少必填读数: " + ", ".join(missing) + "（可通过 /predict 的 data 传入 inlet_flow/outlet_pressure/motor_power/inlet_temperature）"
        )
    return pd.DataFrame([inputs], columns=INPUT_COLUMNS)


def online_post_processing(raw_prediction: Any, aux_dict: Any) -> pd.DataFrame:
    """协议出口：把 predict 结果整理成平台可读的一行。

    raw_prediction 可能是 predict 返回的 DataFrame，也可能是已算好的结果列表。
    只做列序整理与类型收口，不做二次计算，避免与 predict 口径分叉。
    """
    frame = raw_prediction if isinstance(raw_prediction, pd.DataFrame) else pd.DataFrame(raw_prediction)
    for column in OUTPUT_COLUMNS:
        if column not in frame.columns:
            frame[column] = math.nan if column != "quality" else QUALITY_NO_SAMPLE
    return frame[OUTPUT_COLUMNS].reset_index(drop=True)


class EfficiencyModel:
    """pyfunc 实例：一批读数进，五个输出 + 质量码出。"""

    def predict(self, context: Any, model_input: Any) -> pd.DataFrame:
        frame = model_input if isinstance(model_input, pd.DataFrame) else pd.DataFrame(model_input)
        rows = []
        for _, row in frame.iterrows():
            rows.append(
                compute_one(
                    row.get("inlet_flow"),
                    row.get("outlet_pressure"),
                    row.get("motor_power"),
                    row.get("inlet_temperature"),
                    row.get("elevation", DEFAULT_ELEVATION_M),
                )
            )
        return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


# ---------------------------------------------------------------------------
# 打包自包含（放在文件末尾，import 即生效）
#
# 集团技能包会把本模块源码塞进 artifacts/custom_sources.pkl，由 ProductionWrapper
# 在 load_context 里先 exec 注册、再反序列化 logic_bundle.pkl，所以平台主链路能加载。
# 但任何**不经过 ProductionWrapper** 的调用方都会失败：
#   - 直接 mlflow.pyfunc.load_model 读下载下来的 service 目录；
#   - 平台侧按常规模板加载的预览/校验页面；
#   - 自助回放脚本。
# 它们在反序列化 logic_bundle.pkl 里的 EfficiencyModel 时找不到本模块，报
#   ModuleNotFoundError: No module named 'iot_efficiency_mlflow'
#
# register_pickle_by_value 让 cloudpickle 在 dump 时把本模块的类与函数**按源码内联**，
# 反序列化端不再需要在 sys.path 上找到本文件。只在打包进程内生效，运行期零副作用。
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 打包进程内生效
    import sys as _sys

    import cloudpickle as _cloudpickle

    _cloudpickle.register_pickle_by_value(_sys.modules[__name__])
except Exception:  # pragma: no cover - 缺 cloudpickle 时退回原行为
    pass
