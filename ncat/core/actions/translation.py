"""Translate action: TM/term-aware MT suggestions with cue sync."""
from __future__ import annotations

from typing import Any

from ..assets.termbase import match_terms
from ..assets.tm import find_matches
from ..index import build_index
from ..mt import get_backend as get_mt_backend
from ..mt.base import MTRequest, SegmentInput
from ..project import (
    ProjectState, read_cues, read_segments, write_cues, write_segments,
)
from ..subs.rules import apply_all
from ..types import SegmentStatus
from .common import ActionError, project_terms_tm


def run_translate(state: ProjectState, *, backend: str = "llama_cpp",
                  base_url: str | None = None, model: str | None = None,
                  target_lang: str | None = None, brief: str = "",
                  style: str = "", genre: str = "字幕", limit: int = 0,
                  options: dict[str, Any] | None = None,
                  fallback: dict[str, Any] | None = None) -> dict:
    """Translate segments through the configured MT backend."""
    segments = read_segments(state.paths)
    if not segments:
        raise ActionError("missing_segments", "run segment first")
    terms, tm_units = project_terms_tm(state)
    inputs = []
    selected = segments[:limit] if limit else segments
    for index, segment in enumerate(selected):
        context_before = [item.source for item in
                          segments[max(0, index - 1):index]]
        context_after = [item.source for item in
                         segments[index + 1:index + 2]]
        inputs.append(SegmentInput(
            id=segment.id, source=segment.source,
            context_before=context_before, context_after=context_after,
            tm_matches=find_matches(segment.source, tm_units),
            term_hits=match_terms(segment.source, terms),
        ))
    backend_name = backend or "llama_cpp"
    model_name = model or "local"
    backend_options = dict(options or {})
    if base_url:
        backend_options["base_url"] = base_url
    backend_options["model"] = model_name
    resolved_target = (target_lang or
                       (state.project.target_langs[0]
                        if state.project.target_langs else "zh-Hans"))

    def build_request(request_options: dict[str, Any]) -> MTRequest:
        return MTRequest(
            source_lang=state.project.source_lang,
            target_lang=resolved_target,
            segments=inputs,
            document_brief=brief or "",
            style=style or "",
            genre=genre or "字幕",
            options=request_options,
        )

    fallback_used = False
    try:
        result = get_mt_backend(backend_name).translate(
            build_request(backend_options))
    except Exception as primary_error:
        if not fallback:
            raise ActionError("mt_error", str(primary_error)) from primary_error
        fallback_backend = fallback.get("backend") or backend_name
        fallback_options = dict(fallback.get("options") or {})
        fallback_options["model"] = fallback.get("model") or "local"
        try:
            result = get_mt_backend(fallback_backend).translate(
                build_request(fallback_options))
            fallback_used = True
        except Exception as fallback_error:
            raise ActionError(
                "mt_error",
                f"primary: {primary_error}; fallback: {fallback_error}",
            ) from fallback_error

    by_id = {segment.id: segment for segment in selected}
    updated = 0
    term_hits = 0
    empty = 0
    echo = 0
    for target in result.segments:
        segment = by_id.get(target.id)
        if segment is None:
            continue
        term_hits += len(match_terms(segment.source, terms))
        if not target.target.strip():
            empty += 1
        if target.target.strip() == segment.source.strip():
            echo += 1
        if segment.locked or segment.status == SegmentStatus.APPROVED:
            continue
        segment.target = target.target
        segment.status = SegmentStatus.MT_SUGGESTED
        segment.revision += 1
        updated += 1
    write_segments(state.paths, segments)
    cues = read_cues(state.paths)
    updated_ids = {segment.id for segment in selected
                   if segment.id in by_id
                   and not segment.locked
                   and segment.status != SegmentStatus.APPROVED}
    for cue in cues:
        if cue.locked:
            continue
        segment = by_id.get(cue.segment_id)
        if segment is None and 0 <= cue.order < len(segments):
            segment = segments[cue.order]
        if segment is not None and segment.id in updated_ids:
            cue.target_text = segment.target
            cue.revision += 1
    apply_all(cues)
    write_cues(state.paths, cues)
    build_index(state.paths)
    return {
        "project": state.project.id,
        "provider": result.provider,
        "model": result.model,
        "segments": len(result.segments),
        "updated": updated,
        "term_hits": term_hits,
        "empty": empty,
        "source_echo": echo,
        "fallback_used": fallback_used,
        "usage": result.usage.__dict__,
    }
