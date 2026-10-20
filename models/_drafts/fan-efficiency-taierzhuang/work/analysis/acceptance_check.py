import sys
sys.path.insert(0, "src")
import model as m


def show(tag, Q, dp, P, T):
    r = m.compute_row(flow_actual=Q, dp_kpa=dp, motor_power=P, temperature_c=T,
                      elevation_m=m.DEFAULT_ELEVATION_M)
    ex, wx = r["eta_excel"], r["wp_excel"]
    ec, wc = r["eta_corrected"], r["wp_corrected"]
    print("%-10s eta_x=%8.4f%%  wp_x=%10.6f  eta_c=%8.4f%%  wp_c=%10.6f  q=%s"
          % (tag, ex * 100, wx, ec * 100, wc, r["quality"]))


print("--- A. Excel 模板示例输入 ---")
show("模板示例", 93, 47.2, 72.4, 24.8)

print("--- B. 台账 102 行断面 ---")
show("一污4#", 46.1, 46.2, 42.2, 25.5)
show("一污5#", 48.6, 46.1, 41.9, 25.8)
show("二污2#", 48.2, 62.1, 61.3, 26.0)

print("--- C. 差值指纹 ---")
for t in (5, 20, 24.8, 30):
    r = m.compute_row(flow_actual=93, dp_kpa=47.2, motor_power=72.4, temperature_c=t,
                      elevation_m=m.DEFAULT_ELEVATION_M)
    d = r["eta_excel"] / r["eta_corrected"] - 1
    print("  T=%5.1fC  eta_x=%8.4f%%  eta_c=%8.4f%%  偏差=%+.4f%%"
          % (t, r["eta_excel"] * 100, r["eta_corrected"] * 100, d * 100))

print("--- D. 缺输入置空 ---")
print(m.compute_row(flow_actual=float("nan"), dp_kpa=47.2, motor_power=72.4, temperature_c=24.8,
                    elevation_m=m.DEFAULT_ELEVATION_M))

print("--- E. 停机判定 ---")
show("停机", 0.0, 0.1, 0.0, 26.5)
