"""Reproduce the Excel 'Merged' targets for 56thAv_so_190thSt_20251003 from raw CSV.

Locks the statistical algorithms before they go into traffic_diag.metrics.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.config import DEFAULT_BASE   # env var -> data_base.txt

STUDY = "56thAv_so_190thSt_20251003"
RAW = os.path.join(DEFAULT_BASE, "2025", STUDY, STUDY + "_Raw.csv")

# Excel 'Merged Set up' targets (cached values).
TARGETS = {
    "total": 1964,
    "avg_speed": 25.134419551934826,
    "median_speed": 25,
    "max_speed": 47,
    "adt": 280.57142857142856,
    "avg_weekday_traffic": 314.2,
    "p85": 31.51734693877551,
    "p90": 33.47777777777778,
    "p80": 29.813333333333333,
    "p50": 25.032608695652176,
    "p10": 19.029577464788733,
}
DAILY_TOTALS = {"Mon": 270, "Tue": 348, "Wed": 319, "Thu": 334, "Fri": 300, "Sat": 181, "Sun": 212}


def pctl_speed_excel(counts_by_speed: dict[int, int], target_pct: float) -> float:
    """Excel interpolation: cumulative %-vs-speed, linear between integer speeds."""
    speeds = np.arange(1, 100)                       # E = 1..99
    cnt = np.array([counts_by_speed.get(int(s), 0) for s in speeds], dtype=float)
    N = cnt.sum()                                    # F134 = total (weekday) vehicles
    # Excel F[speed s] = count of vehicles with speed STRICTLY < s (cum< shift).
    cum_lt = np.concatenate(([0.0], np.cumsum(cnt)[:-1]))
    G = cum_lt / N * 100.0                            # G[s] = P(speed < s) in %
    below = np.where(G <= target_pct)[0]
    i = below[-1]                                    # largest speed index with G[s] <= P
    s = speeds[i]
    if G[i + 1] == G[i]:
        return float(s)
    return float(s + (target_pct - G[i]) / (G[i + 1] - G[i]) * (speeds[i + 1] - s))


def main():
    df = pd.read_csv(RAW, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    df["ts"] = pd.to_datetime(df["Date&Time"], format="%m/%d/%Y %H:%M", errors="coerce")
    df["speed"] = pd.to_numeric(df["Speed"], errors="coerce")

    # 7-day window: Mon 2025-10-06 00:00:00 .. Sun 2025-10-12 23:59:59
    start = pd.Timestamp("2025-10-06 00:00:00")
    end = pd.Timestamp("2025-10-12 23:59:59")
    w = df[(df["ts"] >= start) & (df["ts"] <= end)].copy()

    out = {}
    out["total"] = len(w)
    out["avg_speed"] = w["speed"].mean()
    out["median_speed"] = w["speed"].median()
    out["max_speed"] = w["speed"].max()
    out["adt"] = out["total"] / 7

    w["date"] = w["ts"].dt.normalize()
    w["dow"] = w["ts"].dt.dayofweek                  # Mon=0..Sun=6
    daily = w.groupby("date").size()
    by_dow = w.groupby("dow").size()                 # one full week => index per day
    weekday_daily = [by_dow.get(d, 0) for d in range(5)]   # Mon..Fri
    out["avg_weekday_traffic"] = float(np.mean(weekday_daily))

    # Percentile / speed distribution uses WEEKDAY (Mon-Fri) vehicles only (N=1571).
    weekday = w[w["dow"] <= 4]
    counts_by_speed = weekday["speed"].round().astype(int).value_counts().to_dict()
    print(f"(weekday N for percentile table = {len(weekday)})")
    for p, key in [(90, "p90"), (85, "p85"), (80, "p80"), (50, "p50"), (10, "p10")]:
        out[key] = pctl_speed_excel(counts_by_speed, p)

    print(f"{'metric':22} {'python':>22} {'excel':>22} {'match':>8}")
    for k, target in TARGETS.items():
        got = out[k]
        ok = abs(float(got) - float(target)) < 1e-6
        print(f"{k:22} {float(got):22.10f} {float(target):22.10f} {'OK' if ok else 'DIFF':>8}")

    print("\nDaily totals (python vs excel):")
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for d, name in enumerate(names):
        got = int(by_dow.get(d, 0))
        exp = DAILY_TOTALS[name]
        print(f"  {name}: {got:4d} vs {exp:4d}  {'OK' if got == exp else 'DIFF'}")

    # Directional split
    print("\nDirection split:")
    print(w["Direction"].value_counts().to_string())
    print("\nClass split:")
    print(w["Class"].value_counts().to_string())


if __name__ == "__main__":
    main()
