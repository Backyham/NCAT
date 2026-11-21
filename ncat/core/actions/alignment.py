"""Align action: map cue timings to transcript word timestamps."""
from __future__ import annotations

from ..align import align_cues_to_words
from ..project import (
    ProjectState, read_cues, read_segments, save_project, write_cues,
)
from ..subs.model import cues_from_segments
from ..subs.rules import apply_all
from ..transcript import apply_offset, load_transcript
from ..types import ProjectStage
from .common import ActionError


def run_align(state: ProjectState, *, offset_ms: int = 0) -> dict:
    """Align cues to ASR words and refresh cue timing metrics."""
    transcript_path = state.paths.asr / "transcript.json"
    if not transcript_path.is_file():
        raise ActionError("missing_transcript",
                          "run transcribe before align")
    transcript = load_transcript(transcript_path)
    if offset_ms:
        apply_offset(transcript, offset_ms)
    cues = read_cues(state.paths)
    if not cues:
        segments = read_segments(state.paths)
        if not segments:
            raise ActionError("missing_segments",
                              "run segment before align")
        cues = cues_from_segments(segments)
    cues = align_cues_to_words(cues, transcript.words)
    metrics = apply_all(cues)
    write_cues(state.paths, cues)
    state.project.stage = ProjectStage.DRAFTING
    save_project(state)
    needs_timing = sum(1 for cue in cues if cue.needs_timing)
    confidences = [cue.timing_confidence for cue in cues
                   if cue.timing_confidence is not None]
    average = sum(confidences) / len(confidences) if confidences else None
    return {
        "project": state.project.id,
        "cues": len(cues),
        "needs_timing": needs_timing,
        "mean_confidence": None if average is None else round(average, 3),
        "metrics": metrics,
    }
