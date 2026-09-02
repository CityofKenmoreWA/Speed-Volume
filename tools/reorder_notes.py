"""Reorder every ``*_Notes.txt`` into a canonical field order.

Target layout::

    Incoming: <...>
    Outgoing: <...>
    Limit: <...>
    Source: <...>

    <everything else, verbatim, in its original order>

This ONLY moves lines. No line's text is ever altered, nothing is added, and
nothing is dropped - which is what makes it safe to run against the live share.
Two checks enforce that per file before anything is written:

  1. the multiset of non-blank lines must be identical before and after, so no
     content can be lost, gained or edited;
  2. ``discovery.read_notes`` - the production parser that resolves direction
     labels and the posted speed limit - must return the same values afterwards.

A file failing either check is left untouched and reported.

Dry run by default. Pass ``--apply`` to write, which also copies the originals to
a backup directory first (``--no-backup`` to skip).

    python tools/reorder_notes.py                       # preview
    python tools/reorder_notes.py --apply               # write
    python tools/reorder_notes.py --base "V:\\..." --apply

Note: ``*_Notes.txt`` is part of the study-catalog fingerprint, so rewriting these
files makes the next catalog refresh recompute the affected studies.
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.config import DEFAULT_BASE, NO_DATA_BASE_MSG, REPO_ROOT  # noqa: E402
from traffic_diag.discovery import (FIELD_ALIASES, Study,  # noqa: E402
                                    field_for, read_notes)

# The slot order to emit. "Limit" is handled here rather than in
# discovery.FIELD_ALIASES because the production parser finds the posted limit
# with its own regex (``_LIMIT_RE``) anywhere in the file, so "Limit:" is not one
# of its named fields - but it still needs a place in the canonical order.
SLOT_ORDER = ["Incoming", "Outgoing", "Limit", "Source"]
LIMIT_PREFIXES = ("speed limit:", "limit:")

# The spellings the production parser treats as correct. Anything in
# FIELD_ALIASES that is not the canonical spelling is a known misspelling, worth
# reporting so it can be corrected by hand - the tool never rewrites the text.
MISSPELLINGS = {k for k, v in FIELD_ALIASES.items() if k != v and k != "request"}


def classify(line: str):
    """Return (slot_name, is_misspelled) for a line, or (None, False) for notes."""
    low = line.strip().lower()
    if not low:
        return None, False
    for pre in LIMIT_PREFIXES:
        if low.startswith(pre):
            return "Limit", False
    field = field_for(line.strip())
    if not field:
        return None, False
    key = low.partition(":")[0].strip()
    return field.capitalize(), key in MISSPELLINGS


def split_fields(text: str):
    """Split into {slot: [lines]}, the notes block, and the misspellings seen.

    Only the FIRST line to claim a slot takes it; a second one stays in the notes
    rather than being silently reordered next to the first.
    """
    fields: dict[str, list[str]] = {}
    notes: list[str] = []
    typos: list[str] = []
    extras: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        slot, bad = classify(line)
        if slot and slot not in fields:
            fields[slot] = [line.strip()]
            if bad:
                typos.append(line.strip())
        elif slot:
            extras.append(line.strip())
            notes.append(line.rstrip())
        else:
            notes.append(line.rstrip())
    # trim blank lines from both ends of the notes block, keep internal ones
    while notes and not notes[0].strip():
        notes.pop(0)
    while notes and not notes[-1].strip():
        notes.pop()
    return fields, notes, typos, extras


def render(fields: dict, notes: list) -> str:
    """Canonical text, CRLF, with a trailing newline."""
    out = [fields[name][0] for name in SLOT_ORDER if name in fields]
    if notes:
        if out:
            out.append("")
        out.extend(notes)
    return "\r\n".join(out) + "\r\n"


def line_multiset(text: str) -> Counter:
    return Counter(l.strip() for l in text.splitlines() if l.strip())


def parsed_fields(text: str) -> dict:
    """What the production parser makes of this text (direction labels, limit)."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "probe_Notes.txt")
        io.open(p, "w", encoding="utf-8", newline="").write(text)
        info = read_notes(Study(path=td, year=2000, location="", install_date=None))
    return {k: info[k] for k in ("incoming", "outgoing", "source", "limit")}


