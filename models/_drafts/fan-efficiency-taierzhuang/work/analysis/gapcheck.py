import glob
from pathlib import Path

import pandas as pd

rows = []
ROOT = Path(__file__).resolve().parents[4]
for f in sorted(glob.glob(str(ROOT / "data" / "fan-efficiency-taierzhuang" / "raw" / "*.parquet"))):
    df = pd.read_parquet(f, columns=["data_time_bj"])
    t = pd.to_datetime(df["data_time_bj"]).sort_values()
    d = t.diff().dt.total_seconds().dropna()
    gaps = int((d > 600).sum())
    mx = round(float(d.max()) / 60, 1) if len(d) else 0.0
    rows.append((Path(f).stem, len(df), gaps, mx, d.median()))

print(f"{'月份':<26}{'行数':>9}{'空缝>10min':>11}{'最大空缝min':>12}{'中位间隔s':>10}")
tot = bad = 0
for n, c, g, mx, med in rows:
    tot += c
    bad += 1 if g else 0
    flag = "  <== 有缺口" if g else ""
    print(f"{n:<26}{c:>9}{g:>11}{mx:>12}{med:>10.1f}{flag}")
print(f"\n合计 {len(rows)} 个月 {tot} 行；{bad} 个月存在 >10min 空缝")
