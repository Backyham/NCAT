"""Segment action: propose from transcript or import bilingual text."""
from __future__ import annotations

from pathlib import Path

from ..project import ProjectState, read_segments, save_project, write_cues, write_segments
from ..segment import (
    load_bilingual_pair, read_lines, segments_from_pairs,
    segments_from_transcript,
)
from ..subs.model import cues_from_segments
from ..subs.rules import apply_all
from ..transcript import load_transcript
from ..types import ProjectStage
from .common import ActionError


def run_segment(state: ProjectState, *, propose: bool = False,
                import_path: str | None = None,
                target: str | None = None,
                force: bool = False) -> dict:
    """Create segments and matching cues, replacing existing ones."""
    existing = read_segments(state.paths)
    if existing and not force:
        raise ActionError("already_exists",
                          "segments already exist; pass --force to replace")

    if propose:
        transcript_path = state.paths.asr / "transcript.json"
        if not transcript_path.is_file():
            raise ActionError("missing_transcript",
                              "run transcribe before segment --propose")
        transcript = load_transcript(transcript_path)
        segments = segments_from_transcript(transcript, state.project.id)
    elif import_path:
        path = Path(import_path).expanduser()
        if not path.is_file():
            raise ActionError("not_found", f"import not found: {path}")
        if target:
            pairs = load_bilingual_pair(path, target)
        else:
            pairs = []
            for line in read_lines(path):
                if "\t" in line:
                    source, target_text = line.split("\t", 1)
                else:
                    source, target_text = line, ""
                pairs.append((source, target_text))
        segments = segments_from_pairs(pairs, state.project.id)
    else:
        raise ActionError("invalid_argument",
                          "segment requires --propose or --import PATH")

    write_segments(state.paths, segments)
    cues = cues_from_segments(segments)
    apply_all(cues)
    write_cues(state.paths, cues)
    state.project.stage = ProjectStage.DRAFTING
    save_project(state)
    return {
        "project": state.project.id,
        "segments": len(segments),
        "cues": len(cues),
    }
