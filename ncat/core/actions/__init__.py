"""Use-case actions shared by the CLI and HTTP API.

The CLI keeps argument parsing and terminal formatting; these functions own
the orchestration (validation, run records, file writes, stage changes).
"""
from __future__ import annotations

from .alignment import run_align
from .common import (
    ActionError, parse_options, project_terms_tm, slug,
)
from .export import run_render, run_style
from .ingest import run_ingest
from .segmentation import run_segment
from .transcribe import list_backends, run_transcribe
from .translation import run_translate

__all__ = [
    "ActionError",
    "list_backends",
    "parse_options",
    "project_terms_tm",
    "run_align",
    "run_ingest",
    "run_render",
    "run_segment",
    "run_style",
    "run_transcribe",
    "run_translate",
    "slug",
]
