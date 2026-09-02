"""Confirm the pipeline runs with only CSV + TXT (no _Report.xlsx).

Migration check: Python must generate the report from raw data alone; the Excel
is just an optional cross-check, not a dependency.
"""
import os
import shutil
import sys
import tempfile
import warnings
from datetime import date

warnings.simplefilter("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.discovery import Study, find_studies
from traffic_diag.pipeline import process_study
from traffic_diag.report import write_excel_report, write_html_report, write_pdf_report

from traffic_diag.config import DEFAULT_BASE as BASE   # env var -> data_base.txt

# Take a real study, copy ONLY its raw CSV + notes TXT into a clean folder.
src = [s for s in find_studies(BASE, year=2025) if s.location == "56thAv_so_190thSt"][0]
import glob
raw = glob.glob(os.path.join(src.path, "*_Raw.csv"))[0]
notes = glob.glob(os.path.join(src.path, "*_Notes.txt"))[0]

tmp = tempfile.mkdtemp(prefix="noexcel_")
folder = os.path.join(tmp, "56thAv_so_190thSt_20251003")
os.makedirs(folder)
shutil.copy(raw, folder)
shutil.copy(notes, folder)
print("Folder contents (no xlsx):", sorted(os.listdir(folder)))

study = Study(path=folder, year=2025, location="56thAv_so_190thSt",
              install_date=date(2025, 10, 3))
print("report_xlsx present? ->", study.report_xlsx)   # expect None

result = process_study(study)
m = result.merged
sd = result.data
print(f"\nRan OK.  total={m.total}  85th={m.design_speed:.2f}  avg={m.avg_speed:.2f}")
print(f"speed_limit={sd.speed_limit:g}  source={sd.speed_limit_source}  (expect 'notes')")
print(f"diagnostics risk={result.diagnostics.risk}")

out = os.path.join(tmp, "out")
h = write_html_report(result, os.path.join(out, "r.html"))
x = write_excel_report(result, os.path.join(out, "r.xlsx"))
p = write_pdf_report(result, os.path.join(out, "r.pdf"))
for f in (h, x, p):
    print(f"  wrote {os.path.basename(f)}: {os.path.getsize(f):,} bytes")
print("\nNO-EXCEL PIPELINE: OK" if all(os.path.getsize(f) > 0 for f in (h, x, p)) else "FAIL")