def process(path: str):
    """Return (status, new_text, typos, extras). status in already/reorder/skip:<why>."""
    data = io.open(path, "rb").read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "skip:not-utf8", None, [], []

    fields, notes, typos, extras = split_fields(text)
    new = render(fields, notes)

    if line_multiset(new) != line_multiset(text):
        return "skip:content-would-change", None, typos, extras
    if parsed_fields(new) != parsed_fields(text):
        return "skip:parse-would-change", None, typos, extras
    if new == text:
        return "already", new, typos, extras
    return "reorder", new, typos, extras


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Reorder *_Notes.txt into Incoming / Outgoing / Limit / Source / notes.")
    ap.add_argument("--base", default=DEFAULT_BASE, help="study data root")
    ap.add_argument("--apply", action="store_true", help="write changes (default: preview only)")
    ap.add_argument("--backup-dir", default=None,
                    help="where to copy originals before writing "
                         "(default: <repo>/reports/notes_backup_<timestamp>)")
    ap.add_argument("--no-backup", action="store_true", help="do not copy originals")
    ap.add_argument("--max", type=int, default=0, help="process at most N files (testing)")
    ap.add_argument("--show", type=int, default=3, help="how many before/after samples to print")
    args = ap.parse_args(argv)

    if not args.base:
        ap.error(NO_DATA_BASE_MSG)
    if not os.path.isdir(args.base):
        ap.error(f"data folder not found: {args.base}")

    files = sorted(glob.glob(os.path.join(args.base, "**", "*_Notes.txt"), recursive=True))
    if args.max:
        files = files[:args.max]
    if not files:
        print("No *_Notes.txt found under", args.base)
        return 1

    backup_dir = None
    if args.apply and not args.no_backup:
        backup_dir = args.backup_dir or os.path.join(
            REPO_ROOT, "reports", f"notes_backup_{datetime.now():%Y%m%d_%H%M%S}")
        os.makedirs(backup_dir, exist_ok=True)

    print(f"{'APPLY' if args.apply else 'DRY RUN'}  -  {len(files)} file(s) under {args.base}")
    if backup_dir:
        print(f"originals -> {backup_dir}")
    print()

    stats = Counter()
    typo_files: list[tuple] = []
    extra_files: list[tuple] = []
    skipped: list[tuple] = []
    shown = 0

    for path in files:
        status, new, typos, extras = process(path)
        stats[status.split(":")[0]] += 1
        if typos:
            typo_files.append((path, typos))
        if extras:
            extra_files.append((path, extras))
        if status.startswith("skip"):
            skipped.append((path, status.split(":", 1)[1]))
            continue
        if status != "reorder":
            continue

        if shown < args.show:
            shown += 1
            old = io.open(path, encoding="utf-8").read()
            print("--- %s" % os.path.relpath(path, args.base))
            print("    BEFORE:")
            for l in old.splitlines():
                print("      | %s" % l)
            print("    AFTER:")
            for l in new.splitlines():
                print("      | %s" % l)
            print()

        if args.apply:
            if backup_dir:
                rel = os.path.relpath(path, args.base)
                dest = os.path.join(backup_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(path, dest)
            tmp = path + ".tmp"
            io.open(tmp, "w", encoding="utf-8", newline="").write(new)
            os.replace(tmp, path)

    print("=" * 62)
    print("  reordered            : %d" % stats["reorder"])
    print("  already in order     : %d" % stats["already"])
    print("  skipped (unsafe)     : %d" % stats["skip"])
    print("=" * 62)

    if typo_files:
        print("\nmisspelled field names, placed correctly but left as written (%d file(s)):"
              % len(typo_files))
        for p, t in typo_files[:15]:
            print("   %-52s %s" % (os.path.relpath(p, args.base)[:52], "; ".join(t)))
        if len(typo_files) > 15:
            print("   ... and %d more" % (len(typo_files) - 15))

    if extra_files:
        print("\nrepeated field lines, left in the notes block (%d file(s)):" % len(extra_files))
        for p, e in extra_files[:10]:
            print("   %-52s %s" % (os.path.relpath(p, args.base)[:52], "; ".join(e)))

    if skipped:
        print("\nSKIPPED - left untouched (%d):" % len(skipped))
        for p, why in skipped[:20]:
            print("   %-52s %s" % (os.path.relpath(p, args.base)[:52], why))

    if not args.apply and stats["reorder"]:
        print("\nThis was a preview. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
