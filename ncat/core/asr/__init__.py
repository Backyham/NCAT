"""ASR backend registry."""
from __future__ import annotations

from .base import ASRBackend, ASRCapabilities, ASRError, ASRRequest
from .qwen3 import Qwen3Backend
from .stable_ts import StableTSBackend
from .whisper import WhisperBackend
from .whisper_cpp import WhisperCppBackend

_BACKENDS: dict[str, type] = {
    "whisper": WhisperBackend,
    "whisper_cpp": WhisperCppBackend,
    "stable_ts": StableTSBackend,
    "qwen3": Qwen3Backend,
}


def get_backend(name: str) -> ASRBackend:
    try:
        return _BACKENDS[name]()
    except KeyError as error:
        raise ASRError(
            f"unknown ASR backend {name!r}; choose from "
            f"{', '.join(sorted(_BACKENDS))}"
        ) from error


def available_backends() -> list[str]:
    return sorted(_BACKENDS)


__all__ = [
    "ASRBackend", "ASRCapabilities", "ASRError", "ASRRequest",
    "Qwen3Backend", "StableTSBackend", "WhisperBackend",
    "WhisperCppBackend", "get_backend", "available_backends",
]
