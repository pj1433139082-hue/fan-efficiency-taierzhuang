"""风机效率模型的行为断言：四条验收标准逐条落地。

对应 model-spec.md#可验证的验收标准：
1. 公式复现（Excel 示例断面）
2. 台账断面（台儿庄实时读数）
3. 差值指纹（温度敏感性：20 ℃ 为零点、低温高估、高温低估）
4. 缺输入与边界置空（质量标记）

仓库测试栈是 ``unittest``（pyproject 的 test extra 不含 pytest），因此本文件不依赖 pytest。
运行：``python -m unittest discover -s tests -t .``
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import model as m  # noqa: E402

# 模板示例断面：P1=72.4 kW，Δp=47.2 kPa，H=38.5 m，Q入口=93 m³/min，t入口=24.8 ℃
EXAMPLE = {
    "inlet_flow": 93.0,
    "outlet_pressure": 47.2,
    "motor_power": 72.4,
    "inlet_temperature": 24.8,
    "elevation": 38.5,
}


def frame(**overrides) -> pd.DataFrame:
    row = dict(EXAMPLE)
    row.update(overrides)
    return pd.DataFrame([row])


def samples(count: int = 4, dt: float = 15.0, **overrides) -> list[dict]:
    base = dict(EXAMPLE)
    base["run_flag"] = 1
    base["dt_s"] = dt
    base.update(overrides)
    return [dict(base) for _ in range(count)]


class FormulaReproductionTests(unittest.TestCase):
    """验收 1：公式复现。"""

    def test_entry_pressure_matches_template(self) -> None:
        self.assertAlmostEqual(m.entry_pressure_kpa(38.5), 100.898291666667, places=9)

    def test_c18_is_standard_pressure_over_k(self) -> None:
        # C18 = P0'/K，P0' ≈ 101319.4 Pa。比 101.325 kPa 低 0.0055% —— 这是模板右表 ρ
        # 只取三位小数造成的固有偏差，也是 Excel 口径在 20 ℃ 时仍差 −0.006% 的来源。
        self.assertAlmostEqual(m.C18 * m.K, 101319.37, delta=0.5)
        bias = m.C18 * m.K / 1000.0 / m.P_STD_KPA - 1.0
        self.assertAlmostEqual(bias, -5.6e-5, delta=1e-6)

    def test_standard_flow_matches_template(self) -> None:
        self.assertAlmostEqual(m.standard_flow(93.0, 24.8, 38.5), 91.1164221503822, places=9)

    def test_excel_caliber_reproduces_template(self) -> None:
        """Excel 口径逐字复现：62.406 kW / 86.20%。"""
        q_std = m.standard_flow(EXAMPLE["inlet_flow"], EXAMPLE["inlet_temperature"],
                                EXAMPLE["elevation"])
        wp = m.excel_power(q_std, EXAMPLE["outlet_pressure"], EXAMPLE["elevation"])
        self.assertAlmostEqual(wp, 62.40609, places=4)
        self.assertAlmostEqual(wp / EXAMPLE["motor_power"], 0.8619626, places=6)

    def test_corrected_caliber_value(self) -> None:
        """修正口径：63.431 kW / 87.61%。"""
        wp = m.corrected_power(EXAMPLE["inlet_flow"], EXAMPLE["outlet_pressure"],
                               EXAMPLE["elevation"])
        self.assertAlmostEqual(wp, 63.43145, places=4)
        self.assertAlmostEqual(wp / EXAMPLE["motor_power"], 0.8761249, places=6)

    def test_predict_returns_both_calibers(self) -> None:
        out = m.predict(frame())
        self.assertEqual(out.loc[0, "quality"], m.VALID)
        self.assertAlmostEqual(out.loc[0, "wp_excel"], 62.40609, places=4)
        self.assertAlmostEqual(out.loc[0, "wp_corrected"], 63.43145, places=4)
        self.assertLess(out.loc[0, "eta_excel"], out.loc[0, "eta_corrected"])

    def test_standard_and_actual_power_agree_at_reference_temperature(self) -> None:
        """20 ℃ 时两个口径几乎一致：仅剩 C18 取整带来的 −0.0055% 固有偏差。"""
        q_std = m.standard_flow(93.0, 20.0, 38.5)
        wp_std = m.excel_power(q_std, 47.2, 38.5)
        wp_act = m.corrected_power(93.0, 47.2, 38.5)
        self.assertAlmostEqual(wp_std / wp_act, 1.0, delta=1e-4)


class LedgerSnapshotTests(unittest.TestCase):
    """验收 2：台账断面（2026-10-16 实时读数）。"""

    CASES = [
        ("一污4#", 46.1, 46.2, 42.2, 25.5, 0.7312),
        ("一污5#", 48.6, 46.1, 41.9, 25.8, 0.7749),
        ("二污2#", 48.2, 62.1, 61.3, 26.0, 0.6797),
    ]

    def test_ledger_snapshot(self) -> None:
        for name, flow, dp, power, temperature, expected in self.CASES:
            with self.subTest(machine=name):
                out = m.predict(pd.DataFrame([{
                    "inlet_flow": flow, "outlet_pressure": dp, "motor_power": power,
                    "inlet_temperature": temperature, "elevation": m.DEFAULT_ELEVATION_M,
                }]))
                self.assertEqual(out.loc[0, "quality"], m.VALID)
                self.assertAlmostEqual(out.loc[0, "eta_corrected"], expected, delta=2e-4)


class DeviationFingerprintTests(unittest.TestCase):
    """验收 3：差值指纹。"""

    def test_deviation_is_zero_at_reference_temperature(self) -> None:
        """20 ℃ 是零点：只剩 C18 取整的 −0.0055%，远小于任何业务可辨差异。"""
        out = m.predict(frame(inlet_temperature=20.0))
        ratio = out.loc[0, "eta_excel"] / out.loc[0, "eta_corrected"]
        self.assertAlmostEqual(ratio, 1.0, delta=1e-4)
        self.assertLess(abs(ratio - 1.0), 1e-4)

    def test_deviation_sign_and_magnitude(self) -> None:
        """低温高估、高温低估；5 ℃ ≈ +5.4%，30 ℃ ≈ −3.3%。"""
        cold = m.predict(frame(inlet_temperature=5.0)).loc[0]
        hot = m.predict(frame(inlet_temperature=30.0)).loc[0]
        deviation_cold = cold["eta_excel"] / cold["eta_corrected"] - 1.0
        deviation_hot = hot["eta_excel"] / hot["eta_corrected"] - 1.0
        self.assertGreater(deviation_cold, 0.0)
        self.assertLess(deviation_hot, 0.0)
        self.assertAlmostEqual(deviation_cold, 0.054, delta=5e-3)
        self.assertAlmostEqual(deviation_hot, -0.033, delta=5e-3)

    def test_deviation_independent_of_flow(self) -> None:
        """偏差只由温度决定，改风量不应改变它。"""
        ratios = []
        for flow in (40.0, 60.0, 80.0, 100.0):
            row = m.predict(frame(inlet_flow=flow)).loc[0]
            self.assertEqual(row["quality"], m.VALID)
            ratios.append(row["eta_excel"] / row["eta_corrected"])
        self.assertLess(max(ratios) - min(ratios), 1e-9)
        self.assertAlmostEqual(ratios[0], 1 / 1.0164303, places=6)


class BoundaryAndQualityTests(unittest.TestCase):
    """验收 4：缺输入与边界置空。"""

    def test_missing_input_returns_no_sample(self) -> None:
        out = m.predict(frame(motor_power=float("nan")))
        self.assertEqual(out.loc[0, "quality"], m.NO_SAMPLE)
        self.assertTrue(math.isnan(out.loc[0, "eta_corrected"]))

    def test_out_of_range_row_is_invalid(self) -> None:
        self.assertEqual(m.classify_row(300.0, 47.2, 72.4, 24.8, 38.5), m.ROW_INVALID)
        self.assertEqual(m.classify_row(93.0, 47.2, 72.4, 120.0, 38.5), m.ROW_INVALID)
        self.assertEqual(m.classify_row(-1.0, 47.2, 72.4, 24.8, 38.5), m.ROW_INVALID)

    def test_stopped_machine_returns_stopped(self) -> None:
        out = m.predict(frame(motor_power=0.0))
        self.assertEqual(out.loc[0, "quality"], m.STOPPED)
        self.assertTrue(math.isnan(out.loc[0, "eta_excel"]))

    def test_compute_row_with_zero_power_does_not_crash(self) -> None:
        """直接调用 compute_row（不经 predict 的短路）时，停机读数不能让分母变 0。

        实测停机分钟占二污 64%；这一条与上一条测的是两条不同路径，
        原测试集只覆盖了 predict 路径，因而漏掉了 compute_row 直调时的 ZeroDivisionError。
        """
        row = m.compute_row(0.0, 0.1, 0.0, 26.5, m.DEFAULT_ELEVATION_M)
        self.assertEqual(row["quality"], m.STOPPED)
        self.assertTrue(math.isnan(row["eta_excel"]))
        self.assertTrue(math.isnan(row["eta_corrected"]))
        self.assertEqual(row["wp_excel"], 0.0)

    def test_non_positive_backpressure_is_ratio_invalid(self) -> None:
        self.assertEqual(m.classify_row(93.0, 0.0, 72.4, 24.8, 38.5), m.RATIO_INVALID)

    def test_absurd_efficiency_is_out_of_range(self) -> None:
        # 功率远小于气动功率 → η 明显大于 1，必须被拦下而不是输出
        out = m.predict(frame(motor_power=6.0))
        self.assertEqual(out.loc[0, "quality"], m.OUT_OF_RANGE)
        self.assertTrue(math.isnan(out.loc[0, "eta_corrected"]))

    def test_default_elevation_used_when_column_missing(self) -> None:
        row = {k: v for k, v in EXAMPLE.items() if k != "elevation"}
        out = m.predict(pd.DataFrame([row]))
        self.assertEqual(out.loc[0, "quality"], m.VALID)
        self.assertAlmostEqual(out.loc[0, "eta_corrected"], 0.8761249, places=6)


class AggregationAndOutageTests(unittest.TestCase):
    """验收 4 的聚合分支：时间加权、覆盖判据、停机双条件、中断升级。"""

    def test_time_weighted_mean(self) -> None:
        group = samples(2)
        group[0]["inlet_flow"] = 40.0
        group[0]["dt_s"] = 45.0
        group[1]["inlet_flow"] = 100.0
        group[1]["dt_s"] = 15.0
        row = m.aggregate_minute(group)
        self.assertEqual(row["quality"], m.VALID)
        self.assertAlmostEqual(row["inlet_flow"], (40 * 45 + 100 * 15) / 60.0)

    def test_needs_enough_samples(self) -> None:
        row = m.aggregate_minute(samples(1))
        self.assertEqual(row["quality"], m.NO_SAMPLE)
        self.assertIsNone(row["inlet_flow"])

    def test_requires_coverage(self) -> None:
        row = m.aggregate_minute(samples(3, dt=5.0))  # 3 点但只覆盖 15 s
        self.assertEqual(row["quality"], m.NO_SAMPLE)

    def test_stop_needs_both_conditions(self) -> None:
        self.assertEqual(m.aggregate_minute(samples(4, motor_power=1.0))["quality"], m.STOPPED)
        self.assertEqual(m.aggregate_minute(samples(4, run_flag=0))["quality"], m.STOPPED)

    def test_mark_outages_promotes_long_gaps(self) -> None:
        flags = ([m.VALID] * 10 + [m.NO_SAMPLE] * 480 + [m.VALID] * 10
                 + [m.NO_SAMPLE] * 5 + [m.VALID] * 5)
        marked = m.mark_outages(flags, step_seconds=60.0)
        self.assertEqual(marked[10], m.DATA_OUTAGE)
        self.assertEqual(marked[489], m.DATA_OUTAGE)
        self.assertEqual(marked[500], m.NO_SAMPLE)
        self.assertEqual(marked[0], m.VALID)


def test_fan_efficiency_taierzhuang_behavior() -> None:
    """模型级冒烟：模板示例输入下双口径结果与「偏差只由温度决定」的指纹成立。

    放在模块级（而非 TestCase 方法）是为了让 `model_lab` 的 python-ready 门禁能
    在 AST 里看到 `test_*` 函数；它同样被 `__main__` 真正执行。
    """
    row = m.predict(frame()).loc[0]
    assert row["quality"] == m.VALID
    assert abs(row["wp_excel"] - 62.4061) < 5e-4
    assert abs(row["wp_corrected"] - 63.4314) < 5e-4
    assert abs(row["eta_excel"] - 0.861963) < 5e-6
    assert abs(row["eta_corrected"] - 0.876125) < 5e-6
    ratio = row["eta_corrected"] / row["eta_excel"]
    assert 1.0163 < ratio < 1.0165, ratio

    cold = m.predict(frame(inlet_temperature=5.0)).loc[0]
    warm = m.predict(frame(inlet_temperature=30.0)).loc[0]
    assert cold["eta_excel"] > cold["eta_corrected"]          # 冬天 Excel 高估
    assert warm["eta_excel"] < warm["eta_corrected"]          # 夏天 Excel 低估


if __name__ == "__main__":
    test_fan_efficiency_taierzhuang_behavior()
    unittest.main()
