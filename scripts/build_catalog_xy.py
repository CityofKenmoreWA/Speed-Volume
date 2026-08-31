"""Build the study catalog WITH lat/lon coordinates.

Same columns as the standard catalog plus **lat** and **lon** — read straight
from each installation photo's GPS (WGS84 / EPSG:4326). Studies whose photo has
no GPS (or a 0/0 fix) get empty lat/lon.

Incremental: metrics and lat/lon for studies already in the output CSV are reused;
only new studies are processed.

    python scripts/build_catalog_xy.py [--base <data folder>] [--out <csv>] [--no-metrics]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.catalog_xy import catalog_xy_path, refresh_catalog_xy  # noqa: E402
from traffic_diag.config import DEFAULT_BASE  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the study catalog with lat/lon (WGS84).")
    ap.add_argument("--base", default=os.environ.get("TRAFFIC_DATA_BASE", DEFAULT_BASE),
                    help="Data root that holds the <year> folders.")
    ap.add_argument("--out", default=None,
                    help="Output CSV path (default: <base>/study_catalog_latlon.csv).")
    ap.add_argument("--no-metrics", action="store_true",
                    help="skip computing avg/85th/ADT/AWDT (lat/lon still read).")
    args = ap.parse_args()

    if not os.path.isdir(args.base):
        print(f"[catalog-latlon] data folder not found: {args.base}", file=sys.stderr)
        return 1

    out = args.out or catalog_xy_path(args.base)
    print(f"[catalog-latlon] scanning {args.base} ... reading photo GPS (WGS84)")
    stats: dict = {}
    df, written = refresh_catalog_xy(args.base, out=out, compute=not args.no_metrics, stats=stats)
    n_locs = df["location"].nunique() if len(df) else 0
    n_years = df["year"].nunique() if len(df) else 0
    print(f"[catalog-latlon] {stats.get('total', len(df))} studies | {n_locs} locations | "
          f"{n_years} years")
    print(f"[catalog-latlon]   metrics: {stats.get('computed', 0)} new, "
          f"{stats.get('reused', 0)} reused")
    print(f"[catalog-latlon]   lat/lon: {stats.get('latlon_present', 0)} with coords, "
          f"{stats.get('latlon_missing', 0)} without (no GPS / 0,0) "
          f"[{stats.get('latlon_computed', 0)} new, {stats.get('latlon_reused', 0)} reused]")
    if written:
        print(f"[catalog-latlon] -> {written}")
    else:
        print(f"[catalog-latlon] could not write CSV to {out} (open in Excel / read-only?)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
