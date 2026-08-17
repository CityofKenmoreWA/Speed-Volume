"""Build (or refresh) the study catalog table before the dashboard opens.

Scans every year folder under the data base once and writes ``study_catalog.csv``
next to the data tree. Run automatically by run_dashboard.bat; can also be run
by hand:

    python scripts/build_catalog.py [--base <data folder>]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.catalog import catalog_path, read_catalog, refresh_catalog, write_catalog
from traffic_diag.config import DEFAULT_BASE


def main() -> int:
    ap = argparse.ArgumentParser(description="Build/refresh the study catalog table.")
    ap.add_argument("--base", default=os.environ.get("TRAFFIC_DATA_BASE", DEFAULT_BASE),
                    help="Data root that holds the <year> folders.")
    ap.add_argument("--no-metrics", action="store_true",
                    help="skip computing avg/85th/ADT/AWDT (structure only)")
    args = ap.parse_args()

    if not os.path.isdir(args.base):
        print(f"[catalog] data folder not found: {args.base}", file=sys.stderr)
        return 1

    print(f"[catalog] scanning {args.base} …")
    # Refresh incrementally: reuse metrics for studies already in the CSV, compute
    # only new ones.
    stats: dict = {}
    df = refresh_catalog(args.base, compute=not args.no_metrics, stats=stats)
    out = catalog_path(args.base)
    wrote = read_catalog(args.base) is not None and os.path.exists(out)
    n_locs = df["location"].nunique() if len(df) else 0
    n_years = df["year"].nunique() if len(df) else 0
    print(f"[catalog] {stats.get('total', len(df))} studies · {n_locs} locations · "
          f"{n_years} years  (metrics: {stats.get('computed', 0)} new, "
          f"{stats.get('reused', 0)} reused)")
    if wrote:
        print(f"[catalog] -> {out}")
    else:
        print(f"[catalog] could not write CSV to {out} (open in Excel / read-only?); "
              f"the app will build it in memory", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
