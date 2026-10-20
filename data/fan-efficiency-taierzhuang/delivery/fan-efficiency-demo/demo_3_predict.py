# -*- coding: utf-8 -*-
"""演示三：接入方式示例——把一批读数喂给契约入口 predict()。

需要 pandas（pip install pandas）。这三个演示里只有这一个需要它。
"""
try:
    import pandas as pd
except ImportError:
    raise SystemExit("这个演示需要 pandas：pip install pandas")

import model as fan

frame = pd.DataFrame({
    "inlet_flow":        [93.0, 46.1, 48.6, 48.2],
    "outlet_pressure":   [47.2, 46.2, 46.1, 62.1],
    "motor_power":       [72.4, 42.2, 41.9, 61.3],
    "inlet_temperature": [24.8, 25.5, 25.8, 26.0],
    "elevation":         [38.5, 38.5, 38.5, 38.5],
})
out = fan.predict(frame)
print(pd.concat([frame, out], axis=1).to_string(index=False, float_format=lambda v: f"{v:.4f}"))
