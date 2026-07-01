"""Standalone CLI for traffic study report generation, validation, and diagnostics.

Examples
--------
  # list available years / locations
  python scripts/generate_report.py --list
  python scripts/generate_report.py --year 2025 --list

  # one location (substring match), HTML + Excel report
  python scripts/generate_report.py --year 2025 --location 56thAv_so_190thSt

  # every study for a year
  python scripts/generate_report.py --year 2025 --all

  # validate Python output against the legacy Excel reports
  python scripts/generate_report.py --year 2025 --validate
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running as a plain script (no install needed).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.config import DEFAULT_BASE  # noqa: E402  (defined below if missing)
from traffic_diag.discovery import find_studies, find_years  # noqa: E402
from traffic_diag.pipeline import process_study  # noqa: E402
from traffic_diag.report import (write_excel_report, write_html_report,  # noqa: E402
                                  write_pdf_report)

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")


def _select(base, year, location):
    studies = find_studies(base, year=year)
    if location:
        loc = location.lower()
        studies = [s for s in studies if loc in s.location.lower() or loc in s.study_id.lower()]
    return studies


def _emit(result, outdir, fmt):
    sid = result.study.study_id
    sub = os.path.join(outdir, sid)
    os.makedirs(sub, exist_ok=True)
    made = []
    if fmt in ("html", "both", "all"):
        made.append(write_html_report(result, os.path.join(sub, f"{sid}_report.html")))
    if fmt in ("excel", "both", "all"):
        made.append(write_excel_report(result, os.path.join(sub, f"{sid}_report.xlsx")))
    if fmt in ("pdf", "all"):
        made.append(write_pdf_report(result, os.path.join(sub, f"{sid}_report.pdf")))
    return made


def main(argv=None):
    p = argparse.ArgumentParser(description="Traffic study report generation & diagnostics.")
    p.add_argument("--base", default=DEFAULT_BASE, help="root Speed and Volume Studies folder")
    p.add_argument("--year", type=int, help="restrict to one year")
    p.add_argument("--location", help="location substring (omit with --all)")
    p.add_argument("--all", action="store_true", help="process every matching study")
    p.add_argument("--list", action="store_true", help="list years/locations and exit")
    p.add_argument("--validate", action="store_true", help="compare against the Excel reports")
    p.add_argument("--out", default=DEFAULT_OUT, help="output directory for reports")
    p.add_argument("--format", choices=["html", "excel", "pdf", "both", "all"], default="both")
    p.add_argument("--speed-limit", type=float, default=None,
                   help="override the posted speed limit (mph). If omitted, resolved per "
                        "study: Notes 'Limit:' line -> existing Excel report -> default 25.")
    p.add_argument("--include-compromised", action="store_true",
                   help="include _Compromised Studies folders")
    args = p.parse_args(argv)

    if not os.path.isdir(args.base):
        p.error(f"base folder not found: {args.base}")

    if args.list:
        if args.year:
            for s in find_studies(args.base, year=args.year):
                flag = f" [{s.status}]" if s.status != "normal" else ""
                print(f"  {s.location}  ({s.install_date}){flag}")
        else:
            print("Years:", ", ".join(map(str, find_years(args.base))))
        return 0

    if args.validate:
        from traffic_diag.validate import validate_many
        studies = _select(args.base, args.year, args.location)
        df = validate_many(studies)
        comp = df[df.metric != "ERROR"]
        if comp.empty:
            print("No comparable studies (no Excel reports found).")
            return 0
        print(f"Validated {comp['study'].nunique()} studies; "
              f"{comp.match.mean()*100:.2f}% of {len(comp)} comparisons match.")
        bad = comp[~comp.match]
        if len(bad):
            print("\nMismatches:")
            print(bad[["study", "direction", "metric", "python", "excel", "abs_diff"]]
                  .to_string(index=False))
        return 0

    studies = _select(args.base, args.year, args.location)
    if not args.all:
        if not args.location:
            p.error("specify --location, or pass --all")
        studies = studies[:1] if studies else []
    if not studies:
        print("No matching studies.")
        return 1

    for s in studies:
        try:
            result = process_study(s, speed_limit=args.speed_limit)
            made = _emit(result, args.out, args.format)
            d = result.diagnostics
            print(f"[OK] {s.study_id}: total={result.merged.total} "
                  f"85th={result.merged.design_speed:.1f} risk={d.risk if d else '-'} "
                  f"-> {', '.join(os.path.relpath(m) for m in made)}")
        except Exception as e:
            print(f"[ERR] {s.study_id}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
