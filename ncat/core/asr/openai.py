"""OpenAI-compatible ASR backend using the shared Bearer API-key client."""
from __future__ import annotations

import json

from ..api import ApiError, api_endpoint, multipart_body, request_json, resolve_api_key
from ..transcript import transcript_from_words
from ..types import Transcript, Word
from .base import ASRCapabilities, ASRError, ASRRequest


class OpenAICompatibleASRBackend:
    name = "openai_compatible"
    capabilities = ASRCapabilities(
        word_timestamps=True, confidence=True, local=False,
        notes="OpenAI-compatible /v1/audio/transcriptions; API key via "
              "NCAT_API_KEY / OPENAI_API_KEY",
    )

    def transcribe(self, request: ASRRequest) -> Transcript:
        options = request.options
        base_url = str(options.get("base_url") or options.get("endpoint") or "")
        if not base_url:
            raise ASRError("openai ASR backend requires base_url or endpoint")
        model = str(options.get("model") or "")
        language = str(request.language or options.get("language") or "")
        fields = {"response_format": "verbose_json"}
        if model:
            fields["model"] = model
        if language:
            fields["language"] = language
        fields["timestamp_granularities[]"] = "word"
        if options.get("backend"):
            fields["backend"] = str(options["backend"])
            local_options = {
                key: value for key, value in options.items()
                if key not in {
                    "base_url", "endpoint", "api_key", "timeout", "retries",
                    "retry_backoff", "extra_headers", "source_id",
                }
            }
            fields["options"] = json.dumps(
                local_options, ensure_ascii=False)
        if options.get("source_id"):
            fields["source_id"] = str(options["source_id"])
        try:
            with open(request.audio_path, "rb") as handle:
                audio = handle.read()
            body, content_type = multipart_body(
                fields, [("file", "audio.wav", audio, "audio/wav")])
            endpoint = api_endpoint(base_url, "/audio/transcriptions")
            payload = request_json(
                endpoint, data=body, headers={"Content-Type": content_type},
                api_key=resolve_api_key(
                    options.get("api_key"), provider="asr"),
                extra_headers=options.get("extra_headers"),
                timeout=float(options.get("timeout", 600)),
                retries=int(options.get("retries", 2)),
                retry_backoff=float(options.get("retry_backoff", 1.0)),
            )
        except (OSError, ApiError) as exc:
            raise ASRError(f"openai ASR failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise ASRError("openai ASR returned a non-object response")
        words: list[Word] = []
        for index, item in enumerate(payload.get("words") or []):
            if not isinstance(item, dict):
                continue
            try:
                start_ms = (int(item["start_ms"]) if "start_ms" in item
                            else int(round(float(item.get("start", 0)) * 1000)))
                end_ms = (int(item["end_ms"]) if "end_ms" in item
                          else int(round(float(item.get("end", 0)) * 1000)))
            except (TypeError, ValueError):
                continue
            confidence = item.get("confidence")
            words.append(Word(
                index=index,
                text=str(item.get("text") or item.get("word") or ""),
                start_ms=start_ms, end_ms=end_ms,
                confidence=None if confidence is None else float(confidence),
                raw=item,
            ))
        if not words:
            raise ASRError(
                "openai ASR returned no word timestamps; use a provider or "
                "local server that supports word timestamps")
        return transcript_from_words(
            words,
            source_id=str(options.get("source_id", "")),
            language=str(payload.get("language") or language or "en"),
            backend=self.name,
            model=str(payload.get("model") or model),
            text=str(payload.get("text") or ""),
            duration_ms=(int(payload["duration_ms"])
                         if payload.get("duration_ms") is not None else None),
            metadata={"cloud": True},
            raw={"openai_asr": payload},
        )


def make_backend() -> OpenAICompatibleASRBackend:
    return OpenAICompatibleASRBackend()
