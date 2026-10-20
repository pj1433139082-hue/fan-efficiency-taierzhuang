"""生成并执行 `notebooks/model.ipynb`（模型实验室第 7 步：Notebook 固化）。"""

from __future__ import annotations

import json
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
WS = HERE.parents[1]
OUT = WS / "notebooks" / "model.ipynb"

md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

cells = [
    md(
        "# 风机效率实时计算（台儿庄）\n"
        "\n"
        "模型 ID `fan-efficiency-taierzhuang`｜目标 `cpu-iot`｜生命周期 `specified`。\n"
        "\n"
        "本 Notebook 回答四件事，并给出可自动复核的断言：\n"
        "\n"
        "1. 两个口径（Excel / 修正）到底差什么、差多少；\n"
        "2. 现场数据可用到什么程度（质量标记与中断段）；\n"
        "3. 双口径偏差随进气温度如何变号；\n"
        "4. 四条验收标准怎么当场断言。\n"
        "\n"
        "> 运行前提：仓库根已有 `data/fan-efficiency-taierzhuang/` 的取数产物"
        "（由 `scripts/取数探针/iot_bulk_fetch.py` 生成）。"
    ),
    code(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "import numpy as np\n"
        "import pandas as pd\n"
        "\n"
        "WS = Path.cwd().resolve()\n"
        "while not (WS / 'src' / 'model.py').exists():\n"
        "    WS = WS.parent\n"
        "sys.path.insert(0, str(WS / 'src'))\n"
        "import model as fan  # noqa: E402\n"
        "\n"
        "REPO = WS.parents[2]\n"
        "DATA = REPO / 'data' / 'fan-efficiency-taierzhuang'\n"
        "\n"
        "print('python', sys.version.split()[0], '| pandas', pd.__version__, '| numpy', np.__version__)\n"
        "print('K =', fan.K, '| P_std =', fan.P_STD_KPA, 'kPa | T_std =', fan.T_STD_K, 'K')\n"
        "print('C18 =', fan.C18, '  (C18*K/1e3 =', round(fan.C18 * fan.K / 1e3, 4), 'kPa)')\n"
        "print('数据目录', DATA, '存在' if DATA.exists() else '缺失')"
    ),
    md(
        "## 1. 契约\n"
        "\n"
        "| 方向 | 字段 | 单位 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 输入 | `inlet_flow` | m³/min | 风机面板显示风量（工况体积流量） |\n"
        "| 输入 | `outlet_pressure` | kPa | 出口表压（即背压 Δp） |\n"
        "| 输入 | `motor_power` | kW | 电机输入功率 |\n"
        "| 输入 | `inlet_temperature` | ℃ | 进气温度 |\n"
        "| 输入 | `elevation` | m | 海拔（唯一可调参数，默认 38.5） |\n"
        "| 输出 | `eta_excel` | 1 | Excel 口径效率（**本期平台生效值**） |\n"
        "| 输出 | `eta_corrected` | 1 | 修正口径效率 |\n"
        "| 输出 | `wp_excel` / `wp_corrected` | kW | 两个口径的气动功率 |\n"
        "| 输出 | `quality` | 枚举 | `VALID` / `STOPPED` / `ROW_INVALID` / `NO_SAMPLE` / `RATIO_INVALID` / `OUT_OF_RANGE` / `DATA_OUTAGE` |"
    ),
    code(
        "ledger = pd.DataFrame(\n"
        "    [\n"
        "        ('一污4#', 46.1, 46.2, 42.2, 25.5),\n"
        "        ('一污5#', 48.6, 46.1, 41.9, 25.8),\n"
        "        ('二污2#', 48.2, 62.1, 61.3, 26.0),\n"
        "    ],\n"
        "    columns=['machine', 'inlet_flow', 'outlet_pressure', 'motor_power', 'inlet_temperature'],\n"
        ")\n"
        "ledger['elevation'] = fan.DEFAULT_ELEVATION_M\n"
        "out = fan.predict(ledger)\n"
        "result = pd.concat([ledger[['machine']], out.set_index(ledger.index)], axis=1)\n"
        "result.round(4)"
    ),
    code(
        "# 验收断言 ②：台账断面（自控信号台账 2026-10-16 的实时读数）\n"
        "expect = {'一污4#': 0.7312, '一污5#': 0.7749, '二污2#': 0.6797}\n"
        "for machine, target in expect.items():\n"
        "    got = float(result.loc[result['machine'] == machine, 'eta_corrected'].iloc[0])\n"
        "    assert abs(got - target) < 5e-4, (machine, got, target)\n"
        "print('✅ 台账断面三条全部在 0.05 个百分点内')\n"
        "print('全部质量标记：', out['quality'].unique())"
    ),
    md(
        "## 2. Excel 口径与修正口径的差别\n"
        "\n"
        "同一个压比项 `Π^K − 1`，两个口径只差**前导项的状态配对**：\n"
        "\n"
        "- Excel：`P标准 × Q标准`（101.325 kPa × 标况流量）——等于把进口状态当成 20 ℃、1 atm；\n"
        "- 修正：`P入口 × Q入口`（当地压力 × 工况流量）。\n"
        "\n"
        "两者的桥是质量守恒 `P入口·Q入口 = P标准·Q标准·(T入口/T标准)`，"
        "所以**偏差只由进气温度决定**，与风量无关。"
    ),
    code(
        "excel_case = pd.DataFrame([\n"
        "    dict(motor_power=72.4, outlet_pressure=47.2, inlet_flow=93.0,\n"
        "         inlet_temperature=20.0, elevation=38.5)\n"
        "])\n"
        "base = fan.predict(excel_case).iloc[0]\n"
        "\n"
        "# 复现 Excel 原表的 24.8 ℃ 读数（Excel 用的是 24.8 ℃ 的工况流量，却按标准状态算）\n"
        "excel_248 = fan.compute_row(93.0, 47.2, 72.4, 24.8, 38.5)\n"
        "\n"
        "print('20 ℃ 时理论偏差 =', round(float((base.eta_excel / base.eta_corrected - 1) * 100), 4), '%')\n"
        "print('24.8 ℃ 时（Excel 原表工况）')\n"
        "print('  Excel 口径  Wp = %.4f kW, eta = %.4f%%' % (excel_248['wp_excel'], excel_248['eta_excel'] * 100))\n"
        "print('  修正口径   Wp = %.4f kW, eta = %.4f%%' % (excel_248['wp_corrected'], excel_248['eta_corrected'] * 100))\n"
        "print('偏差（Excel − 修正）= %.4f%%' % ((excel_248['eta_excel'] / excel_248['eta_corrected'] - 1) * 100))"
    ),
    code(
        "# 验收断言 ①：公式复现\n"
        "assert abs(excel_248['eta_excel'] - 0.861963) < 1e-5\n"
        "assert abs(excel_248['eta_corrected'] - 0.876125) < 1e-5\n"
        "assert abs(excel_248['wp_excel'] - 62.406094) < 1e-4\n"
        "assert abs(excel_248['wp_corrected'] - 63.431445) < 1e-4\n"
        "print('✅ 公式复现：86.20% / 87.61%，62.406 / 63.431 kW')"
    ),
    md(
        "## 3. 数据可用性\n"
        "\n"
        "流水线顺序固定为：**先做行级有效性判定 → 时间加权聚合 → 分钟级质量标记 → 中断升级**。\n"
        "运行时代码（`src/model.py`）与离线统计用的是同一份函数，避免两边口径漂移。"
    ),
    code(
        "minute_path = DATA / 'derived' / 'minute-v2.parquet'\n"
        "if minute_path.exists():\n"
        "    minute = pd.read_parquet(minute_path)\n"
        "    counts = (minute.groupby(['machine', 'quality']).size()\n"
        "              .unstack(fill_value=0))\n"
        "    share = counts.div(counts.sum(axis=1), axis=0)\n"
        "    print('分钟级样本数与质量分布（行=机组）')\n"
        "    display(pd.concat([counts, (share * 100).round(2).add_suffix('_pct')], axis=1))\n"
        "else:\n"
        "    print('缺少 minute-v2.parquet，请先运行 work/analysis/analyze_v2.py')\n"
        "    counts = None"
    ),
    code(
        "if minute_path.exists():\n"
        "    intervals = []\n"
        "    for machine, group in minute.sort_values(['machine', 'minute']).groupby('machine'):\n"
        "        bad = group['quality'].isin([fan.NO_SAMPLE, fan.ROW_INVALID, fan.DATA_OUTAGE]).to_numpy()\n"
        "        t = group['minute'].to_numpy()\n"
        "        start = None\n"
        "        for i, flag in enumerate(np.append(bad, False)):\n"
        "            if flag and start is None:\n"
        "                start = i\n"
        "            elif not flag and start is not None:\n"
        "                hours = (i - start) * 60 / 3600\n"
        "                if hours > 6:\n"
        "                    intervals.append((machine, pd.Timestamp(t[start]), pd.Timestamp(t[i - 1]), round(hours, 1)))\n"
        "                start = None\n"
        "    outages = pd.DataFrame(intervals, columns=['machine', 'start', 'end', 'hours'])\n"
        "    print(outages.to_string(index=False) if len(outages) else '无中断段')"
    ),
    md(
        "## 4. 偏差随温度变号\n"
        "\n"
        "偏差 = `eta_excel / eta_corrected − 1`，等价于 `T标准/T入口 − 1`。\n"
        "20 ℃ 附近为 0，冬天为正（Excel 高估），夏天为负（Excel 低估）。"
    ),
    code(
        "if minute_path.exists():\n"
        "    valid = minute[minute['quality'] == fan.VALID].copy()\n"
        "    valid['dev'] = (valid['eta_excel'] / valid['eta_corrected'] - 1) * 100\n"
        "    edges = [-30, 10, 15, 20, 25, 30, 35, 60]\n"
        "    valid['band'] = pd.cut(valid['inlet_temperature'], edges)\n"
        "    table = valid.groupby('band', observed=True).agg(\n"
        "        样本=('dev', 'size'),\n"
        "        效率修正中位=('eta_corrected', 'median'),\n"
        "        效率Excel中位=('eta_excel', 'median'),\n"
        "        偏差中位=('dev', 'median'),\n"
        "    )\n"
        "    table['效率修正中位'] = (table['效率修正中位'] * 100).round(2)\n"
        "    table['效率Excel中位'] = (table['效率Excel中位'] * 100).round(2)\n"
        "    table['偏差中位'] = table['偏差中位'].round(2)\n"
        "    display(table)\n"
        "else:\n"
        "    print('跳过：缺少 minute-v2.parquet')"
    ),
    code(
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']\n"
        "plt.rcParams['axes.unicode_minus'] = False\n"
        "\n"
        "plots_dir = WS / 'plots'\n"
        "plots_dir.mkdir(exist_ok=True)\n"
        "\n"
        "temps = np.linspace(0, 45, 91)\n"
        "dev = (fan.T_STD_K / (temps + 273.15) - 1) * 100\n"
        "fig, ax = plt.subplots(figsize=(7, 3.6))\n"
        "ax.axhline(0, color='#999', lw=0.8)\n"
        "ax.axvline(20, color='#c0392b', lw=0.8, ls='--')\n"
        "ax.plot(temps, dev, color='#1f4e79', lw=2)\n"
        "ax.fill_between(temps, dev, 0, where=dev > 0, color='#c0392b', alpha=0.12)\n"
        "ax.fill_between(temps, dev, 0, where=dev < 0, color='#2e7d32', alpha=0.12)\n"
        "ax.set_xlabel('进气温度 (℃)')\n"
        "ax.set_ylabel('Excel 偏差 (%)')\n"
        "ax.set_title('双口径偏差只由进气温度决定（正=Excel 高估）')\n"
        "ax.grid(alpha=0.25)\n"
        "fig.tight_layout()\n"
        "fig.savefig(plots_dir / 'eta-deviation-vs-temperature.png', dpi=150)\n"
        "plt.close(fig)\n"
        "print('图已保存 ->', plots_dir / 'eta-deviation-vs-temperature.png')\n"
        "print('5 ℃: %+.2f%% | 20 ℃: %+.2f%% | 30 ℃: %+.2f%% | 40 ℃: %+.2f%%'\n"
        "      % tuple((fan.T_STD_K / (t + 273.15) - 1) * 100 for t in (5, 20, 30, 40)))"
    ),
    md(
        "## 5. 验收断言\n"
        "\n"
        "四条标准全部可自动复核：公式复现、台账断面、差值指纹、缺输入置空。"
    ),
    code(
        "# 验收断言 ③：差值指纹\n"
        "def dev_at(t):\n"
        "    row = fan.compute_row(93.0, 47.2, 72.4, t, 38.5)\n"
        "    return (row['eta_excel'] / row['eta_corrected'] - 1) * 100\n"
        "\n"
        "assert abs(dev_at(20.0)) < 0.01\n"
        "assert abs(dev_at(5.0) - 5.39) < 0.05\n"
        "assert abs(dev_at(30.0) + 3.35) < 0.05\n"
        "print('✅ 差值指纹：20 ℃ = %.4f%%，5 ℃ = %+.2f%%，30 ℃ = %+.2f%%'\n"
        "      % (dev_at(20.0), dev_at(5.0), dev_at(30.0)))\n"
        "\n"
        "# 验收断言 ④：缺输入置空\n"
        "missing = pd.DataFrame([dict(inlet_flow=np.nan, outlet_pressure=47.2, motor_power=72.4,\n"
        "                             inlet_temperature=24.8, elevation=38.5)])\n"
        "row = fan.predict(missing).iloc[0]\n"
        "assert row['quality'] == fan.NO_SAMPLE\n"
        "assert np.isnan(row['eta_excel']) and np.isnan(row['eta_corrected'])\n"
        "print('✅ 缺输入 ->', row['quality'], '，两个口径均置空')"
    ),
    code(
        "# 验收断言 ⑤（补）：偏差与风量无关\n"
        "ratios = []\n"
        "for flow in (40.0, 60.0, 80.0, 100.0):\n"
        "    r = fan.compute_row(flow, 47.2, 72.4 * flow / 93.0, 24.8, 38.5)\n"
        "    ratios.append(r['eta_excel'] / r['eta_corrected'])\n"
        "assert max(ratios) - min(ratios) < 1e-9\n"
        "print('✅ 改风量 40→100 偏差恒为 %.4f%%' % ((ratios[0] - 1) * 100))"
    ),
    md(
        "## 6. 结论\n"
        "\n"
        "1. **两个口径只差一个温度因子**（`T标准/T入口`），代码里是同一份公式的两条前导项，"
        "不是两套算法，因此不会各自漂移。\n"
        "2. **本期平台生效 `eta_excel`**（与 Excel 原表逐字一致），`eta_corrected` 同行输出，"
        "待验收后再切换——切换时不需要改代码，只改配置。\n"
        "3. **数据不可用时宁可输出空值**：质量标记会告诉下游「这一分钟没有可用的效率」，"
        "而不是给一个看似合理的数（例如停机时 V_IF 爆表那一段）。\n"
        "4. 偏差在 20 ℃ 附近为 0、冬季高估、夏季低估，**靠历史对账发现不了**——"
        "这也是本期坚持双口径并行输出的原因。"
    ),
]

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {
    "display_name": "Python 3.11 (model-lab)",
    "language": "python",
    "name": "python3",
}
nb.metadata["language_info"] = {"name": "python", "version": "3.11"}

OUT.parent.mkdir(parents=True, exist_ok=True)

client = NotebookClient(nb, timeout=900, kernel_name="python3",
                        resources={"metadata": {"path": str(WS)}})
client.execute()

OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("已写入", OUT)
print("cells:", len(nb.cells))
