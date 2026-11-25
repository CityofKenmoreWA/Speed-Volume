"""Parallel report generation: the same reports as generate_report.py, several at once.

Each study is handled by one of ``--jobs`` worker processes. Output names,
locations and the skip/overwrite rules are exactly those of generate_report.py,
because this script calls that one's own ``_select`` and ``_emit`` rather than
re-implementing them - the two cannot drift apart on where a file lands or which
names are refused.

Examples
--------
  # PDF for every study, into each study's own folder, 8 at a time (resumable)
  python scripts/generate_report_parallel.py --all --in-place --format pdf --jobs 8

  # one year, all formats, into reports/<study>/
  python scripts/generate_report_parallel.py --year 2026 --all --format all

With --in-place (and no --overwrite) a study whose files are all already there
is skipped BEFORE its data is loaded, so resuming a stopped run costs seconds,
not a re-analysis of every finished study.

Exit codes: 0 all good · 1 bad arguments / nothing matched · 2 finished, but one
or more studies failed (each is printed as [ERR]).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool

# Allow running as a plain script (no install needed), and import the sequential
# generator from beside this file.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from traffic_diag.config import DEFAULT_BASE, NO_DATA_BASE_MSG  # noqa: E402
from traffic_diag.pipeline import process_study  # noqa: E402

import generate_report as seq  # noqa: E402

# Workers are replaced after roughly this many tasks each. Matplotlib and the PDF
# builder hold on to memory between figures; recycling bounds each worker's
# footprint over a run of hundreds of studies instead of letting it creep.
#
# The recycling is done by running the tasks in WAVES, each on a fresh pool - NOT
# with ProcessPoolExecutor(max_tasks_per_child=...). On Python 3.12 for Windows
# that option deadlocks: once the first workers retire, their replacements never
# pick up work and the run hangs forever with no error. A full run stalled at
# exactly 320/782 = 8 workers x 20 tasks x 2 generations, and a 200-trivial-task
# reproduction hangs the same way while finishing in 1.5 s without the option.
_TASKS_PER_WORKER = 20


def _targets(study, outdir, formats, in_place, suffix):
    """The paths ``seq._emit`` would write for this study, in its order."""
    sid = study.study_id
    folder = study.path if in_place else os.path.join(outdir, sid)
    return [os.path.join(folder, f"{sid}_{suffix}.{seq.WRITERS[n][1]}")
            for n in ("html", "excel", "pdf") if n in formats]


def _run_group(studies, opts):
    """Worker: process studies that share an output location, in order.

    Studies are grouped so that two workers never write the same file at once:
    with --out, two folders with the same name (e.g. one also filed under
    "_Compromised Studies") map to the same reports/<study_id>/ directory.
    Returns one result dict per study; errors are returned, not raised, so a
    failure that cannot be pickled still reaches the parent intact.
    """
    out = []
    for s in studies:
        t0 = time.time()
        try:
            if opts["in_place"] and not opts["overwrite"]:
                paths = _targets(s, opts["out"], opts["formats"], True, opts["suffix"])
                if paths and all(os.path.exists(p) for p in paths):
                    out.append({"id": s.study_id, "ok": True, "written": [],
                                "skipped": len(paths), "line": "all files already there",
                                "secs": time.time() - t0})
                    continue
            result = process_study(s, speed_limit=opts["speed_limit"])
            made, skipped = seq._emit(result, opts["out"], opts["formats"],
                                      in_place=opts["in_place"], suffix=opts["suffix"],
                                      overwrite=opts["overwrite"])
            d = result.diagnostics
            note = ", ".join(os.path.basename(m) for m in made) or "nothing new"
            if skipped:
                note += f"  (skipped {len(skipped)} already there)"
            line = (f"total={result.merged.total} 85th={result.merged.design_speed:.1f} "
                    f"risk={d.risk if d else '-'} -> {note}")
            out.append({"id": s.study_id, "ok": True, "written": made,
                        "skipped": len(skipped), "line": line, "secs": time.time() - t0})
        except Exception as e:  # noqa: BLE001 - one bad study must not sink the run
            out.append({"id": s.study_id, "ok": False, "written": [], "skipped": 0,
                        "line": f"{type(e).__name__}: {e}", "secs": time.time() - t0})
    return out


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Parallel traffic study report generation (same output as "
                    "generate_report.py).")
    p.add_argument("--base", default=DEFAULT_BASE, help="root Speed and Volume Studies folder")
    p.add_argument("--year", type=int, help="restrict to one year")
    p.add_argument("--location", help="location substring (omit with --all)")
    p.add_argument("--all", action="store_true", help="process every matching study")
    p.add_argument("--out", default=seq.DEFAULT_OUT, help="output directory for reports")
    p.add_argument("--in-place", action="store_true",
                   help="write into each study's OWN folder instead of --out, as "
                        f"<study>_{seq.DEFAULT_IN_PLACE_SUFFIX}.<ext>. Existing files "
                        "are skipped, so a re-run resumes where it stopped.")
    p.add_argument("--suffix", default=seq.DEFAULT_IN_PLACE_SUFFIX,
                   help=f"filename suffix (default {seq.DEFAULT_IN_PLACE_SUFFIX})")
    p.add_argument("--overwrite", action="store_true",
                   help="with --in-place, replace files this tool already wrote")
    p.add_argument("--format", choices=sorted(seq.FORMATS), default="both")
    p.add_argument("--speed-limit", type=float, default=None,
                   help="override the posted speed limit (mph); else resolved per study")
    p.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                   help="worker processes (default: min(8, CPU count)). Each needs "
                        "~400 MB RAM; over a network share, the share may saturate "
                        "before the CPUs do.")
    args = p.parse_args(argv)

    if not args.base:
        p.error(NO_DATA_BASE_MSG)
    if not os.path.isdir(args.base):
        p.error(f"base folder not found: {args.base}")
    if args.jobs < 1:
        p.error("--jobs must be at least 1")
    if not args.all and not args.location:
        p.error("specify --location, or pass --all")
    if seq._collides_with_legacy(f"x_{args.suffix}"):
        p.error(f"--suffix {args.suffix!r} would produce '<study>_{args.suffix}.xlsx', "
                f"which Windows treats as the existing '<study>_Report.xlsx' and would "
                f"destroy it. Choose a suffix that does not end in 'Report'.")

    studies = seq._select(args.base, args.year, args.location)
    if not args.all:
        studies = studies[:1]
    if not studies:
        print("No matching studies.")
        return 1

    formats = seq.FORMATS[args.format]
    opts = {"out": args.out, "formats": formats, "in_place": args.in_place,
            "suffix": args.suffix, "overwrite": args.overwrite,
            "speed_limit": args.speed_limit}

    # One task per output location, keeping discovery order inside each.
    groups = defaultdict(list)
    for s in studies:
        groups[s.path if args.in_place else s.study_id.lower()].append(s)
    tasks = list(groups.values())
    jobs = min(args.jobs, len(tasks))

    print(f"Data folder: {args.base}")
    if args.in_place:
        print(f"Writing into each study's own folder as <study>_{args.suffix}.<ext>; "
              f"existing files are {'REPLACED' if args.overwrite else 'skipped'}.")
    else:
        print(f"Writing into {os.path.abspath(args.out)}")
    print(f"{len(studies)} studies, format={args.format}, {jobs} worker(s)\n", flush=True)

    n_done = n_written = n_skipped = n_err = 0
    t_start = time.time()
    wave = jobs * _TASKS_PER_WORKER   # see _TASKS_PER_WORKER: fresh pool per wave
    for w0 in range(0, len(tasks), wave):
        pool = ProcessPoolExecutor(max_workers=jobs)
        try:
            futures = {pool.submit(_run_group, g, opts): g for g in tasks[w0:w0 + wave]}
            for fut in as_completed(futures):
                try:
                    results = fut.result()
                except BrokenProcessPool:
                    raise
                except Exception as e:  # noqa: BLE001 - the worker itself died
                    results = [{"id": s.study_id, "ok": False, "written": [], "skipped": 0,
                                "line": f"worker failed: {e}", "secs": 0.0}
                               for s in futures[fut]]
                for r in results:
                    n_done += 1
                    n_written += len(r["written"])
                    n_skipped += r["skipped"]
                    tag = "OK" if r["ok"] else "ERR"
                    n_err += 0 if r["ok"] else 1
                    print(f"[{n_done}/{len(studies)}] [{tag}] {r['id']}: {r['line']}  "
                          f"({r['secs']:.1f}s)", flush=True)
        except KeyboardInterrupt:
            print("\nInterrupted - stopping workers. Re-run with --in-place to resume.",
                  flush=True)
            pool.shutdown(wait=False, cancel_futures=True)
            return 130
        except BrokenProcessPool:
            print("\nA worker process died unexpectedly (often: out of memory). "
                  "Re-run with a lower --jobs; with --in-place it resumes.", flush=True)
            pool.shutdown(wait=False, cancel_futures=True)
            return 2
        pool.shutdown(wait=True)

    mins = (time.time() - t_start) / 60
    print(f"\n{len(studies)} studies in {mins:.1f} min: {n_written} file(s) written, "
          f"{n_skipped} skipped, {n_err} error(s).")
    return 2 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
