"""OpenAI Whisper backend (lazy import; runs in the ASR environment)."""
from __future__ import annotations

from typing import Any

from ..transcript import transcript_from_voicebase
from ..types import Transcript
from .base import ASRBackend, ASRCapabilities, ASRRequest, ASRError

_MODEL_CACHE: dict[str, Any] = {}


class WhisperBackend(ASRBackend):
    name = "whisper"
    capabilities = ASRCapabilities(
        word_timestamps=True, confidence=False, language_detection=True,
        local=True,
    )

    def transcribe(self, request: ASRRequest) -> Transcript:
        try:
            import whisper  # type: ignore
        except ImportError as error:
            raise ASRError(
                "whisper is not installed in this interpreter; use the ASR "
                "environment or install openai-whisper"
            ) from error

        options = dict(request.options)
        model_name = str(options.pop("model", "large-v3"))
        source_id = str(options.pop("source_id", ""))
        options.pop("duration_ms", None)
        language = request.language or str(options.pop("language", "en"))
        if model_name not in _MODEL_CACHE:
            _MODEL_CACHE[model_name] = whisper.load_model(model_name)
        model = _MODEL_CACHE[model_name]
        result = model.transcribe(
            request.audio_path,
            word_timestamps=True,
            language=language,
            **options,
        )
        words = []
        for segment in result.get("segments", []):
            for word in segment.get("words", []):
                words.append({
                    "p": len(words),
                    "s": int(word["start"] * 1000),
                    "e": int(word["end"] * 1000),
                    "w": word.get("word", ""),
                })
        transcript = transcript_from_voicebase(
            {"transcript": {"text": result.get("text", ""), "words": words}},
            source_id=source_id,
            language=language,
            backend=self.name,
            model=model_name,
            duration_ms=words[-1]["e"] if words else None,
        )
        return transcript
