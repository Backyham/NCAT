"""Transcript normalisation and VoiceBase-compatible JSON I/O."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .text import normalise_text
from .types import Transcript, Word

_PUNCT = re.compile(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff']+")


def normalise_word(text: str) -> str:
    return _PUNCT.sub("", normalise_text(text).casefold())


def word_from_voicebase(item: dict[str, Any]) -> Word | None:
    text = str(item.get("w", item.get("word", "")) or "")
    normalized = normalise_word(text)
    if not normalized:
        return None
    try:
        start_ms = int(item.get("s", item.get("start_ms", 0)))
        end_ms = int(item.get("e", item.get("end_ms", start_ms)))
    except (TypeError, ValueError):
        return None
    return Word(
        index=int(item.get("p", item.get("index", 0)) or 0),
        text=text,
        normalized=normalized,
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=item.get("confidence"),
        speaker=item.get("speaker"),
    )


def _voicebase_words(data: dict[str, Any]) -> list[dict[str, Any]]:
    if "transcript" in data:
        return list(data["transcript"].get("words") or [])
    media = data.get("media", {})
    transcript = media.get("transcripts", {}).get("latest", {})
    return list(transcript.get("words") or [])


def transcript_from_voicebase(data: dict[str, Any], *, source_id: str = "",
                              language: str = "en", backend: str = "",
                              model: str = "",
                              duration_ms: int | None = None) -> Transcript:
    words: list[Word] = []
    for index, item in enumerate(_voicebase_words(data)):
        item.setdefault("p", index)
        word = word_from_voicebase(item)
        if word is not None:
            word.index = len(words)
            words.append(word)
    text = ""
    if "transcript" in data:
        text = str(data["transcript"].get("text") or "")
    text = normalise_text(text) or " ".join(word.text for word in words)
    return Transcript(
        source_id=source_id,
        language=language,
        backend=backend,
        model=model,
        text=text,
        words=words,
        duration_ms=duration_ms,
    )


def transcript_from_words(words: list[Word], *, source_id: str = "",
                          language: str = "en", backend: str = "",
                          model: str = "", text: str = "",
                          duration_ms: int | None = None) -> Transcript:
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
        duration_ms=duration_ms,
    )


def transcript_to_voicebase(transcript: Transcript) -> dict[str, Any]:
    return {
        "transcript": {
            "text": transcript.text,
            "language": transcript.language,
            "backend": transcript.backend,
            "model": transcript.model,
            "words": [
                {
                    "p": word.index,
                    "s": word.start_ms,
                    "e": word.end_ms,
                    "w": word.text,
                    **({"confidence": word.confidence}
                       if word.confidence is not None else {}),
                }
                for word in transcript.words
            ],
        }
    }


def save_transcript(path: str | Path, transcript: Transcript) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(transcript_to_voicebase(transcript), ensure_ascii=False,
                   indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_transcript(path: str | Path, *, source_id: str = "",
                    language: str = "en") -> Transcript:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return transcript_from_voicebase(
        payload, source_id=source_id, language=language,
        backend=payload.get("transcript", {}).get("backend", ""),
        model=payload.get("transcript", {}).get("model", ""),
    )


def write_transcript_text(path: str | Path, transcript: Transcript) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript.text + "\n", encoding="utf-8")
    return path
