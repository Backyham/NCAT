"""Abstract base for MT backends.

A backend is one inference engine wrapped around a specific model file
format. Two implementations live in this package:

  - LlamaCppBackend : GGUF Q4/Q5/Q6/Q8, runs on any GPU
  - VLLMBackend     : HF safetensors (FP8/BF16), fast on modern GPUs
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from ..config import ModelEntry


# Standard MT prompt. Override per-backend if model uses a different
# chat template (e.g. HY-MT may want a specific Tencent format).
MT_PROMPT = (
    'Translate the following text from {source_lang} to {target_lang}.\n'
    'Output only the translation, no commentary.\n\n'
    '{source_lang}: {text}\n'
    '{target_lang}:'
)


def build_mt_prompt(text: str,
                    source_lang: str = 'en',
                    target_lang: str = 'zh',
                    entry: ModelEntry | None = None) -> str:
    """Build a translation prompt. entry may override defaults."""
    src = (entry.default_source_lang if entry else source_lang) or 'en'
    tgt = (entry.default_target_lang if entry else target_lang) or 'zh'
    return MT_PROMPT.format(source_lang=src,
                            target_lang=tgt,
                            text=text.strip())


def probe_vram_bytes() -> int | None:
    """Return total VRAM in bytes, or None if no CUDA / probe failed."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        return torch.cuda.get_device_properties(0).total_memory
    except Exception:
        return None


def human_bytes(n: int | None) -> str:
    if n is None:
        return 'unknown'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} PB'


class Backend(ABC):
    """Base class for MT inference backends."""

    def __init__(self, entry: ModelEntry):
        self.entry = entry
        self._loaded = False
        self._model: Any = None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def label(self) -> str:
        return f'{self.__class__.__name__}({self.entry.name})'

    def load(self) -> None:
        if not self.entry.exists():
            raise FileNotFoundError(
                f'Model file/dir not found: {self.entry.path}\n'
                f'Edit mt_serve/config.py and fix the path.'
            )
        vram = probe_vram_bytes()
        if vram is not None:
            print(f'[{self.label}] Detected VRAM: {human_bytes(vram)}')
        t0 = time.time()
        self._do_load()
        print(f'[{self.label}] Loaded in {time.time() - t0:.1f}s')
        self._loaded = True

    def unload(self) -> None:
        self._do_unload()
        self._loaded = False

    @abstractmethod
    def _do_load(self) -> None:
        ...

    @abstractmethod
    def _do_unload(self) -> None:
        ...

    @abstractmethod
    def translate(self,
                 text: str,
                 source_lang: str | None = None,
                 target_lang: str | None = None,
                 max_new_tokens: int = 256) -> str:
        """Run a single translation. Return only the translated text."""
        ...

    @abstractmethod
    def complete(self,
                en_context: str,
                zh_partial: str,
                max_new_tokens: int = 30) -> str:
        """Auto-complete a partial Chinese translation given English context.

        Returns the continuation only (no echo of the prefix).
        """
        ...
