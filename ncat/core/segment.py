"""Segment creation and bilingual text import.

Segments are the translation unit; cues are the timed subtitle unit. This
module deliberately keeps them separate: importing bilingual text creates
segments, and timing is attached later by ``align``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Sequence

from .project import new_id, now_iso
from .text import normalise_text, source_hash
from .types import Segment, SegmentStatus, Transcript, Word

_SENTENCE_END = re.compile(r"[.!?。！？][\"'”’)]*$")


def read_lines(path: str | Path) -> list[str]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def load_bilingual_pair(source_path: str | Path,
                        target_path: str | Path) -> list[tuple[str, str]]:
    source_lines = read_lines(source_path)
    target_lines = read_lines(target_path)
    if len(source_lines) != len(target_lines):
        raise ValueError(
            f"bilingual line count mismatch: {len(source_lines)} source lines "
            f"vs {len(target_lines)} target lines"
        )
    return list(zip(source_lines, target_lines))


def segments_from_pairs(
    pairs: Iterable[tuple[str, str]],
    project_id: str,
    *,
    status: SegmentStatus = SegmentStatus.TRANSLATED,
) -> list[Segment]:
    segments: list[Segment] = []
    timestamp = now_iso()
    for order, (source, target) in enumerate(pairs):
        source_text = normalise_text(source)
        target_text = normalise_text(target)
        if not source_text and not target_text:
            continue
        segments.append(Segment(
            id=new_id("seg"),
            project_id=project_id,
            order=len(segments),
            source=source_text,
            target=target_text,
            source_hash=source_hash(source_text),
            status=status if target_text else SegmentStatus.EMPTY,
            created_at=timestamp,
            updated_at=timestamp,
        ))
    return segments


def segments_from_source_text(text: str | Iterable[str],
                              project_id: str) -> list[Segment]:
    if isinstance(text, str):
        lines = [normalise_text(line) for line in text.splitlines()]
    else:
        lines = [normalise_text(line) for line in text]
    return segments_from_pairs(
        ((line, "") for line in lines if line), project_id,
        status=SegmentStatus.EMPTY,
    )


def segments_from_transcript(transcript: Transcript,
                             project_id: str) -> list[Segment]:
    segments: list[Segment] = []
    timestamp = now_iso()
    for order, text in enumerate(_transcript_units(transcript)):
        clean = normalise_text(text)
        if not clean:
            continue
        segments.append(Segment(
            id=new_id("seg"),
            project_id=project_id,
            order=len(segments),
            source=clean,
            source_hash=source_hash(clean),
            status=SegmentStatus.EMPTY,
            created_at=timestamp,
            updated_at=timestamp,
        ))
    return segments


def segments_to_bilingual(segments: Sequence[Segment]) -> str:
    lines = []
    for segment in sorted(segments, key=lambda item: item.order):
        lines.append(f"{segment.source}\t{segment.target}")
    return "\n".join(lines) + ("\n" if lines else "")


def _transcript_units(transcript: Transcript) -> list[str]:
    native = [
        normalise_text(item.get("text", ""))
        for item in transcript.segments
        if normalise_text(item.get("text", ""))
    ]
    if native:
        return native
    if transcript.chunks:
        chunks = [
            normalise_text(item.get("text", ""))
            for item in transcript.chunks
            if normalise_text(item.get("text", ""))
        ]
        if chunks:
            return chunks
    return _group_words(transcript.words)


def _group_words(words: Sequence[Word], *, max_words: int = 14,
                 pause_ms: int = 700) -> list[str]:
    units: list[str] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            units.append(" ".join(word.text for word in current))
            current.clear()

    for word in words:
        if current:
            gap = word.start_ms - current[-1].end_ms
            if gap >= pause_ms:
                flush()
        current.append(word)
        if len(current) >= max_words or _SENTENCE_END.search(word.text):
            flush()
    flush()
    return units
