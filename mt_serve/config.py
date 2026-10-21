"""
Model registry for mt_serve.

Each entry describes one model file/dir + the backend that loads it.

Paths are per-machine, so nothing here is hardcoded. Each entry resolves
in this order:

    1. the matching environment variable (NCAT_MT_<NAME>_PATH)
    2. a local .gguf beside this file, named <name>.gguf
    3. empty -> the entry is listed but not loadable

Register your models by setting the environment variable, or by dropping the
GGUF next to this file. `GET /models` reports which paths actually exist.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Format codes used in the registry.
Format = Literal['gguf', 'safetensors']          # safetensors = HF FP8/BF16
Backend = Literal['llama_cpp', 'vllm']

_HERE = Path(__file__).resolve().parent


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

    def resolved_path(self) -> str:
        env_name = f'NCAT_MT_{self.name.upper().replace("-", "_")}_PATH'
        value = os.environ.get(env_name, '').strip()
        if value:
            return value
        local = _HERE / f'{self.name}.gguf'
        if self.format == 'gguf' and local.is_file():
            return str(local)
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

    'index-translate-9b-q8': ModelEntry(
        name='index-translate-9b-q8',
        format='gguf',
        backend='llama_cpp',
        description='Index-Translate 9B Q8. ~9.8 GB; all layers fit on a '
                    '24 GB card. Candidate A of two.',
        n_ctx=8192,
        n_gpu_layers=-1,
    ),

    'hy-mt2-7b-q8': ModelEntry(
        name='hy-mt2-7b-q8',
        format='gguf',
        backend='llama_cpp',
        description='Tencent Hy-MT2 7B Q8. ~8.0 GB. Candidate B of two. '
                    'Uses its own chat template, not the Qwen3 one.',
        n_ctx=8192,
        n_gpu_layers=-1,
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
