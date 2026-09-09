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

from traffic_diag.config import (DEFAULT_BASE, NO_DATA_BASE_MSG,  # noqa: E402
                                 REPORTS_DIR)
from traffic_diag.discovery import find_studies, find_years  # noqa: E402
from traffic_diag.pipeline import process_study  # noqa: E402
from traffic_diag.report import (write_excel_report, write_html_report,  # noqa: E402
                                  write_pdf_report)

DEFAULT_OUT = REPORTS_DIR


def _select(base, year, location):
    studies = find_studies(base, year=year)
    if location:
        loc = location.lower()
        studies = [s for s in studies if loc in s.location.lower() or loc in s.study_id.lower()]
    return studies


# --format spellings -> the set of formats they select.
FORMATS = {
    "html": {"html"},
    "excel": {"excel"},
    "pdf": {"pdf"},
    "both": {"html", "excel"},
    "excel+pdf": {"excel", "pdf"},
    "all": {"html", "excel", "pdf"},
}

WRITERS = {
    "html": (write_html_report, "html"),
    "excel": (write_excel_report, "xlsx"),
    "pdf": (write_pdf_report, "pdf"),
}

# Suffix for --in-place output. It must NOT end in "_Report", because:
#   * the study folders already hold a legacy <study>_Report.xlsx / .pdf, and
#     Windows filenames are case-insensitive - writing "<study>_report.xlsx"
#     next to "<study>_Report.xlsx" does not create a second file, it destroys
#     the first one;
#   * catalog.FINGERPRINT_GLOBS and Study.report_xlsx both glob "*_Report.xlsx",
#     so a file ending that way would be mistaken for the legacy workbook (used
#     to resolve the posted speed limit) and would change every study's
#     fingerprint, forcing a full catalog recompute.
DEFAULT_IN_PLACE_SUFFIX = "Analysis"


def _collides_with_legacy(stem: str) -> bool:
    """True if this stem would be seen as the legacy '*_Report' file."""
    return stem.lower().endswith("_report")


def _emit(result, outdir, formats, in_place=False,
          suffix=DEFAULT_IN_PLACE_SUFFIX, overwrite=False):
    """Write the requested formats; return (written, skipped).

    Default: <outdir>/<study_id>/<study_id>_report.<ext>.
    --in-place: into the study's own folder as <study_id>_<suffix>.<ext>, and
    never over an existing file unless ``overwrite``.
    """
    sid = result.study.study_id
    if in_place:
        target, stem = result.study.path, f"{sid}_{suffix}"
    else:
        target, stem = os.path.join(outdir, sid), f"{sid}_report"
        os.makedirs(target, exist_ok=True)

    written, skipped = [], []
    for name in ("html", "excel", "pdf"):
        if name not in formats:
            continue
        writer, ext = WRITERS[name]
        path = os.path.join(target, f"{stem}.{ext}")
        # os.path.exists is case-insensitive on Windows, which is exactly the
        # check needed here - see the note on DEFAULT_IN_PLACE_SUFFIX.
        if in_place and not overwrite and os.path.exists(path):
            skipped.append(path)
            continue
        written.append(writer(result, path))
    return written, skipped


def main(argv=None):
    p = argparse.ArgumentParser(description="Traffic study report generation & diagnostics.")
    p.add_argument("--base", default=DEFAULT_BASE, help="root Speed and Volume Studies folder")
    p.add_argument("--year", type=int, help="restrict to one year")
    p.add_argument("--location", help="location substring (omit with --all)")
    p.add_argument("--all", action="store_true", help="process every matching study")
    p.add_argument("--list", action="store_true", help="list years/locations and exit")
    p.add_argument("--validate", action="store_true", help="compare against the Excel reports")
    p.add_argument("--trend", action="store_true",
                   help="with --location: write a per-location over-time stats CSV (all years)")
    p.add_argument("--out", default=DEFAULT_OUT, help="output directory for reports")
    p.add_argument("--in-place", action="store_true",
                   help="write into each study's OWN folder instead of --out, as "
                        f"<study>_{DEFAULT_IN_PLACE_SUFFIX}.<ext>. Existing files are "
                        "skipped, so a re-run resumes where it stopped.")
    p.add_argument("--suffix", default=DEFAULT_IN_PLACE_SUFFIX,
                   help=f"filename suffix for --in-place (default {DEFAULT_IN_PLACE_SUFFIX})")
    p.add_argument("--overwrite", action="store_true",
                   help="with --in-place, replace files this tool already wrote")
    p.add_argument("--format", choices=sorted(FORMATS), default="both")
    p.add_argument("--speed-limit", type=float, default=None,
                   help="override the posted speed limit (mph). If omitted, resolved per "
                        "study: Notes 'Limit:' line -> existing Excel report -> default 25.")
    p.add_argument("--include-compromised", action="store_true",
                   help="include _Compromised Studies folders")
    args = p.parse_args(argv)

    if not args.base:
        p.error(NO_DATA_BASE_MSG)
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

    if args.trend:
        from traffic_diag.trends import over_time_table
        matches = _select(args.base, args.year, args.location)
        if not matches:
            print("No matching studies for --trend."); return 1
        location = matches[0].location
        table = over_time_table(args.base, location)
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, f"{location}_trend.csv")
        table.to_csv(path, index=False)
        print(f"[OK] {location}: {len(table)} studies over time -> {os.path.relpath(path)}")
        print(table.to_string(index=False))
        return 0

    studies = _select(args.base, args.year, args.location)
    if not args.all:
        if not args.location:
            p.error("specify --location, or pass --all")
        studies = studies[:1] if studies else []
    if not studies:
        print("No matching studies.")
        return 1

    formats = FORMATS[args.format]

    if args.in_place:
        stem_probe = f"x_{args.suffix}"
        if _collides_with_legacy(stem_probe):
            p.error(f"--suffix {args.suffix!r} would produce '<study>_{args.suffix}.xlsx', "
                    f"which Windows treats as the existing '<study>_Report.xlsx' and would "
                    f"destroy it. Choose a suffix that does not end in 'Report'.")
        print(f"Writing into each study's own folder as <study>_{args.suffix}.<ext>; "
              f"existing files are {'REPLACED' if args.overwrite else 'skipped'}.")

    n_written = n_skipped = n_err = 0
    for s in studies:
        try:
            result = process_study(s, speed_limit=args.speed_limit)
            made, skipped = _emit(result, args.out, formats, in_place=args.in_place,
                                  suffix=args.suffix, overwrite=args.overwrite)
            n_written += len(made)
            n_skipped += len(skipped)
            d = result.diagnostics
            note = ", ".join(os.path.basename(m) for m in made) or "nothing new"
            if skipped:
                note += f"  (skipped {len(skipped)} already there)"
            print(f"[OK] {s.study_id}: total={result.merged.total} "
                  f"85th={result.merged.design_speed:.1f} risk={d.risk if d else '-'} "
                  f"-> {note}")
        except Exception as e:
            n_err += 1
            print(f"[ERR] {s.study_id}: {e}")
    if len(studies) > 1:
        print(f"\n{len(studies)} studies: {n_written} file(s) written, "
              f"{n_skipped} skipped, {n_err} error(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
