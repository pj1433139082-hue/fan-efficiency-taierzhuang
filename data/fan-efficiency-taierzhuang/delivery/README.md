# 台儿庄 · 对外交付物

这个目录放**要发出去的东西**（会离开本机、发给人看/填的）。模型自己的资产（`model-spec.md`、`decisions.yaml`、分析报告）在
`models/_drafts/fan-efficiency-taierzhuang/` 里，不放这儿。

| 文件 | 用途 |
|---|---|
| `对接清单-对外.docx` / `.xlsx` | **发放版，以此为准**。给对方的冷读者版：开头「谁发的/为什么找你/答完回到哪」+ 术语与编号对照 + 一~七节（点位对照、厂家先答 7 条、可后补 8 条、数采 5 条、生产业主 4 条、实测运行范围、怎么回）|
| `对接清单-内部.docx` / `.xlsx` | 内部版，多一列「影响（内部分析）」，其余同对外版 |
| `对外发送-台儿庄点位与问题清单.md` | 可复制文案版（聊天/邮件里贴文字用）。**比 docx 少术语表与第七节回传说明**，两者冲突时以 docx 为准 |
| `fan-efficiency-demo-v0.1.0.zip` | 零依赖试跑包（给领导跑数看结果用） |

## 为什么这些都放在 `data/` 而不是模型工作区

两个原因：

1. **模型工作区禁止二进制表格**。`model_lab` 的 `workspace-safe` 门禁把 `.xlsx` 归入原始数据后缀（`model_lab/security_audit.py` 的
   `_RAW_DATA_SUFFIXES`），工作区里出现 `.xlsx` 会让 `model_lab status` 直接 BLOCK。
2. **对外交付物不是模型资产**。它们不属于版本化契约、不参与门禁、每次内容改了重新生成即可，跟 `raw/`、`hourly.parquet` 一样属于"本机大文件"，
   已由 `.gitignore` 排除。

这样两个厂的布局就一致了：`data/<厂>/` 下统一是 `raw/`（原始数据，丢了只能重拉）、`derived/`（中间结果，删了能重算）、`delivery/`（对外交付物）。

## 谁生成的

`models/_drafts/fan-efficiency-taierzhuang/work/tools/make_docs_taierzhuang.py`（openpyxl + python-docx，一次产 4 个 office 文件）。改了内容要重新生成，别手改 office 文件。需要装 openpyxl/python-docx 的解释器——`.venv-311` 里没有，用 DSH 运行时 python。

生成后要过两道检查：`officecli validate <file>` 必须 `success: true`（校验前先 `officecli close <file>`，否则读到的是常驻进程里的旧副本）；再 `officecli view <file> screenshot --page N` 看版面有没有破页、断行、串列。

## 与协作仓库的关系

协作仓库 `blower-optimization` 里另有一份归档副本，位置是 `blower-optimization/models/fan-efficiency-taierzhuang/docs/对接/`（那边没有这条门禁，
md/docx/xlsx 四个文件放在一起）。本目录是本地工作副本，以本目录为准。
