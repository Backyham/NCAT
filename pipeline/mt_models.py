"""Registry of MT models available to the pipeline.

Each entry names one model, how to load it, and how to prompt it. Nothing
carries a machine-specific path: ``resolved_path()`` consults the
environment first, then ``models/`` in the repository.

The candidates are not a primary and a fallback. Both are still under
evaluation and exactly one is expected to survive, so no entry is treated
as a default and the caller must name one.

Two per-model settings are not interchangeable:

* ``extra_args``    -- the qwen35 hybrid needs ``--reasoning off`` or it
                       emits a several-hundred-token monologue that buries
                       the translation
* ``prompt_template`` -- Hy-MT2 uses its own template
                       (``<|startoftext|>...<|extra_0|>``); raw text makes
                       it echo the prompt and answer in English
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Format codes used in the registry.
Format = Literal['gguf', 'safetensors']          # safetensors = HF FP8/BF16
Backend = Literal['llama_cpp', 'vllm']

_HERE = Path(__file__).resolve().parent
# Repository models/ directory, where converted GGUF files land.
_MODELS_DIR = _HERE.parent / 'models'


@dataclass
class ModelEntry:
    name: str
    format: Format
    backend: Backend
    description: str = ''
    path: str = ''

    # Context window (tokens).
    n_ctx: int = 8192

    # llama-cpp-only: how many layers to put on GPU. -1 = all.
    # Lower this if the model does not fit in VRAM (rest goes to CPU).
    n_gpu_layers: int = -1

    # vllm-only: fraction of VRAM to allocate, and whether to offload.
    gpu_memory_utilization: float = 0.85
    enable_cpu_offload: bool = False
    enforce_eager: bool = False

    # Source/target language codes.
    default_source_lang: str = 'en'
    default_target_lang: str = 'zh'

    # llama-cli flags this model needs. Two are not optional:
    #   - 'chat-template-hy-mt2'  Hy-MT2 uses its own template, not Qwen3's
    #   - '--reasoning off'     the qwen35 hybrid emits a long "Start thinking"
    #                            monologue that buries the translation and
    #                            wastes hundreds of tokens per line
    extra_args: list = field(default_factory=list)

    # How one source segment is turned into a prompt. These are NOT
    # interchangeable: sending raw text to Hy-MT2 makes it echo the prompt
    # and answer in English instead of translating. '{text}' is the source
    # segment, '{lang}' the target language.
    prompt_template: str = (
        'Translate the following sentence into {lang}.{newline}{text}'
    )

    def resolved_path(self) -> str:
        env_name = f'NCAT_MT_{self.name.upper().replace("-", "_")}_PATH'
        value = os.environ.get(env_name, '').strip()
        if value:
            return value
        if self.format == 'gguf':
            # Beside this file, then the repository's models/ directory.
            # The latter is where converted models land by convention.
            for candidate in (_HERE / f'{self.name}.gguf',
                              _MODELS_DIR / f'{self.name}.gguf'):
                if candidate.is_file():
                    return str(candidate)
            # Converted files keep the upstream model's own spelling
            # ("Index-Translate-9B-F16.gguf"), which differs from the
            # registry key in case as well as punctuation, so match
            # case-insensitively on the alphanumeric part of the name.
            wanted = re.sub(r'[^a-z0-9]', '', self.name.lower())
            if _MODELS_DIR.is_dir():
                for candidate in sorted(_MODELS_DIR.glob('*.gguf')):
                    stem = re.sub(r'[^a-z0-9]', '',
                                  candidate.stem.lower())
                    # Accept the registry key with or without the -f16 tag.
                    if stem.startswith(wanted.removesuffix('f16')) or \
                            stem.startswith(wanted):
                        return str(candidate)
        return self.path

    def exists(self) -> bool:
        path = self.resolved_path()
        if not path:
            return False
        p = Path(path)
        if self.format == 'gguf':
            return p.is_file() and p.suffix == '.gguf'
        return p.is_dir()

    def __repr__(self) -> str:
        ok = 'OK' if self.exists() else 'MISSING'
        return f'<ModelEntry {self.name} [{ok}] {self.format} {self.resolved_path()}>'


# -----------------------------------------------------------------------------
# REGISTRY
#
# The two entries below are MT *candidates*, not a primary and a fallback:
# neither has been chosen, and one is expected to be removed after they are
# compared. Throughput has been measured but translation quality has not, so
# neither is presented as the default.
#
# Only llama_cpp is wired up; vllm is declared for completeness but cannot run
# on Pascal (sm_61) GPUs.
# -----------------------------------------------------------------------------

REGISTRY: dict[str, ModelEntry] = {

    'index-translate-9b-f16': ModelEntry(
        name='index-translate-9b-f16',
        format='gguf',
        backend='llama_cpp',
        description='Index-Translate 9B, full-precision (bf16) GGUF, 17.1 GB. '
                    'Candidate A of two. Note: qwen35 hybrid architecture -- '
                    'most layers are SSM/linear-attention, which are slower '
                    'than plain attention on older GPUs.',
        n_ctx=8192,
        n_gpu_layers=-1,
        extra_args=['--reasoning', 'off'],
    ),
    # Index-Translate uses the default instruction template.

    'hy-mt2-7b-f16': ModelEntry(
        name='hy-mt2-7b-f16',
        format='gguf',
        backend='llama_cpp',
        description='Tencent Hy-MT2 7B, full-precision (bf16) GGUF, 14.0 GB. '
                    'Candidate B of two. Uses its own chat template '
                    '(<|startoftext|>...<|extra_0|>), not the Qwen3 one.',
        n_ctx=8192,
        n_gpu_layers=-1,
        extra_args=['--no-warmup'],
        # Hy-MT2's own template. Verified working; a plain-text prompt makes
        # the model echo it and reply in English.
        prompt_template='<|startoftext|>Translate into {lang}: {text}<|extra_0|>',
    ),
}


def get(name: str) -> ModelEntry:
    if name not in REGISTRY:
        raise KeyError(
            f'Unknown model {name!r}. '
            f'Available: {", ".join(sorted(REGISTRY))}'
        )
    return REGISTRY[name]


def list_names() -> list[str]:
    return sorted(REGISTRY)


def list_all() -> list[ModelEntry]:
    return [REGISTRY[n] for n in list_names()]


def available() -> list[ModelEntry]:
    """Return only models whose path actually resolves to something real."""
    return [e for e in list_all() if e.exists()]
