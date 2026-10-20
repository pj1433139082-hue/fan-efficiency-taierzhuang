# 风机效率实时计算（台儿庄）

污水处理厂曝气风机的**实时效率（风机效率 η）**计算模型。现场给五个读数（进风流量、出口压力、电机功率、进气温度、海拔）就直接算出风机效率与风压功率——不训练、不依赖历史数据，加载端零第三方依赖。

- 模型 ID：`fan-efficiency-taierzhuang`
- 版本：`0.1.0`
- 打包格式：MLflow pyfunc（`cpu-iot` 目标、Python 3.11、mlflow 2.22.2）
- 生命周期：`releasable`。框架的 `model-lab publish` 只支持点码模型，本模型走集团技能包 A 通道（`scripts/deploy-kit/fan-efficiency-taierzhuang/deploy.py`）上传。

## 本仓库是脱敏公开版

这份快照用于代码与方法展示，**不是**完整的项目档案。相对内部仓库，这里做了三件事：

1. 删除客户现场材料与实测数据（`data/` 全部、`work/` 分析产物、`plots/` 实测信号图，以及两份含现场对接内容的记录文档）；
2. 内网主机名与厂站编码统一替换为占位符（`<internal-host>` / `<plant-code>`），平台 Run 直链不对外给出；
3. 负责人账号替换为 `<owner-id>`。

因此文中的平台链接、点位编码不是可直接使用的真实值；要复现现场数据请联系模型负责人。

## 平台身份（2026-10-20 换绑后）

| 项 | 值 |
|---|---|
| 实验 | `MLModel-2563_V001`（id 176） |
| Run ID | `26df4cd32bc6489196a33dba3d975878` |
| Run 名 | `invincible-wasp-339` |
| 模型 URI | `runs:/26df4cd32bc6489196a33dba3d975878/service` |
| 平台页 | 内网 MLflow 平台（地址不对外公开） |
| Run 状态 | `FINISHED`；平台部署配置已换绑到该 Run，试运行成功（2026-10-20） |

上一版 Run `5fd59ee0b6c94f64b9935a50614ff46e`（`lyrical-skink-360`）已打 `model_lab_superseded_by` / `model_lab_superseded_reason` 标签，只作对照：它的输入签名是 `double`，平台上参数值写整数（`64`）会被推断成 `int64`，在 `predict` 前被 schema 校验拒掉；在用 Run 的五个输入签名是 `any`，整数与小数都能过。

## 输入 / 输出

**输入（5 列，顺序固定）**

| 列 | 含义 | 单位 |
|---|---|---|
| `inlet_flow` | 进风流量（标况） | m³/min |
| `outlet_pressure` | 出口压力（背压） | kPa |
| `motor_power` | 电机输入功率 | kW |
| `inlet_temperature` | 进气温度 | ℃ |
| `elevation` | 海拔 | m |

**输出（5 列）**

| 列 | 含义 | 单位 |
|---|---|---|
| `eta_excel` | 风机效率（**Excel 口径，平台生效值**） | 1 |
| `eta_corrected` | 风机效率（修正口径，并列输出） | 1 |
| `wp_excel` | 风压功率（Excel 口径） | kW |
| `wp_corrected` | 风压功率（修正口径） | kW |
| `quality` | 数据质量码（`VALID` / `SUSPECT_` 等） | code |

口径决定：平台生效读 `eta_excel`（与用户方 Excel 工具对齐），`eta_corrected` 仅供对照。

## 快速开始

### 1. 本地加载（值内联包，零依赖）

`release-bundle/service` 里的类与函数都按**值**内联，下载目录即可加载，不需要 `custom_sources` 注入、也不需要本仓库在 `sys.path` 上：

```python
import pandas as pd
import mlflow.pyfunc

m = mlflow.pyfunc.load_model("models/_drafts/fan-efficiency-taierzhuang/release-bundle/service")
df = pd.DataFrame([{
    "inlet_flow": 46.1, "outlet_pressure": 46.2, "motor_power": 42.2,
    "inlet_temperature": 25.5, "elevation": 38.5,
}])
print(m.predict(df))
# 实测 eta_excel=0.717711、eta_corrected=0.731217、quality=VALID（与 src/model.py 逐字一致）
```

