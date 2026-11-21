"""Export actions: ASS generation and subtitle burn-in."""
from __future__ import annotations

from pathlib import Path

from ..media_backend import get_media_backend
from ..project import ProjectState, read_cues
from ..subs.model import export_ass
from .common import ActionError, slug


def run_style(state: ProjectState, *, template: str | None = None,
              output: str | None = None,
              bilingual: bool = True) -> dict:
    """Export current cues to ASS, carrying project styles when available."""
    cues = read_cues(state.paths)
    if not cues:
        raise ActionError("missing_cues", "run segment/align first")
    selected_template = template
    if not selected_template:
        candidate = state.paths.assets / "template.ass"
        selected_template = str(candidate) if candidate.is_file() else None
    destination = (Path(output).expanduser() if output else
                   state.paths.renders / f"{slug(state.project.name)}.ass")
    path = export_ass(cues, destination, template=selected_template,
                      bilingual=bilingual)
    return {
        "project": state.project.id,
        "ass": str(path),
        "cues": len(cues),
        "template": selected_template,
    }


def run_render(state: ProjectState, *, ass: str | None = None,
               preset: str = "original", encoder: str = "libx264",
               crf: int = 20, output: str | None = None) -> dict:
    """Burn an ASS subtitle file into the project proxy/source."""
    cues = read_cues(state.paths)
    if not cues:
        raise ActionError("missing_cues", "run segment/align first")
    if ass:
        ass_path = Path(ass).expanduser()
        if not ass_path.is_file():
            raise ActionError("not_found", f"ASS not found: {ass_path}")
    else:
        ass_path = state.paths.renders / f"{slug(state.project.name)}.ass"
        if not ass_path.is_file():
            template = state.paths.assets / "template.ass"
            export_ass(cues, ass_path,
                       template=str(template) if template.is_file() else None,
                       bilingual=True)
    source = (state.source.proxy_path or state.source.path
              if state.source else None)
    if not source or not Path(source).is_file():
        raise ActionError("missing_media",
                          "no proxy/source; run ingest first")
    destination = (Path(output).expanduser() if output else
                   state.paths.renders /
                   f"{slug(state.project.name)}-{preset}.mp4")
    get_media_backend().render(
        source, ass_path, destination, preset=preset,
        encoder=encoder, crf=crf,
    )
    return {
        "project": state.project.id,
        "render": str(destination),
        "ass": str(ass_path),
        "preset": preset,
    }
