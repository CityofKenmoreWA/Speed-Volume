"""Build (or refresh) the study catalog table.

Scans every year folder under the data base and writes ``study_catalog.csv`` next
to the data tree. This is what normally writes to the share; the dashboard only
reads the CSV — except that it writes a structure-only catalog if the file is
missing altogether, so this script should be what creates it first. Run it from
the scheduled task (``KenmoreTrafficDashboard.bat refresh``), or by hand:

    python scripts/build_catalog.py [--base <data folder>] [--no-metrics]

Exit codes: 0 refreshed · 1 data folder unreachable · 2 could not write the CSV.
A non-zero exit is what lets the scheduled task surface a stale catalog instead
of failing silently.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.catalog import catalog_path, refresh_catalog
from traffic_diag.config import DEFAULT_BASE


def main() -> int:
    ap = argparse.ArgumentParser(description="Build/refresh the study catalog table.")
    ap.add_argument("--base", default=os.environ.get("TRAFFIC_DATA_BASE", DEFAULT_BASE),
                    help="Data root that holds the <year> folders.")
    ap.add_argument("--no-metrics", action="store_true",
                    help="skip computing avg/85th/ADT/AWDT (structure only)")
    args = ap.parse_args()

    stamp = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    if not os.path.isdir(args.base):
        print(f"[{stamp}] data folder not reachable: {args.base}", file=sys.stderr)
        return 1

    print(f"[{stamp}] scanning {args.base} ...")
    # Refresh incrementally: reuse metrics for studies already in the CSV, compute
    # only new ones.
    stats: dict = {}
    df, wrote = refresh_catalog(args.base, compute=not args.no_metrics, stats=stats)
    out = catalog_path(args.base)
    n_locs = df["location"].nunique() if len(df) else 0
    n_years = df["year"].nunique() if len(df) else 0
    print(f"[{stamp}] {stats.get('total', len(df))} studies | {n_locs} locations | "
          f"{n_years} years  (metrics: {stats.get('computed', 0)} new/changed, "
          f"{stats.get('reused', 0)} unchanged)")
    if wrote:
        print(f"[{stamp}] wrote {out}")
        return 0
    print(f"[{stamp}] COULD NOT WRITE {out} - the catalog is now stale. "
          f"Check that the account running this task has write access to the file "
          f"(and that it is not open in Excel).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
