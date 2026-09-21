"""Strip the export preamble from raw radar CSVs.

Most ``*_Raw.csv`` files start with the real header on line 1::

    Date&Time,Speed,Class,Direction
    3/17/2023 12:01:07 AM,31.0,Medium,Incoming

A handful were saved from the radar software's *report* view instead of its
plain CSV export, so the header is pushed down by a title block::

    Raw Data,,,,,,
    ArrowheadDr_no_151stSt20230309,,from Fri-Mar-10-2023-12-00-AM to ...
    ,,,,,3/12/2023 2:00,
    Date&Time,Speed,Class,Direction,,is dst,      <- the real header
    3/10/2023 0:33,38,Medium,Incoming,,,

``pandas`` then takes ``Raw Data,,,,,,`` as the header and the loader fails with
"No timestamp column", which is why those studies carry blank metrics in the
catalog. This script deletes the preamble lines so the header lands on line 1.

It is a dry run unless ``--apply`` is given. Every file it rewrites is copied to
a backup tree first, and the rewrite is only kept if the result loads cleanly
through the normal adapter - a file that would not parse afterwards is restored
and reported instead.

    python scripts/strip_raw_preamble.py --base <folder>            # dry run
    python scripts/strip_raw_preamble.py --base <folder> --apply

Exit codes: 0 nothing to do / all good - 1 data folder unreachable - 2 one or
more files could not be fixed.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.config import (DEFAULT_BASE, NO_DATA_BASE_MSG, RADAR, REPO_ROOT, TS)
from traffic_diag.sources import SourceAdapter

# The header row is the first line whose first field is one of these. Taken from
# the source spec so this stays in step with what the loader actually accepts.
_TS_NAMES = {str(c).strip().lower().replace(" ", "") for c in RADAR.column_map[TS]}

# A preamble longer than this means the file is not what we think it is; leave it
# alone rather than guess. The known-bad exports all put the header on line 4.
MAX_PREAMBLE = 10


def find_header_line(raw: bytes) -> Optional[int]:
    """Index of the line holding the real header, or None if there isn't one.

    Works on bytes and returns a line index, so the caller can drop whole lines
    without re-encoding the file: these CSVs are CRLF and occasionally carry a
    BOM, and neither should change just because we removed some lines.
    """
    for i, line in enumerate(raw.splitlines()[:MAX_PREAMBLE + 1]):
        first = line.decode("utf-8-sig", errors="replace").split(",")[0]
        if first.strip().lower().replace(" ", "") in _TS_NAMES:
            return i
    return None


def raw_files(base: str) -> list:
    """Every ``*_Raw.csv`` under ``<base>/<year>/...``, sorted."""
    return sorted(glob.glob(os.path.join(base, "*", "**", "*_Raw.csv"), recursive=True))


def loads_ok(path: str):
    """(True, detail) if the normal adapter reads this file into a non-empty frame."""
    try:
        df = SourceAdapter(RADAR).load_file(path)
    except Exception as exc:
        return False, "{}: {}".format(type(exc).__name__, exc)
    if df.empty:
        return False, "parsed but no usable rows"
    return True, "{:,} rows".format(len(df))


def process(path: str, base: str, backup_root: str, apply: bool) -> dict:
    """Inspect one file; strip its preamble when ``apply``. Returns a result row."""
    rel = os.path.relpath(path, base)
    out = {"rel": rel, "skipped": 0, "status": "", "detail": ""}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        out.update(status="ERROR", detail="unreadable: {}".format(exc))
        return out

    idx = find_header_line(raw)
    if idx is None:
        # No recognizable header at all. Not this script's problem to solve, but
        # worth surfacing: the loader cannot read it either.
        out.update(status="NO HEADER",
                   detail="no timestamp column in first {} lines".format(MAX_PREAMBLE + 1))
        return out
    if idx == 0:
        out.update(status="ok", detail="header already on line 1")
        return out

    out["skipped"] = idx
    lines = raw.splitlines(keepends=True)
    trimmed = b"".join(lines[idx:])

    if not apply:
        out.update(status="WOULD FIX", detail="drop {} preamble line(s)".format(idx))
        return out

    backup = os.path.join(backup_root, rel)
    try:
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.copy2(path, backup)
    except OSError as exc:
        out.update(status="ERROR", detail="could not back up: {}".format(exc))
        return out

    # Write via a temp file + atomic replace, so a failure part-way through never
    # leaves a half-written CSV where the original used to be.
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(trimmed)
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        out.update(status="ERROR", detail="could not write: {}".format(exc))
        return out

    ok, detail = loads_ok(path)
    if not ok:
        # Put the original back: a file we cannot parse afterwards is worse than
        # the one we started with, whatever the preamble looked like.
        try:
            shutil.copy2(backup, path)
            out.update(status="ERROR",
                       detail="reverted, would not load ({})".format(detail))
        except OSError as exc:
            out.update(status="ERROR",
                       detail="would not load ({}) AND revert failed: {} - "
                              "restore by hand from {}".format(detail, exc, backup))
        return out

    out.update(status="FIXED", detail="dropped {} line(s) -> {}".format(idx, detail))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE, help="study data folder")
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite the files (default: dry run)")
    ap.add_argument("--backup-dir", default=None,
                    help="where originals are copied "
                         "(default: reports/raw_preamble_backup/<stamp>)")
    args = ap.parse_args()

    base = args.base
    if not base:
        print(NO_DATA_BASE_MSG)
        return 1
    if not os.path.isdir(base):
        print("Data folder unreachable: {}".format(base))
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = args.backup_dir or os.path.join(
        REPO_ROOT, "reports", "raw_preamble_backup", stamp)

    files = raw_files(base)
    print("{} - {:,} raw file(s) under {}".format(
        "APPLY" if args.apply else "DRY RUN", len(files), base))
    if args.apply:
        print("backups -> {}".format(backup_root))
    print()

    results = [process(p, base, backup_root, args.apply) for p in files]

    interesting = [r for r in results if r["status"] != "ok"]
    for r in interesting:
        print("  [{}] {}".format(r["status"], r["rel"]))
        print("        {}".format(r["detail"]))
    if not interesting:
        print("  every raw file already has its header on line 1 - nothing to do.")

    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_fixed = sum(1 for r in results if r["status"] == "FIXED")
    n_would = sum(1 for r in results if r["status"] == "WOULD FIX")
    n_bad = sum(1 for r in results if r["status"] in ("ERROR", "NO HEADER"))
    print()
    print("already fine: {:,} | {} | problems: {}".format(
        n_ok,
        "fixed: {}".format(n_fixed) if args.apply else "would fix: {}".format(n_would),
        n_bad))
    if n_would:
        print("\nRe-run with --apply to rewrite them.")
    return 2 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
