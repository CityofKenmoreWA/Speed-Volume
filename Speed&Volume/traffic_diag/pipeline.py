"""Orchestration: turn a discovered study into metrics + diagnostics (+ report).

This is the single backbone shared by the CLI and the Streamlit dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .config import (DIRECTION, TS, AnalysisConfig, DEFAULT_ANALYSIS,
                     DiagnosticThresholds, DEFAULT_THRESHOLDS, SourceSpec)
from .diagnostics import DiagnosticReport, run_diagnostics
from .discovery import Study, find_studies
from .metrics import DirectionMetrics, compute_direction_metrics, design_dates_for
from .study import StudyData, load_study

DIRECTIONS = ("Merged", "Incoming", "Outgoing")


@dataclass
class StudyResult:
    study: Study
    data: StudyData
    metrics: dict = field(default_factory=dict)        # label -> DirectionMetrics
    diagnostics: Optional[DiagnosticReport] = None

    @property
    def merged(self) -> DirectionMetrics:
        return self.metrics["Merged"]

    def summary_frame(self) -> pd.DataFrame:
        """One row per direction of the headline scalar statistics."""
        return pd.DataFrame([self.metrics[d].summary() for d in self.metrics])


def process_study(study: Study,
                  cfg: AnalysisConfig = DEFAULT_ANALYSIS,
                  thresholds: DiagnosticThresholds = DEFAULT_THRESHOLDS,
                  spec: Optional[SourceSpec] = None,
                  speed_limit: Optional[float] = None,
                  window: Optional[tuple] = None,
                  run_diag: bool = True) -> StudyResult:
    """Load a study and compute metrics for Merged / Incoming / Outgoing + diagnostics."""
    sd = load_study(study, spec=spec, cfg=cfg, speed_limit=speed_limit, window=window)

    dates = sorted(sd.window[TS].dt.normalize().unique()) if not sd.window.empty else []
    n_days = len(dates)
    design_dates = design_dates_for(dates, cfg) if dates else []

    metrics: dict = {}
    for label in DIRECTIONS:
        sub = sd.window if label == "Merged" else sd.window[sd.window[DIRECTION] == label]
        metrics[label] = compute_direction_metrics(
            sub, label, sd.speed_limit, cfg, n_days=n_days, design_dates=design_dates)

    diag = run_diagnostics(sd, thresholds) if run_diag else None
    return StudyResult(study=study, data=sd, metrics=metrics, diagnostics=diag)


def process_all(base: str,
                year: Optional[int] = None,
                cfg: AnalysisConfig = DEFAULT_ANALYSIS,
                thresholds: DiagnosticThresholds = DEFAULT_THRESHOLDS,
                include_compromised: bool = True,
                speed_limit: Optional[float] = None,
                on_error: str = "collect"):
    """Process every (or one year's) study. Yields (Study, StudyResult|None, error)."""
    studies = find_studies(base, year=year, include_compromised=include_compromised)
    for s in studies:
        try:
            yield s, process_study(s, cfg=cfg, thresholds=thresholds,
                                   speed_limit=speed_limit), None
        except Exception as e:
            if on_error == "raise":
                raise
            yield s, None, str(e)
