"""End-to-end smoke test of the backbone against verified Excel targets."""
import sys

import pandas as pd

sys.path.insert(0, r"C:\Users\moshanreh\Desktop\Mohammad\Claude\Intersection")

from traffic_diag.discovery import find_studies, find_years
from traffic_diag.study import load_study
from traffic_diag.metrics import compute_direction_metrics
from traffic_diag.config import DIRECTION

BASE = r"C:\Users\moshanreh\Desktop\Mohammad\Speed and Volume Studies"
TARGETS = {
    "total_vehicles": 1964, "average_speed": 25.134419551934826,
    "median_speed": 25.0, "max_speed": 47.0, "adt": 280.57142857142856,
    "average_weekday_traffic": 314.2, "p85_speed": 31.51734693877551,
}

print("Years found:", find_years(BASE))
studies = find_studies(BASE, year=2025)
print(f"2025 studies: {len(studies)} (compromised: {sum(s.is_compromised for s in studies)})")

target = next(s for s in studies if s.location == "56thAv_so_190thSt")
print(f"\nTarget study: {target.study_id} status={target.status} date={target.install_date}")

# Force the Excel window for an exact comparison.
sd = load_study(target, speed_limit=25,
                window=("2025-10-06 00:00:00", "2025-10-12 23:59:59"))
print(f"Notes: incoming={sd.notes['incoming']} outgoing={sd.notes['outgoing']} "
      f"source={sd.notes['source']}")
print(f"Window: {sd.window_start} .. {sd.window_end}  (raw rows={len(sd.raw)}, window rows={len(sd.window)})")

m = compute_direction_metrics(sd.window, "Merged", sd.speed_limit)
summ = m.summary()
print(f"\n{'metric':24}{'python':>20}{'excel':>20}{'match':>8}")
for k, exp in TARGETS.items():
    got = summ.get(k)
    ok = got is not None and abs(float(got) - float(exp)) < 1e-6
    print(f"{k:24}{float(got):20.10f}{float(exp):20.10f}{'OK' if ok else 'DIFF':>8}")

print("\nDaily totals:", m.daily_totals)
print("AM peak:", m.am_peak, "| PM peak:", m.pm_peak)
print("Class:", m.class_counts, "| pct:", {k: round(v, 1) for k, v in m.class_pct.items()})
print("Direction:", m.direction_counts)
print("Pace:", m.pace)
print("85th pctl table:", [(p, round(s, 2) if s else None) for p, s, _ in m.pct_table])

# Per-direction
for d in ("Incoming", "Outgoing"):
    sub = sd.window[sd.window[DIRECTION] == d]
    md = compute_direction_metrics(sub, d, sd.speed_limit)
    print(f"\n{d}: total={md.total} adt={md.adt:.1f} p85={md.design_speed} "
          f"avg={md.avg_speed:.2f}")

# Auto window selection check
sd_auto = load_study(target, speed_limit=25)
print(f"\nAuto-selected window: {sd_auto.window_start} .. {sd_auto.window_end}")
print("Selection note:", sd_auto.selection_note or "(clean Mon-Sun week)")
