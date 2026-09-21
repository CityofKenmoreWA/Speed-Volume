"""Sync the deploy bundle from source, and optionally repackage its zip.

``deploy/KenmoreTrafficDashboard`` is a self-contained copy of the app — its own
bundled Python plus the runtime subset of this project — and ``.gitignore``
excludes that copied code. Nothing about it shows up in ``git status``, so an
edit to ``traffic_diag/`` here leaves the bundle silently running last month's
code until someone notices. This script is the missing step.

The bundle is a SUBSET, not a mirror: it ships what the dashboard and the
catalog refresh need, and leaves out development-only modules (``validate.py``
compares against the legacy Excel; ``catalog_xy.py`` and the other scripts are
one-off tooling). ``EXCLUDE`` below records that, so a new module added here does
not silently become part of the distributable.

    python scripts/package_deploy.py            # sync the folder, report changes
    python scripts/package_deploy.py --zip      # sync, then rebuild the .zip too
    python scripts/package_deploy.py --check    # report drift, change nothing

Exit codes: 0 in sync / synced · 1 source or bundle missing · 2 (--check only)
the bundle is out of date.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(REPO_ROOT, "deploy", "KenmoreTrafficDashboard")
ZIP_PATH = os.path.join(REPO_ROOT, "deploy", "KenmoreTrafficDashboard.zip")

# Modules that stay behind: development tooling the server never runs.
EXCLUDE = {"validate.py", "catalog_xy.py"}

# Scripts the server DOES run (the scheduled catalog refresh). The report
# generator and the one-off maintenance scripts are not part of the bundle.
SCRIPTS = {"build_catalog.py"}


def _runtime_files() -> list:
    """Relative paths that belong in the bundle, as (source, destination) pairs."""
    out = []
    pkg = os.path.join(REPO_ROOT, "traffic_diag")
    for name in sorted(os.listdir(pkg)):
        if name.endswith(".py") and name not in EXCLUDE:
            out.append(os.path.join("traffic_diag", name))
    out.append(os.path.join("app", "streamlit_app.py"))
    for name in sorted(SCRIPTS):
        out.append(os.path.join("scripts", name))
    assets = os.path.join(REPO_ROOT, "assets")
    if os.path.isdir(assets):
        for dirpath, _dirnames, files in os.walk(assets):
            for f in files:
                full = os.path.join(dirpath, f)
                out.append(os.path.relpath(full, REPO_ROOT))
    return out


def _stale_pycache() -> list:
    """Project ``__pycache__`` directories inside the bundle.

    The bundled interpreter writes these the first time it imports the app, and
    they hold bytecode for whatever the code was at that moment. The shipped zip
    has never contained any, so they are cleared before packaging rather than
    distributing stale bytecode alongside fresh sources.
    """
    hits = []
    for sub in ("traffic_diag", "app", "scripts"):
        for dirpath, dirnames, _files in os.walk(os.path.join(BUNDLE, sub)):
            for d in list(dirnames):
                if d == "__pycache__":
                    hits.append(os.path.join(dirpath, d))
    return hits


def sync(check_only: bool = False) -> list:
    """Copy the runtime subset into the bundle. Returns the paths that differed."""
    changed = []
    for rel in _runtime_files():
        src = os.path.join(REPO_ROOT, rel)
        dst = os.path.join(BUNDLE, rel)
        if not os.path.exists(src):
            continue
        same = os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False)
        if same:
            continue
        changed.append(rel)
        if not check_only:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    return changed


def build_zip() -> str:
    """Repackage the bundle, mirroring the layout the existing zip already uses:
    every file under a single top-level ``KenmoreTrafficDashboard/`` folder."""
    for d in _stale_pycache():
        shutil.rmtree(d, ignore_errors=True)

    tmp = ZIP_PATH + ".tmp"
    n = 0
    # Written to a temp file and moved into place, so an interrupted build cannot
    # leave a truncated archive where the working distributable used to be.
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirnames, files in os.walk(BUNDLE):
            dirnames.sort()
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                arc = os.path.relpath(full, os.path.dirname(BUNDLE)).replace(os.sep, "/")
                z.write(full, arc)
                n += 1
    os.replace(tmp, ZIP_PATH)
    print("  packaged {:,} files -> {} ({:.1f} MB)".format(
        n, os.path.relpath(ZIP_PATH, REPO_ROOT), os.path.getsize(ZIP_PATH) / 1e6))
    return ZIP_PATH


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", action="store_true", help="also rebuild the .zip")
    ap.add_argument("--check", action="store_true",
                    help="report drift without changing anything")
    args = ap.parse_args(argv)

    if not os.path.isdir(BUNDLE):
        print("Deploy bundle not found: {}".format(BUNDLE))
        return 1

    changed = sync(check_only=args.check)
    if args.check:
        if changed:
            print("Bundle is OUT OF DATE - {} file(s) differ from source:".format(len(changed)))
            for rel in changed:
                print("   ", rel)
            return 2
        print("Bundle is in sync with source.")
        return 0

    if changed:
        print("Synced {} file(s) into the bundle:".format(len(changed)))
        for rel in changed:
            print("   ", rel)
    else:
        print("Bundle already in sync with source.")

    if args.zip:
        print("Rebuilding {} ...".format(os.path.relpath(ZIP_PATH, REPO_ROOT)))
        build_zip()
    elif changed:
        print("\nThe .zip still holds the old code - re-run with --zip to repackage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
