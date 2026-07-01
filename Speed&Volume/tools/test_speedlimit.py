"""Test the speed-limit resolution precedence: input -> Excel -> Notes -> 25."""
import os
import sys
import tempfile
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, r"C:\Users\moshanreh\Desktop\Mohammad\Claude\Intersection")

from traffic_diag.discovery import _LIMIT_RE, Study, find_studies
from traffic_diag.study import resolve_speed_limit

BASE = r"C:\Users\moshanreh\Desktop\Mohammad\Speed and Volume Studies"

print("Regex (structured 'Limit:' matches; prose does NOT):")
cases = {
    "Limit: 30": 30, "Speed Limit: 25": 25, "limit:40": 40,
    "construction speed limit 25 mph": None,
    "speed limit sign from 35 MPH to 30 MPH": None,
    "mounting opportunities limits options": None,
}
for text, expected in cases.items():
    m = _LIMIT_RE.search(text)
    got = int(m.group(1)) if m else None
    flag = "OK" if got == expected else "DIFF"
    print("  %-4s %-5s  <- %r" % (flag, got, text))

print("\nResolution:")
s = [x for x in find_studies(BASE, year=2025) if x.location == "56thAv_so_190thSt"][0]
print("  56thAv (has Excel)   :", resolve_speed_limit(s))
print("  56thAv explicit=30   :", resolve_speed_limit(s, explicit=30))

d = tempfile.mkdtemp()
with open(os.path.join(d, "X_Notes.txt"), "w") as fh:
    fh.write("Incoming: NB\nLimit: 30\n")
print("  notes-only 'Limit: 30':", resolve_speed_limit(Study(path=d, year=2025, location="X", install_date=None)))

d2 = tempfile.mkdtemp()
print("  nothing -> default   :", resolve_speed_limit(Study(path=d2, year=2025, location="Y", install_date=None)))
