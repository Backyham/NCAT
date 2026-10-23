"""NCAT pipeline stages.

Every stage is a plain function taking ``(job_id, report, paths, ...)``, and
``pipeline.runner`` dispatches them by name for the web worker. There is no
command-line entry point -- see ``paths.py`` for why.

``paths.py`` holds the stage order and the job directory layout.
"""
from __future__ import annotations

from .paths import STAGES, STAGE_LABELS, JobPaths, Report, build_paths

__all__ = ['STAGES', 'STAGE_LABELS', 'JobPaths', 'Report', 'build_paths']