> 说明：仓库里这份 `release-bundle/service` 是**本地构建产物**（2026-10-18），`MLmodel` 里签名仍写 `double`——本地传**整数**会抛 `MlflowException`（`Can not safely convert int64 to float64`），要写成 `46.0` 这样的小数。平台在用的那份由部署脚本上传时已把五个输入放宽为 `any`，平台上整数与小数都能过。
>
> 另一行实测：`inlet_flow=49.2, outlet_pressure=63.1, motor_power=64.0, inlet_temperature=30.0, elevation=38.5` → `eta_excel=0.651352` / `eta_corrected=0.673609`（即 `docs/平台参数示例.json` 那一行）。

### 2. 平台调用

参数值示例见 [docs/平台参数示例.json](models/_drafts/fan-efficiency-taierzhuang/docs/平台参数示例.json)（一条真实量级读数，仅作格式示例）。整数与小数现在都能填。批量样本文件属现场数据，脱敏版未包含。

### 3. 重新上传到集团 MLflow

```powershell
cd scripts/deploy-kit/fan-efficiency-taierzhuang
python deploy.py
```

必须在该目录内执行（脚本把「当前目录下所有 `.py`」打进 `custom_sources`）；需要 `infra/` 凭据包与样本 CSV 环境变量，见部署套件 [README](scripts/deploy-kit/fan-efficiency-taierzhuang/README.md)。`infra/` 含内网凭据，**不进 Git**，需从内网/框架仓库取。

## 目录结构

```
models/_drafts/fan-efficiency-taierzhuang/   模型工作区（契约、决策、文档、源码、发布件）
├── model.yaml              机器可读契约：5 输入 / 5 输出、目标环境、制品摘要
├── model-spec.md           模型规格（口径、公式、边界）
├── decisions.yaml          设计决策与理由
├── inputs.md / questions.md / CHECKLIST.md
├── docs/                   历史数据分析、效率曲线、数据质量审查、发布记录
├── src/model.py            纯函数实现（本地零依赖）
├── notebooks/model.ipynb   建模 Notebook
├── release-bundle/service/ MLflow pyfunc 包（值内联，可直接 load）
├── runtime/                Python 3.11 运行时锁定与说明
├── tests/                  model.py 的行为测试
├── validation/             验证报告
├── releases/               版本快照说明
├── explainer/              给业务方的单页解释器（HTML）
├── learning/               经验教训与任务书
└── baselines/              行为基线说明
scripts/deploy-kit/fan-efficiency-taierzhuang/   发布套件（A 通道）
├── deploy.py               三步上传：构建包 → 内网 MLflow → 写标签
├── iot_efficiency_mlflow.py
└── README.md               套件用法与四个必知
```

## 本仓库不含什么

| 内容 | 原因 |
|---|---|
| `scripts/deploy-kit/**/infra/` | 集团 MLflow 凭据（混淆内嵌）与 pyarmor 运行时二进制，任何 Git 都不入库 |
| `data/` 全部 | 客户现场材料、实测曲线与交付样本（原始 28 个月度分片 + 逐分钟明细约 225 MB；脱敏版整个目录不导出） |
| `work/` | 取数、审计、选型的分析中间产物 |
| `plots/` | 逐参数实测信号图 |
| 内网主机名、厂站编码、负责人账号 | 以 `<internal-host>` / `<plant-code>` / `<owner-id>` 占位 |

## 来源与同步

- **权威源**：私有仓库 `learn-model`（`pj1433139082-hue/learn-model`，不公开）里的 `models/_drafts/fan-efficiency-taierzhuang/` 与 `scripts/deploy-kit/fan-efficiency-taierzhuang/`。
- 本仓库是**导出快照**，目录结构刻意与框架保持一致（含 `models/_drafts/` 前缀），方便直接比对与回灌；要改模型请回 `learn-model` 改，再重新导出，避免两边漂移。
- 本仓库带 `.export-profile.json`（`profile: public`）。重同步必须带 `--public`，否则脚本会拒绝执行——防止客户材料被重新推上来：

```powershell
.\.venv-311\Scripts\python.exe scripts\export-model-repo.py fan-efficiency-taierzhuang `
    --public --target <本仓库目录> --push
```

- 平台绑定与运维细节见快照内 [docs/发布记录-0.1.0.md](models/_drafts/fan-efficiency-taierzhuang/docs/发布记录-0.1.0.md)。

## 已知边界与待确认

- 厂家侧 5 条待确认：C-4 温度探头位置、E-3 喘振最小风量、A-2 样本风量范围、A-3 面板换算参数、D-5 功率来源；回齐后发 `0.1.1`。
- 平台本期只做批量/托管调用，不做实时流式。
- 6 台机历史口径统计见 [docs/历史数据分析报告.md](models/_drafts/fan-efficiency-taierzhuang/docs/历史数据分析报告.md)。
