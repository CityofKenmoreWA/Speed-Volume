# etc/ — things that are not part of this project

Parked here to keep the project root to just the application. Nothing in this
folder is imported by `traffic_diag`, the dashboard, the CLI scripts, or the deploy
bundle, and nothing here is needed to run or build any of them.

Git ignores everything in `etc/` except this file (`/etc/*` plus `!/etc/README.md`
in `.gitignore`), so the contents stay local. Back them up separately if they
matter.

## `documents/`

Unrelated reference material that happened to be sitting in the project root:

| File | What it is |
|---|---|
| `NE_181_St_61_Ave_NE*.docx` (5 files) | Restriping / signing compliance reports for NE 181st St & 61st Ave NE |
| `NE_181_St_and_61_Ave_NE_Signing_Report.docx` | Signing report for the same location |
| `~$_181_St_61_Ave_NE.docx` | A Word lock file left behind by an open document — safe to delete |
| `mutcd11theditionr1hl.pdf` | MUTCD 11th edition, ~31 MB |

## `kape_analysis/`

Thirteen one-off scripts for the **KAPE portable speed-camera** study — a separate
piece of work that happens to use this project as a library. They read the speed
studies through `traffic_diag` and write their results to
`…\Claude\KAPE\portable_model\`, outside this repo entirely.

They still run from here. Each one adds the repo root to `sys.path` before
importing, and that line was updated when they moved:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))  # repo root, two levels up
```

Run them the same way as before, with the new path:

```bash
.venv\Scripts\python.exe etc\kape_analysis\profile_61st.py
```

If the KAPE work ever gets its own repository, this folder is what moves.

## `generated/`

Output that a tool produced and nobody needs to keep. `validate_results.csv` is the
per-cell detail from `tools/validate_all.py`; it is regenerated on every run, and
that tool now writes new copies to `reports/` instead of the project root.
