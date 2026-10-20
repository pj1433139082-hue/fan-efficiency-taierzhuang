# -*- coding: utf-8 -*-
"""演示一：给 5 个读数，算两个口径的风机效率。不需要任何数据文件。"""
import sys
import model as fan

# 台儿庄一污 4# 的实测断面（2026-10 台账）：功率/风量/出口压力/进口温度
CASES = [
    ("模板示例（Excel 里的那组）", 72.4, 93.0, 47.2, 24.8),
    ("一污 4# 实测",              42.2, 46.1, 46.2, 25.5),
    ("一污 5# 实测",              41.9, 48.6, 46.1, 25.8),
    ("二污 2# 实测",              61.3, 48.2, 62.1, 26.0),
]
ELEV = 38.5   # 竣工图设计海拔，m

print("=" * 78)
print("风机效率：Excel 口径 vs 修正口径")
print("=" * 78)
print("输入：电机功率 kW / 进口风量 m3/min / 出口压力 kPa / 进口温度 ℃ / 海拔 m")
print()
head = f"{'工况':<22}{'Excel效率':>10}{'修正效率':>10}{'Excel气动kW':>13}{'修正气动kW':>13}{'偏差':>8}"
print(head)
print("-" * 78)
for name, pin, q, dp, t in CASES:
    r = fan.compute_row(q, dp, pin, t, ELEV)
    dev = (r["eta_excel"] / r["eta_corrected"] - 1) * 100
    print(f"{name:<22}{r['eta_excel']*100:>9.2f}%{r['eta_corrected']*100:>9.2f}%"
          f"{r['wp_excel']:>13.3f}{r['wp_corrected']:>13.3f}{dev:>7.2f}%")

print()
print("说明：Excel 口径把进口状态当成 1 个标准大气压、20 ℃；修正口径用真实进口状态。")
print("两口径共用压比项 (P出口/P进口)^0.2857，差别只在前导项 P×Q 用的是哪个状态。")
print("模板示例那组：Excel 口径应得 62.406 kW / 86.20%，修正口径 63.431 kW / 87.61%。")

# 单点调用示例（下游接入用的就是这个函数）
if len(sys.argv) == 5:
    pin, q, dp, t = (float(x) for x in sys.argv[1:])
    r = fan.compute_row(q, dp, pin, t, ELEV)
    print()
    print("你的输入:", dict(motor_power_kw=pin, inlet_flow_m3min=q, dp_kpa=dp, temp_c=t))
    print("Excel 口径效率 %.4f%%，修正口径效率 %.4f%%" % (r["eta_excel"]*100, r["eta_corrected"]*100))
    print("参数格式：python demo_1_single.py 功率kW 风量m3min 出口压力kPa 进口温度C")
