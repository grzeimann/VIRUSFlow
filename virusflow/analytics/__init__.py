from __future__ import annotations

"""
Analytics subsystem package.

Post-run, read-only consumers of artifacts that produce derived analytics
artifacts (plots, summaries, reports) with clear provenance.

This package intentionally depends only on the artifacts/registry layers
and does not import algorithms, tasks, planning, or executors.
"""

from .materialization import AnalysisStudyRecord, AnalysisStudyService, RetentionPolicy

__all__ = ["AnalysisStudyRecord", "AnalysisStudyService", "RetentionPolicy"]
