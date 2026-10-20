# 部署套件 · 风机效率实测（台儿庄）0.1.0

集团部署技能包 A 通道的本项目落位。当前绑定实验 `MLModel-2563_V001`（id 176），
在用 Run `26df4cd32bc6489196a33dba3d975878`（`invincible-wasp-339`，2026-10-20 重传版：加载端零依赖 + 输入签名容忍整数；详见
`models/_drafts/fan-efficiency-taierzhuang/docs/发布记录-0.1.0.md`）。上一版 `5fd59ee0…` 是打
类型容错补丁之前上传的，已标 superseded 保留对照。同一制品最早发在实验
`MLModel-2555_V001`（175）：`8891950d…` 按值版、`d9b9625…` 按引用版（已标
`model_lab_superseded_by`），换绑后仅留作对照。平台部署配置已于 2026-10-20 换绑到在用 Run 并试运行成功。

## 目录内容

| 文件 | 作用 |
| --- | --- |
| `deploy.py` | 发布入口：MODEL_CARD + AUX_DICT + 调 `run_pipeline_and_deploy` |
| `iot_efficiency_mlflow.py` | 业务主模块：`online_feature_engineering` / `online_post_processing` / pyfunc 类；公式自包含，与 `src/model.py` 对拍一致；末尾按值注册，加载端零依赖 |
| `infra/` | 技能包原生运行时（310/311/312 + `mlops_deploy_iot2.py` + `deploy_template_iot2.py`），含 `.pyd` / `.so`，**不要放进模型工作区** |
| 样本 CSV | 不放在这里（工作区禁 `.csv` 的规则延伸到套件目录），实际路径 `data/fan-efficiency-taierzhuang/delivery/deploy-sample.csv`，24 行、列＝平台输入参数 |

## 怎么跑

```powershell
# 1) 开 VPN，确认内网可达（HTTPS 反复超时先查 MTU=1400）
Test-NetConnection <internal-host> -Port 443

# 2) 必须在本目录内执行（技能包把 cwd 下所有 .py 打进 custom_sources）
cd E:\beikong-code\learn-model\scripts\deploy-kit\fan-efficiency-taierzhuang
$env:FAN_DEPLOY_SAMPLE = "E:\beikong-code\learn-model\data\fan-efficiency-taierzhuang\delivery\deploy-sample.csv"
& "E:\beikong-code\learn-model\.venv-311\Scripts\python.exe" -u deploy.py
```

三步全 ✅（本地验证 → 打包上传 → 云端回测）才算发布完成；之后去平台「参数配置 / 数据目录」绑定输入输出。

## 三个必知

1. **不要在工作区根执行**：`custom_sources` 是「当前目录全量 `.py`」，会把 `work/analysis/*.py` 打包进去，
   Step 2 重执行取数/分析脚本，表现成卡死无输出。
2. **本模块已做「按值序列化」，加载端不再需要注册**：`iot_efficiency_mlflow.py` 末尾调了
   `cloudpickle.register_pickle_by_value(...)`，打包时类与函数按源码内联进 `logic_bundle.pkl`，
   裸 `mlflow.pyfunc.load_model` 也能加载。**2026-10-19 之前上传的 Run 不具备这一点**，仍会
   报 `ModuleNotFoundError: No module named 'iot_efficiency_mlflow'`，必须先用
   `artifacts/custom_sources.pkl` 注入 `sys.modules`。
3. **本模型不进 `models/<NN_名称>/`**：数值入参模型不受框架 `publish` 通道支持（`serving_mode_unsupported`），
   平台身份以本工作区的 `models/_drafts/fan-efficiency-taierzhuang/docs/发布记录-0.1.0.md` 为准，`lifecycle_state` 停在 `releasable`。

## 第四个必知：平台参数值写整数会炸（2026-10-20 重传修好并换绑）

平台按「参数值」JSON 组 DataFrame，`"motor_power": 64`、`"inlet_temperature": 30` 这种整数值会被推断成
`int64`；MLflow 的 pandas schema 校验只放行 `itemsize <= 6` 的整数转 `double`，于是训练任务在 `predict`
之前就抛

```
Failed to enforce schema ... Incompatible input types for column 'motor_power'.
Can not safely convert int64 to float64.
```

`deploy.py` 的 `enable_tolerant_input_schema()` 把 5 个输入列的**声明类型**改成 `any`
（列名与顺序不变，平台照样能读到 5 个输入参数），整数与小数都能过，真正的类型兜底在
`online_feature_engineering` 里做。**该补丁已于 2026-10-20 随新 Run 26df4cd32bc6489196a33dba3d975878
上线**：Run 的 `mlflow.log-model.history` 五个输入类型均为 `any`，`int64` 与 `float64`
两组同值入参预测结果 `equals=True`。**平台部署配置已于 2026-10-20 换绑到该 Run 并试运行成功**，平台上整数与小数都能直接填；只有拿旧 Run（`5fd59ee0…`）做对照时才必须写成带小数的形式（`64.0`、`30.0`）。
