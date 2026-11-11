"""Unified ASR contract shared by local, subprocess, and cloud backends.

There is intentionally no preset backend and no universal invocation config:
NeMo Parakeet, a whisper.cpp binary, and an HTTP API need different code. The
unified part is the data/contract boundary below, so the CLI, transcript
writer, and later alignment stages do not care which implementation ran.
Implementations are registered as custom backends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..types import Transcript


class ASRError(RuntimeError):
    """An ASR backend could not run or returned unusable output."""


@dataclass
class ASRCapabilities:
    word_timestamps: bool = True
    confidence: bool = True
    language_detection: bool = False
    diarization: bool = False
    vad: bool = True
    local: bool = True
    max_duration_s: int | None = None
    notes: str = ""


@dataclass
class ASRRequest:
    audio_path: str
    language: str | None = None
    # Backend-specific settings: model id/path, device, endpoint, API key
    # reference, timeout, prompt, etc. The core never interprets these keys.
    options: dict[str, Any] = field(default_factory=dict)


class ASRBackend(Protocol):
    name: str
    capabilities: ASRCapabilities

    def transcribe(self, request: ASRRequest) -> Transcript:
        """Return words in absolute milliseconds with native fields preserved."""
        ...
