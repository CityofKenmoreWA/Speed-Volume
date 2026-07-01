"""traffic_diag — modular traffic study diagnostics & report generation.

Backbone for reading raw traffic-counter data (radar today; SFS / GridSmart
later), selecting the best complete 7-day study period, computing the same
statistics/tables/figures as the legacy Excel report, running data-quality
diagnostics, and exporting a standardized report.

Public entry points live in ``pipeline`` (process a study / batch) and
``discovery`` (enumerate years + locations on disk).
"""

__version__ = "0.1.0"
