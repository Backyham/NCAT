"""NCAT pipeline stages.

Each stage is independently runnable:

    python -m pipeline.download URL
    python -m pipeline.transcode VIDEO
    python -m pipeline.transcribe AUDIO.wav
    python -m pipeline.render VIDEO ASS

and each is also callable from the web runner with the same semantics. See
``paths.py`` for the stage order and the job directory layout.
"""
from __future__ import annotations

from .paths import STAGES, STAGE_LABELS, JobPaths, Report, build_paths

__all__ = ['STAGES', 'STAGE_LABELS', 'JobPaths', 'Report', 'build_paths']
