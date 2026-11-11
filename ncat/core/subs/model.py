"""Subtitle cue model and ASS bridge.

Cue objects are the editable subtitle units. ASS import/export keeps the
template's styles, script info, comments and BOM/CRLF conventions.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Iterable, Sequence

from ..project import new_id, now_iso
from ..types import Cue, Segment
from .ass import (
    AssDocument, AssEvent, AssStyle, parse_ass, write_ass,
)

_TAG = re.compile(r"\{[^}]*\}")
_ENGLISH_SEPARATOR = "{\\r英}"

_DEFAULT_STYLE_FORMAT = [
    "Name", "Fontname", "Fontsize", "PrimaryColour", "SecondaryColour",
    "OutlineColour", "BackColour", "Bold", "Italic", "Underline",
    "StrikeOut", "ScaleX", "ScaleY", "Spacing", "Angle", "BorderStyle",
    "Outline", "Shadow", "Alignment", "MarginL", "MarginR", "MarginV",
    "Encoding",
]
_DEFAULT_EVENT_FORMAT = [
    "Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR",
    "MarginV", "Effect", "Text",
]


def cue_from_segment(segment: Segment, *, style: str = "Default") -> Cue:
    return Cue(
        id=new_id("cue"),
        project_id=segment.project_id,
        segment_id=segment.id,
        order=segment.order,
        source_text=segment.source,
        target_text=segment.target,
        style=style,
    )


def cues_from_segments(segments: Iterable[Segment], *,
                       style: str = "Default") -> list[Cue]:
    return [cue_from_segment(segment, style=style)
            for segment in sorted(segments, key=lambda item: item.order)]


def cues_from_ass(document: AssDocument, project_id: str, *,
                  bilingual_only: bool = False,
                  style: str = "Default") -> list[Cue]:
    cues: list[Cue] = []
    timestamp = now_iso()
    for event in document.dialogues():
        target, source = split_bilingual_text(event.text)
        if bilingual_only and not source:
            continue
        if not target and not source:
            continue
        cues.append(Cue(
            id=new_id("cue"),
            project_id=project_id,
            segment_id=None,
            order=len(cues),
            start_ms=event.start_ms,
            end_ms=event.end_ms,
            source_text=source,
            target_text=target,
            style=event.style or style,
            locked=False,
        ))
    return cues


def split_bilingual_text(text: str) -> tuple[str, str]:
    raw = (text or "").replace("\\N", "\\N")
    if _ENGLISH_SEPARATOR in raw:
        target, source = raw.split(_ENGLISH_SEPARATOR, 1)
        return _clean_target(target), _clean_source(source)
    if "\\N" in raw:
        parts = raw.split("\\N")
        english = [part for part in parts if _looks_english(part)]
        if english:
            source = " ".join(english)
            target = "\\N".join(part for part in parts if not _looks_english(part))
            return _clean_target(target), _clean_source(source)
    return _clean_target(raw), ""


def _looks_english(value: str) -> bool:
    stripped = _TAG.sub("", value)
    return bool(re.search(r"[A-Za-z]{2,}", stripped)) and not re.search(
        r"[\u4e00-\u9fff]", stripped)


def _clean_target(value: str) -> str:
    return value.strip()


def _clean_source(value: str) -> str:
    value = _TAG.sub("", value)
    return value.replace("\\N", " ").replace("\\n", " ").strip()


def dialogue_text(cue: Cue, *, bilingual: bool = True) -> str:
    target = cue.target_text or cue.source_text
    if bilingual and cue.source_text and cue.target_text:
        return f"{target}\\N{_ENGLISH_SEPARATOR}{cue.source_text}"
    return target


def default_ass_document() -> AssDocument:
    return AssDocument(
        script_info=[
            "Title: NCAT export",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
        ],
        style_format=list(_DEFAULT_STYLE_FORMAT),
        styles=[AssStyle(name="Default", fields={
            "Name": "Default",
            "Fontname": "Arial",
            "Fontsize": "24",
            "PrimaryColour": "&H00FFFFFF",
            "SecondaryColour": "&H000000FF",
            "OutlineColour": "&H00000000",
            "BackColour": "&H00000000",
            "Bold": "0",
            "Italic": "0",
            "Underline": "0",
            "StrikeOut": "0",
            "ScaleX": "100",
            "ScaleY": "100",
            "Spacing": "0",
            "Angle": "0",
            "BorderStyle": "1",
            "Outline": "2",
            "Shadow": "0",
            "Alignment": "2",
            "MarginL": "10",
            "MarginR": "10",
            "MarginV": "10",
            "Encoding": "1",
        })],
        event_format=list(_DEFAULT_EVENT_FORMAT),
    )


def ass_from_cues(cues: Sequence[Cue], *, template: str | Path | None = None,
                  bilingual: bool = True) -> AssDocument:
    document = (parse_ass(Path(template))
                if template and Path(template).is_file()
                else default_ass_document())
    document = copy.deepcopy(document)
    non_dialogue = [event for event in document.events
                    if event.kind != "Dialogue"]
    document.events = list(non_dialogue)
    for cue in sorted(cues, key=lambda item: (item.order, item.id)):
        document.events.append(AssEvent(
            kind="Dialogue",
            start_ms=cue.start_ms if cue.start_ms is not None else 0,
            end_ms=cue.end_ms if cue.end_ms is not None else 0,
            style=cue.style or "Default",
            text=dialogue_text(cue, bilingual=bilingual),
            fields={
                "Layer": "0",
                "Style": cue.style or "Default",
                "Text": dialogue_text(cue, bilingual=bilingual),
            },
        ))
    return document


def export_ass(cues: Sequence[Cue], path: str | Path, *,
               template: str | Path | None = None,
               bilingual: bool = True) -> Path:
    return write_ass(path, ass_from_cues(
        cues, template=template, bilingual=bilingual))


def sort_cues(cues: Sequence[Cue]) -> list[Cue]:
    return sorted(cues, key=lambda cue: (
        cue.start_ms if cue.start_ms is not None else 10**15,
        cue.order, cue.id))


def split_cue(cue: Cue, *, at_ms: int | None = None,
              ratio: float = 0.5) -> tuple[Cue, Cue]:
    start = cue.start_ms if cue.start_ms is not None else 0
    end = cue.end_ms if cue.end_ms is not None else start
    cut = at_ms if at_ms is not None else start + round((end - start) * ratio)
    cut = max(start + 1, min(end - 1, cut)) if end - start > 2 else start
    first_target, second_target = _split_text(cue.target_text)
    first_source, second_source = _split_text(cue.source_text)
    left = copy.deepcopy(cue)
    right = copy.deepcopy(cue)
    left.id = new_id("cue")
    right.id = new_id("cue")
    left.end_ms = cut
    right.start_ms = cut
    left.target_text, right.target_text = first_target, second_target
    left.source_text, right.source_text = first_source, second_source
    left.revision += 1
    right.revision += 1
    left.order = cue.order
    right.order = cue.order + 1
    return left, right


def merge_cues(first: Cue, second: Cue) -> Cue:
    merged = copy.deepcopy(first)
    merged.id = new_id("cue")
    merged.end_ms = second.end_ms
    merged.target_text = _join_text(first.target_text, second.target_text)
    merged.source_text = _join_text(first.source_text, second.source_text)
    merged.alignment_method = None
    merged.timing_confidence = None
    merged.revision += 1
    return merged


def _split_text(text: str) -> tuple[str, str]:
    text = text or ""
    words = text.split()
    if len(words) < 2:
        return text, ""
    middle = len(words) // 2
    return " ".join(words[:middle]), " ".join(words[middle:])


def _join_text(left: str, right: str) -> str:
    left, right = (left or "").strip(), (right or "").strip()
    if not left:
        return right
    if not right:
        return left
    return f"{left} {right}"
