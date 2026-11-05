"""stable-ts enhanced Whisper backend."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from ..transcript import transcript_from_words, word_from_voicebase
from ..types import Transcript, Word
from .base import ASRBackend, ASRCapabilities, ASRRequest, ASRError

_MODEL_CACHE: dict[str, Any] = {}


class StableTSBackend(ASRBackend):
    name = "stable_ts"
    capabilities = ASRCapabilities(
        word_timestamps=True, confidence=False, language_detection=True,
        local=True,
    )

    def transcribe(self, request: ASRRequest) -> Transcript:
        try:
            import stable_whisper  # type: ignore
        except ImportError as error:
            raise ASRError(
                "stable-whisper is not installed in this interpreter; use the "
                "ASR environment"
            ) from error
        options = dict(request.options)
        model_name = str(options.pop("model", "large-v3"))
        source_id = str(options.pop("source_id", ""))
        language = request.language or str(options.pop("language", "en"))
        precision = float(options.pop("precision", 0.02))
        if model_name not in _MODEL_CACHE:
            _MODEL_CACHE[model_name] = stable_whisper.load_model(model_name)
        model = _MODEL_CACHE[model_name]
        result = model.transcribe(
            request.audio_path,
            word_timestamps=True,
            language=language,
            vad=True,
            **options,
        )
        result = model.refine(request.audio_path, result, precision=precision)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stable-ts.json"
            result.save_as_json(str(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
        words: list[Word] = []
        texts: list[str] = []
        for segment in payload.get("segments", []):
            texts.append(str(segment.get("text", "")))
            for item in segment.get("words", []):
                word = word_from_voicebase({
                    "p": len(words),
                    "s": int(float(item["start"]) * 1000),
                    "e": int(float(item["end"]) * 1000),
                    "w": item.get("word", ""),
                })
                if word is not None:
                    word.index = len(words)
                    words.append(word)
        return transcript_from_words(
            words,
            source_id=source_id,
            language=language,
            backend=self.name,
            model=model_name,
            text=" ".join(part.strip() for part in texts if part.strip()),
            duration_ms=words[-1].end_ms if words else None,
        )
