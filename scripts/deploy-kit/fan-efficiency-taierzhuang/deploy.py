"""风机效率模型 —— 平台发布脚本（infra_model_deploy 协议）。

用法（在 deploy-kit/fan-efficiency-taierzhuang 执行，需内网可达 MLflow）：

    python deploy.py

三步验证：
    Step 1 本地验证  → ✅ 本地验证通过
    Step 2 打包上传  → ✅ 上传成功! Run ID: ...
    Step 3 云端回测  → ✅ 云端回测成功

口径与参数：
    - 实验名 / 作者来自 MODEL_CARD，脚本启动时会再次校验，占位符直接拒绝；
    - 五个输入（inlet_flow / outlet_pressure / motor_power / inlet_temperature / elevation）
      由 AUX_DICT 兜底、由 /predict 推参覆盖；
    - 不打包明文源码（code_paths=None），业务逻辑进 logic_bundle.pkl。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Tuple

# Windows UTF-8 控制台
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 内网服务不吃系统代理
for _proxy in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_proxy, None)
os.environ["NO_PROXY"] = "<internal-host>,localhost,127.0.0.1,*"
os.environ["no_proxy"] = "<internal-host>,localhost,127.0.0.1,*"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INFRA_DIR = os.path.join(SCRIPT_DIR, "infra")
for _path in (INFRA_DIR, SCRIPT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from mlops_deploy_iot2 import run_pipeline_and_deploy  # noqa: E402

from iot_efficiency_mlflow import (  # noqa: E402
    DEFAULT_ELEVATION_M,
    EfficiencyModel,
    online_feature_engineering,
    online_post_processing,
)

# ------------------------------------------------------------------ 部署配置
# elevation 有默认值（台儿庄 38.5 m）；四个读数必填，推参未给则拒绝，不做静默兜底。
AUX_DICT: Dict[str, Any] = {
    "inlet_flow": None,
    "outlet_pressure": None,
    "motor_power": None,
    "inlet_temperature": None,
    "elevation": DEFAULT_ELEVATION_M,
}

# 发布标识：平台实验名逐字采用绑定页名称，作者为部署责任人（不是平台认证凭据）
MODEL_CARD: Dict[str, str] = {
    "model_id": "fan-efficiency-taierzhuang",
    "model_version": "0.1.0",
    "caliber": "excel",
    "experiment_name": "MLModel-2563_V001",
    "author": "<owner-id>",
}

# 验证样本：五个输入列各取一条真实分钟读数（见 data/fan-efficiency-taierzhuang/delivery/deploy-sample.csv）
_CSV_NAME = "deploy-sample.csv"
_REPO_ROOT = os.environ.get("FAN_REPO_ROOT") or os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
SAMPLE_CSV_PATH = os.environ.get(
    "FAN_DEPLOY_SAMPLE",
    os.path.join(_REPO_ROOT, "data", "fan-efficiency-taierzhuang", "delivery", _CSV_NAME),
)

EVALUATION: Dict[str, Any] = {}


def enable_tolerant_input_schema() -> None:
    """把 5 个输入列的声明类型放宽为 any，避免平台整数参数被 schema 校验挡掉。

    平台按「参数值」JSON 组 DataFrame：像 `64`、`30` 这种整数值会推断成 int64，
    而 MLflow 的 pandas schema 校验只放行 itemsize ≤ 6 的整数转 double
    （site-packages/mlflow/models/utils.py 里 `is_upcast = values.dtype.itemsize <= 6`），
    于是 int64 的 motor_power 会在 predict 之前就抛
    `Failed to enforce schema ... Can not safely convert int64 to float64`。

    这里只改「声明类型」，不动列名与顺序——平台展示输入输出参数定义靠的仍是同一份
    signature；真正的类型兜底在 online_feature_engineering 里做（四个读数会转 float）。
    """

    import mlflow.models as _mlmodels
    from mlflow.types.schema import AnyType, ColSpec, Schema

    original = _mlmodels.infer_signature
    if getattr(original, "_fan_tolerant", False):
        return

    def infer_signature(inputs, outputs=None, **kwargs):  # noqa: ANN001
        sig = original(inputs, outputs, **kwargs)
        cols = [ColSpec(AnyType(), column.name) for column in sig.inputs]
        return _mlmodels.ModelSignature(inputs=Schema(cols), outputs=sig.outputs)

    infer_signature._fan_tolerant = True  # type: ignore[attr-defined]
    _mlmodels.infer_signature = infer_signature


def prompt_for_experiment_and_author(default_exp: str, default_author: str) -> Tuple[str, str]:
    """部署前强制校验，防止占位符在平台上生成无用实验。"""

    def is_invalid(value: str) -> bool:
        text = (value or "").strip()
        return not text or "XXX" in text.upper() or "DEFAULT" in text.upper() or text.lower() in ["anonymous", "admin", "user"]

    exp_name, author = (default_exp or "").strip(), (default_author or "").strip()
    interactive = sys.stdin.isatty() or os.environ.get("INTERACTIVE_DEPLOY") == "1"

    print("\n" + "=" * 65)
    print("📋 [MLOps 防护] 部署前参数确认 (防止 MLflow 生成无用实验)")
    print("=" * 65)

    if interactive:
        if is_invalid(exp_name):
            while True:
                value = input("👉 请输入 MLflow 实验名称 (例如 MLModel-2555_V001): ").strip()
                if not is_invalid(value):
                    exp_name = value
                    break
                print("   ❌ 实验名称不能包含占位符 'XXX' 或为空，请重新输入！")
        if is_invalid(author):
            while True:
                value = input("👉 请输入模型作者/部署责任人 (Author): ").strip()
                if not is_invalid(value):
                    author = value
                    break
                print("   ❌ 作者名称不能为默认值或为空，请重新输入！")
    else:
        if is_invalid(exp_name):
            raise ValueError(f"❌ 部署失败：EXPERIMENT_NAME 不能使用占位符 '{exp_name}'！")
        if is_invalid(author):
            raise ValueError(f"❌ 部署失败：USERNAME/Author 不能使用默认名称 '{author}'！")

    print(f"   ✅ 参数确认通过 -> Experiment: {exp_name} | Author: {author}")
    print("=" * 65 + "\n")
    return exp_name, author


def _make_bundle_self_contained() -> None:
    """打包前把运行时也改成「按值序列化」，让产物脱离 deploy-kit 目录也能加载。

    技能包里 `ProductionWrapper.__module__` 被改写成 ``mlflow.pyfunc.model``，
    cloudpickle 于是按**引用**记录真实模块 ``mlops_deploy_iot2``；任何没有把
    ``infra/`` 放进 sys.path 的加载端（下载 Run 直接 load、平台部分预览页、
    自助脚本）都会报 ``ModuleNotFoundError: No module named 'mlops_deploy_iot2'``。

    这里把模块名指回真实模块再按值注册，类与方法按源码内联进 python_model.pkl。
    主模块 `iot_efficiency_mlflow` 自己也在文件末尾做了同样的事。
    """
    import cloudpickle

    import mlops_deploy_iot2 as infra

    infra.ProductionWrapper.__module__ = "mlops_deploy_iot2"
    cloudpickle.register_pickle_by_value(infra)


def main() -> str:
    exp_name, author = prompt_for_experiment_and_author(MODEL_CARD["experiment_name"], MODEL_CARD["author"])
    _make_bundle_self_contained()
    enable_tolerant_input_schema()
    if not os.path.exists(SAMPLE_CSV_PATH):
        raise FileNotFoundError(f"找不到验证样本: {SAMPLE_CSV_PATH}")
    return run_pipeline_and_deploy(
        sample_csv_path=SAMPLE_CSV_PATH,
        model_path="",
        feature_engineer_fn=online_feature_engineering,
        post_process_fn=online_post_processing,
        aux_dict=AUX_DICT,
        model_type="pyfunc",
        custom_objects={},
        experiment_name=exp_name,
        username=author,
        evaluation=EVALUATION,
        pyfunc_model=EfficiencyModel(),
    )


if __name__ == "__main__":
    main()
