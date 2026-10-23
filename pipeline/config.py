"""Model paths and runtime settings.

Resolution order, first hit wins:

1. environment variable (``NCAT_ASR_MODEL``, ...)
2. ``[tool.ncat]`` in ``pyproject.toml``
3. the built-in default (for the ASR pair, a HuggingFace repo id, so a
   fresh checkout can pull from the Hub without configuring anything)

Nothing here carries a machine-specific path. This module lives in
``pipeline/`` rather than ``backend/`` because the stages that actually load
models must be able to read it, and ``pipeline`` must not depend on
``backend``. ``backend.config`` re-exports for its own callers.
"""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# pyproject key -> environment variable
MODEL_KEYS: dict[str, str] = {
    'models_root': 'NCAT_MODELS_ROOT',
    'asr_model': 'NCAT_ASR_MODEL',
    'aligner_model': 'NCAT_ALIGNER_MODEL',
    'mt_model': 'NCAT_MT_MODEL',
    'llm_model': 'NCAT_LLM_MODEL',
}

SETTING_KEYS: dict[str, str] = {
    'data_dir': 'NCAT_DATA_DIR',
    'ffmpeg': 'NCAT_FFMPEG',
}

SETTING_DEFAULTS: dict[str, str] = {
    # Overridden to a private build when the system ffmpeg cannot use the
    # GPU encoder. See encoders.py.
    'ffmpeg': 'ffmpeg',
}

# Used only when neither the environment nor pyproject says anything. A repo
# id resolves through the Hub; a local path does not touch the network.
MODEL_DEFAULTS: dict[str, str] = {
    'asr_model': 'Qwen/Qwen3-ASR-1.7B-hf',
    'aligner_model': 'Qwen/Qwen3-ForcedAligner-0.6B-hf',
    'mt_model': '',
    'llm_model': '',
    'models_root': '',
}

# Common names for each model, so a repository checkout that has converted
# or copied weights into models/ works with no configuration at all.
_LOCAL_NAMES: dict[str, tuple[str, ...]] = {
    'asr_model': ('Qwen3-ASR-1.7B-hf', 'Qwen3-ASR-1.7B'),
    'aligner_model': ('Qwen3-ForcedAligner-0.6B-hf', 'Qwen3-ForcedAligner-0.6B'),
    'mt_model': ('Index-Translate-9B-F16', 'HY-MT2-7B-F16'),
    'llm_model': (),
}


def _discover_in_models_dir(key: str) -> str:
    """Find a model by directory name under ``models/``.

    Keeps a fresh checkout working without environment variables: the model
    weights live next to the code, so the code should look there before
    falling back to a Hub id that would need the network.
    """
    names = _LOCAL_NAMES.get(key)
    if not names:
        return ''
    models_dir = BASE_DIR / 'models'
    if not models_dir.is_dir():
        return ''
    lowered = {name.lower(): name for name in names}
    for entry in sorted(models_dir.iterdir()):
        if entry.is_dir() and entry.name.lower() in lowered:
            return str(entry)
    return ''


@lru_cache(maxsize=1)
def _pyproject_config() -> dict[str, str]:
    pyproject = BASE_DIR / 'pyproject.toml'
    if not pyproject.is_file():
        return {}
    with pyproject.open('rb') as fh:
        section = tomllib.load(fh).get('tool', {}).get('ncat', {}) or {}
    return {k: str(v) for k, v in section.items() if v}


def model_path(key: str) -> str:
    """Resolve one model location.

    An empty result means "not configured", which callers surface rather
    than guess at -- a missing model should be visible before a run starts,
    not minutes into it.
    """
    env_name = MODEL_KEYS.get(key)
    if env_name:
        value = os.environ.get(env_name, '').strip()
        if value:
            return value
    configured = _pyproject_config().get(key, '')
    if configured:
        return configured
    default = MODEL_DEFAULTS.get(key, '')
    # Local repo checkout wins over the Hub id so a fresh install works
    # without a network or environment configuration.
    local = _discover_in_models_dir(key)
    if local:
        return local
    return default


def is_local(path: str) -> bool:
    """True when the path is a filesystem location rather than a Hub id."""
    if not path:
        return False
    return '/' in path or path.endswith(('.gguf', '.safetensors', '.nemo'))


def model_status() -> dict[str, dict[str, object]]:
    """Every model key with its resolved path and local existence."""
    out: dict[str, dict[str, object]] = {}
    for key in MODEL_KEYS:
        if key == 'models_root':
            continue
        path = model_path(key)
        present = (Path(path).exists() if is_local(path)
                   else bool(path))     # a Hub id counts as available
        out[key] = {'path': path, 'local': is_local(path),
                    'present': bool(present)}
    return out


def setting(key: str) -> str:
    """Non-model scalar setting (data directory, ffmpeg binary, ...)."""
    env_name = SETTING_KEYS.get(key)
    if env_name:
        value = os.environ.get(env_name, '').strip()
        if value:
            return value
    return SETTING_DEFAULTS.get(key, '')