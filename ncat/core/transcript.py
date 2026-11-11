"""Native transcript model and JSON I/O.

The on-disk format is NCAT's own rich JSON, not a lossy VoiceBase projection.
Known fields are normalised for alignment/CLI use; provider-specific fields are
kept under ``raw`` (per word or transcript) and native segments are preserved
verbatim, so callers can use logprobs, alternatives, token ids, etc. later.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .text import normalise_text
from .types import Transcript, Word

SCHEMA = "ncat.transcript/1"
_PUNCT = re.compile(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff']+")
_TRANSCRIPT_KEYS = {
    "schema", "source_id", "language", "backend", "model", "text", "words",
    "segments", "chunks", "duration_ms", "created_at", "metadata", "raw",
}
_WORD_KEYS = {
    "index", "text", "normalized", "start_ms", "end_ms", "confidence",
    "speaker", "raw",
}


def normalise_word(text: str) -> str:
    return _PUNCT.sub("", normalise_text(text).casefold())


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def word_from_payload(item: dict[str, Any]) -> Word | None:
    if not isinstance(item, dict):
        return None
    text = str(item.get("text", "") or "")
    normalized = str(item.get("normalized")
                     or normalise_word(text))
    if not text and not normalized:
        return None
    try:
        start_ms = int(item.get("start_ms", 0))
        end_ms = int(item.get("end_ms", start_ms))
    except (TypeError, ValueError):
        return None
    confidence = item.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None
    raw = _mapping(item.get("raw"))
    raw.update({key: value for key, value in item.items()
                if key not in _WORD_KEYS})
    speaker = item.get("speaker")
    return Word(
        index=int(item.get("index", 0) or 0),
        text=text,
        normalized=normalized,
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=confidence,
        speaker=None if speaker is None else str(speaker),
        raw=raw,
    )


def word_to_payload(word: Word) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "index": word.index,
        "text": word.text,
        "normalized": word.normalized or normalise_word(word.text),
        "start_ms": word.start_ms,
        "end_ms": word.end_ms,
    }
    if word.confidence is not None:
        payload["confidence"] = word.confidence
    if word.speaker is not None:
        payload["speaker"] = word.speaker
    if word.raw:
        payload["raw"] = word.raw
    return payload


def transcript_from_payload(data: dict[str, Any], *, source_id: str = "",
                            language: str = "en", backend: str = "",
                            model: str = "",
                            duration_ms: int | None = None) -> Transcript:
    if not isinstance(data, dict):
        raise ValueError("transcript JSON must be an object")
    if "transcript" in data and "words" not in data:
        raise ValueError(
            "legacy VoiceBase-wrapped transcript JSON is not supported; "
            "write NCAT native transcript JSON (top-level words)")
    schema = data.get("schema")
    if schema is not None and schema != SCHEMA:
        raise ValueError(
            f"unsupported transcript schema {schema!r}; expected {SCHEMA!r}")
    if not isinstance(data.get("words", []), list):
        raise ValueError("transcript words must be a list")
    segments = data.get("segments") or []
    chunks = data.get("chunks") or []
    if not isinstance(segments, list) or not isinstance(chunks, list):
        raise ValueError("transcript segments/chunks must be lists")

    words: list[Word] = []
    for item in data.get("words", []):
        word = word_from_payload(item)
        if word is not None:
            words.append(word)
    for index, word in enumerate(words):
        word.index = index
        if not word.normalized:
            word.normalized = normalise_word(word.text)

    text = normalise_text(str(data.get("text") or "")) or " ".join(
        word.text for word in words)
    stored_duration = data.get("duration_ms")
    if duration_ms is None and stored_duration is not None:
        try:
            duration_ms = int(stored_duration)
        except (TypeError, ValueError):
            duration_ms = None
    raw = _mapping(data.get("raw"))
    raw.update({key: value for key, value in data.items()
                if key not in _TRANSCRIPT_KEYS})
    return Transcript(
        source_id=str(data.get("source_id") or source_id),
        language=str(data.get("language") or language or "en"),
        backend=str(data.get("backend") or backend),
        model=str(data.get("model") or model),
        text=text,
        words=words,
        segments=segments,
        chunks=chunks,
        duration_ms=duration_ms,
        created_at=data.get("created_at"),
        metadata=_mapping(data.get("metadata")),
        raw=raw,
    )


def transcript_from_words(words: list[Word], *, source_id: str = "",
                          language: str = "en", backend: str = "",
                          model: str = "", text: str = "",
                          duration_ms: int | None = None,
                          segments: list[dict[str, Any]] | None = None,
                          chunks: list[dict[str, Any]] | None = None,
                          metadata: dict[str, Any] | None = None,
                          raw: dict[str, Any] | None = None) -> Transcript:
    ordered = sorted(words, key=lambda word: (word.start_ms, word.index))
    for index, word in enumerate(ordered):
        word.index = index
        if not word.normalized:
            word.normalized = normalise_word(word.text)
    return Transcript(
        source_id=source_id,
        language=language,
        backend=backend,
        model=model,
        text=normalise_text(text) or " ".join(word.text for word in ordered),
        words=ordered,
        segments=list(segments or []),
        chunks=list(chunks or []),
        duration_ms=duration_ms,
        metadata=dict(metadata or {}),
        raw=dict(raw or {}),
    )


def transcript_to_payload(transcript: Transcript) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source_id": transcript.source_id,
        "language": transcript.language,
        "backend": transcript.backend,
        "model": transcript.model,
        "text": transcript.text,
        "words": [word_to_payload(word) for word in transcript.words],
    }
    if transcript.segments:
        payload["segments"] = transcript.segments
    if transcript.chunks:
        payload["chunks"] = transcript.chunks
    if transcript.duration_ms is not None:
        payload["duration_ms"] = transcript.duration_ms
    if transcript.created_at:
        payload["created_at"] = transcript.created_at
    if transcript.metadata:
        payload["metadata"] = transcript.metadata
    if transcript.raw:
        payload["raw"] = transcript.raw
    return payload


def apply_offset(transcript: Transcript, offset_ms: int) -> Transcript:
    """Shift every word by a configurable global offset, never silently."""
    offset_ms = int(offset_ms)
    if offset_ms == 0:
        return transcript
    for word in transcript.words:
        word.start_ms += offset_ms
        word.end_ms += offset_ms
    if transcript.duration_ms is not None:
        transcript.duration_ms += offset_ms
    return transcript


def clamp_word_times(transcript: Transcript) -> Transcript:
    """Keep word times non-negative and non-decreasing without reordering."""
    previous_start = 0
    for word in transcript.words:
        word.start_ms = max(previous_start, max(0, int(word.start_ms)))
        word.end_ms = max(word.start_ms, int(word.end_ms))
        previous_start = word.start_ms
    return transcript


def renumber_words(transcript: Transcript) -> Transcript:
    for index, word in enumerate(transcript.words):
        word.index = index
        if not word.normalized:
            word.normalized = normalise_word(word.text)
    return transcript


def chunk_transcript(transcript: Transcript, chunk_ms: int, *,
                     max_gap_ms: int = 1500) -> Transcript:
    """Group words into optional chunks for display/indexing.

    ``chunk_ms <= 0`` leaves ``chunks`` empty. A new chunk starts on a large
    pause or when adding the next word would exceed the requested span.
    """
    transcript.chunks = []
    if chunk_ms <= 0 or not transcript.words:
        return transcript
    chunks: list[dict[str, Any]] = []
    first = 0
    chunk_start = transcript.words[0].start_ms
    previous_end = transcript.words[0].end_ms
    for index, word in enumerate(transcript.words[1:], start=1):
        large_gap = word.start_ms - previous_end >= max_gap_ms
        too_long = word.end_ms - chunk_start > chunk_ms
        if large_gap or too_long:
            chunks.append(_chunk_payload(
                transcript.words[first:index], len(chunks)))
            first = index
            chunk_start = word.start_ms
        previous_end = word.end_ms
    chunks.append(_chunk_payload(transcript.words[first:], len(chunks)))
    transcript.chunks = chunks
    return transcript


def _chunk_payload(words: list[Word], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "start_ms": words[0].start_ms,
        "end_ms": words[-1].end_ms,
        "text": " ".join(word.text for word in words if word.text),
        "first_word": words[0].index,
        "last_word": words[-1].index,
        "word_count": len(words),
    }


def finalize_transcript(transcript: Transcript, *, offset_ms: int = 0,
                        chunk_ms: int = 0,
                        max_gap_ms: int = 1500) -> Transcript:
    """Apply configurable timing cleanup and indexing after a backend run."""
    apply_offset(transcript, offset_ms)
    clamp_word_times(transcript)
    renumber_words(transcript)
    chunk_transcript(transcript, chunk_ms, max_gap_ms=max_gap_ms)
    transcript.text = normalise_text(transcript.text) or " ".join(
        word.text for word in transcript.words)
    if transcript.words:
        last_end = transcript.words[-1].end_ms
        transcript.duration_ms = max(transcript.duration_ms or 0, last_end)
    if not transcript.created_at:
        transcript.created_at = datetime.now(timezone.utc).isoformat()
    return transcript


def _atomic_write_text(path: str | Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return path


def save_transcript(path: str | Path, transcript: Transcript) -> Path:
    return _atomic_write_text(
        path,
        json.dumps(transcript_to_payload(transcript), ensure_ascii=False,
                   indent=2) + "\n",
    )


def load_transcript(path: str | Path, *, source_id: str = "",
                    language: str = "en") -> Transcript:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return transcript_from_payload(
        payload, source_id=source_id, language=language,
    )


def write_transcript_text(path: str | Path, transcript: Transcript) -> Path:
    return _atomic_write_text(path, transcript.text + "\n")
