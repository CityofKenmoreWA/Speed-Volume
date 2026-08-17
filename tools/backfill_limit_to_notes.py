"""Backfill a ``Limit: <n>`` line into each study's _Notes.txt, from its Excel.

One-time helper: walks the whole data tree, and for every folder that contains a
``*_Report.xlsx`` reads the posted speed limit and records it as a ``Limit: <n>``
line in that folder's ``*_Notes.txt`` (creating the notes file if absent). This
walks the filesystem directly, so it covers every special/nested subfolder
(_COVID-19, _Short Studies, _Validation, _MX SFS, Radar Data, etc.), not just the
folders the study-discovery logic traverses. Going forward the ``Limit:`` line is
maintained by hand for new studies.

Idempotent: folders whose notes already contain a ``Limit:`` line are skipped, so
re-running is safe. Dry-run by default; pass --apply to write.

Usage:
    python tools/backfill_limit_to_notes.py            # preview (no changes)
    python tools/backfill_limit_to_notes.py --apply    # write the files
    python tools/backfill_limit_to_notes.py --apply --no-create   # only append to existing notes
"""
import glob
import os
import sys
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.config import DEFAULT_BASE
from traffic_diag.discovery import _LIMIT_RE
from traffic_diag.study import read_speed_limit_xlsx


def _folders_with_report(base):
    """Every folder under base containing a *_Report.xlsx (covers nested subfolders)."""
    for dirpath, _dirs, files in os.walk(base):
        if any(f.lower().endswith("_report.xlsx") for f in files):
            yield dirpath


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    create_missing = "--no-create" not in argv
    base = DEFAULT_BASE
    if "--base" in argv:
        base = argv[argv.index("--base") + 1]

    updated, already, created, no_limit, errors = [], [], [], [], []

    for folder in _folders_with_report(base):
        name = os.path.basename(folder)
        try:
            xlsx = sorted(glob.glob(os.path.join(folder, "*_Report.xlsx")))[0]
            limit = read_speed_limit_xlsx(xlsx)
            if limit is None:
                no_limit.append(name)
                continue
            n = int(round(limit))
            notes = sorted(glob.glob(os.path.join(folder, "*_Notes.txt")))
            if notes:
                notes_path = notes[0]
                with open(notes_path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                if _LIMIT_RE.search(text):
                    already.append(name)
                    continue
                new_text = text.rstrip() + f"\nLimit: {n}\n"
                if apply:
                    with open(notes_path, "w", encoding="utf-8") as fh:
                        fh.write(new_text)
                updated.append((name, n))
            elif create_missing:
                new_path = os.path.join(folder, f"{name}_Notes.txt")
                if apply:
                    with open(new_path, "w", encoding="utf-8") as fh:
                        fh.write(f"Limit: {n}\n")
                created.append((name, n))
            else:
                no_limit.append(name + " (no notes file)")
        except Exception as e:
            errors.append((name, str(e)[:80]))

    mode = "APPLIED" if apply else "DRY-RUN (no files changed)"
    print(f"=== Backfill Limit -> Notes [{mode}] ===")
    print(f"Folders with a report   : {len(updated) + len(already) + len(created) + len(no_limit)}")
    print(f"Will append Limit line  : {len(updated)}")
    print(f"Will create notes file  : {len(created)}")
    print(f"Already have Limit line : {len(already)}")
    print(f"Report but no limit     : {len(no_limit)}")
    print(f"Errors                  : {len(errors)}")
    for label, items in (("append", updated), ("create", created)):
        for nm, n in items[:6]:
            print(f"   {label}  {nm}  -> Limit: {n}")
    if errors:
        print("  errors:")
        for nm, e in errors[:10]:
            print(f"   {nm}: {e}")
    if not apply:
        print("\nRe-run with --apply to write the files.")


if __name__ == "__main__":
    main()
