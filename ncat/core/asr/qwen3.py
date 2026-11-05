"""Qwen3-ASR backend placeholder.

The real backend runs in the separate ASR environment. Keeping the class here
makes the CLI/factory stable while the worker protocol is implemented.
"""
from __future__ import annotations

from ..types import Transcript
from .base import ASRBackend, ASRCapabilities, ASRRequest, ASRError


class Qwen3Backend(ASRBackend):
    name = "qwen3"
    capabilities = ASRCapabilities(
        word_timestamps=True, confidence=False, language_detection=True,
        local=True,
    )

    def transcribe(self, request: ASRRequest) -> Transcript:
        raise ASRError(
            "Qwen3 ASR backend is not implemented yet; Phase 1.x will add the "
            ".venv-asr worker. Use --backend whisper for now."
        )
